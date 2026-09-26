"""Camoufox-backed driver for chrome_manager pools.

Camoufox wraps Playwright's *sync* API, which is greenlet-bound: every object
must be used from the thread that created it. chrome_manager builds drivers on
warmer threads and serves them to cheroot request threads, so each
CamoufoxDriver owns a dedicated daemon thread that creates the browser and
executes every page operation; the public methods marshal callables onto that
thread and wait for the result. The pool's lease semantics guarantee a single
caller at a time, so a plain job queue is enough.

Launch settings mirror the hand-tuned camoufox_test.py probe: virtual display
(camoufox manages its own Xvfb — no xvfb-run wrapper needed), geoip-derived
timezone/locale/geolocation from the proxy exit IP, humanized cursor, en-US
locale, image blocking, 30s default page timeout.

A wedged page op (call() timeout) leaves the owner thread stuck inside
Playwright, so the clean shutdown path (sentinel -> cm.__exit__) can never run
and the Firefox tree (~1GB+) would outlive the driver. close() therefore
force-kills the driver's OS processes, recorded by pid-diff around the launch,
and kill_orphan_browsers() lets the pool janitor reap any tree that still
slips through.
"""
import json
import queue
import sys
import threading
from urllib.parse import urlparse

import psutil
from camoufox import DefaultAddons
from camoufox.sync_api import Camoufox

# Camoufox bundles uBlock Origin ENABLED by default, and a fresh profile
# re-downloads its filter lists (ublockorigin.pages.dev, cdn.jsdelivr.net,
# github.io, pgl.yoyo.org, publicsuffix.org, ~30 MB) on every launch — through
# the driver's proxy — tens of MB per browser launch, paid for on a metered
# residential proxy. Every driver here is a throwaway profile, so the addon
# never pays for itself: exclude it unconditionally.
EXCLUDE_ADDONS = [DefaultAddons.UBO]

# Camoufox's managed Xvfb ("virtual") is Linux-only; run headed elsewhere
# (local macOS development).
HEADLESS = "virtual" if sys.platform.startswith("linux") else False

STARTUP_TIMEOUT = 120    # browser launch + first page
CALL_TIMEOUT = 90        # hard ceiling per marshalled op (page default is 30s)
GOTO_TIMEOUT_MS = 40000  # residential exits are slow; don't wait for full load
FETCH_TIMEOUT_MS = 20000  # per in-page fetch (AbortSignal), well under CALL_TIMEOUT

# In-page fetch run from the loaded page, so it inherits the page's cookies,
# x5sec clearance and TLS fingerprint. Never throws across the evaluate bridge:
# returns a structured object so the Python side gets a clean error taxonomy.
_FETCH_JS = """async ({url, method, headers, body, timeoutMs}) => {
    try {
        const r = await fetch(url, {
            method: method || 'GET',
            credentials: 'include',
            headers: headers || {},
            body: body || undefined,
            signal: AbortSignal.timeout(timeoutMs),
        });
        return { ok: true, status: r.status, body: await r.text() };
    } catch (e) {
        return { ok: false, status: 0, body: '', error: String(e) };
    }
}"""

DEFAULT_FETCH_HEADERS = {"accept-language": "en-US,en;q=0.5"}

# Live (unclosed) drivers, so kill_orphan_browsers() knows which OS processes
# are legitimately owned. Guarded by _drivers_lock.
_drivers = set()
_drivers_lock = threading.Lock()

# Process names identifying a camoufox browser tree (Linux binary / macOS app
# binary). Used only to decide whether an UNOWNED process tree may be killed —
# botasaurus Chrome trees and its Xvfb never match.
_CAMOUFOX_NAMES = {"camoufox-bin", "camoufox", "Camoufox"}


def _children_since(pre_launch_pids):
    """Direct children of this process spawned since the snapshot — the OS
    processes of the driver being launched (playwright node driver + Xvfb;
    camoufox-bin and the Firefox children live under the node driver).
    multiprocessing helpers (the resource_tracker appears on first use) are
    process-wide, not the driver's — never attribute those."""
    kids = []
    for p in psutil.Process().children():
        if p.pid in pre_launch_pids:
            continue
        try:
            if "resource_tracker" in " ".join(p.cmdline()):
                continue
        except psutil.Error:
            pass
        kids.append(p)
    return kids


def _kill_tree(root):
    """SIGKILL a process and all its descendants. psutil checks creation time
    before signalling, so a recycled pid is a no-op, not a stray kill."""
    try:
        procs = [root] + root.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass


def kill_orphan_browsers():
    """Janitor safety net: SIGKILL every camoufox browser tree no live driver
    owns. MUST be called with chrome_manager's _create_lock held so no launch
    is mid-flight — otherwise a half-launched browser has processes no driver
    owns yet and would be reaped as an orphan.

    Detection is by tree content: a direct child of this process whose tree
    contains a camoufox binary is a browser tree. An orphaned driver's Xvfb is
    a sibling of that tree and doesn't match — close() covers it; this sweep
    is the backstop for the ~1GB Firefox trees."""
    with _drivers_lock:
        owned = {p.pid for d in _drivers for p in d._procs}
    killed = 0
    for child in psutil.Process().children():
        if child.pid in owned:
            continue
        try:
            tree = [child] + child.children(recursive=True)
        except psutil.NoSuchProcess:
            continue
        names = set()
        for p in tree:
            try:
                names.add(p.name())
            except psutil.Error:
                pass
        if not names & _CAMOUFOX_NAMES:
            continue
        for p in tree:
            try:
                p.kill()
            except psutil.Error:
                pass
        killed += 1
        print(f"camoufox: killed orphaned browser tree "
              f"(root pid {child.pid}, {len(tree)} procs)")
    return killed


class FetchResponse:
    """Minimal requests-like result from an in-page fetch. NOT raised on
    HTTP/network errors — inspect `.ok`, `.status_code`, `.error`.

      ok           False only for transport failures (network error / abort
                   timeout / bridge miss), NOT for HTTP 4xx/5xx.
      status_code  HTTP status (0 when the fetch never completed).
      text         response body as text.
      error        transport error string when ok is False, else None.
    """

    def __init__(self, ok, status_code, text, error=None):
        self.ok = ok
        self.status_code = status_code
        self.text = text
        self.error = error

    def json(self):
        return json.loads(self.text)

    def __repr__(self):
        return f"<FetchResponse ok={self.ok} status={self.status_code} bytes={len(self.text)}>"


def _proxy_dict(proxy_url):
    """http://user:pass@host:port -> playwright proxy dict (None = direct)."""
    if not proxy_url:
        return None
    u = urlparse(proxy_url)
    return {
        "server": f"http://{u.hostname}:{u.port}",
        "username": u.username,
        "password": u.password,
    }


class CamoufoxDriver:
    def __init__(self, proxy_url, locale="en-US", humanize=True, block_images=True, addons=None, screen=None):
        """`addons`: paths of EXTRACTED Firefox extensions (a directory with a
        manifest.json each) to load next to the defaults — e.g. the CapSolver
        extension google_search/identity.py mints SERP identities with."""
        self._jobs = queue.Queue()
        self._started = threading.Event()
        self._start_error = None
        self._closed = False
        self._browser = None  # set by the owner thread; used only from marshalled closures
        # OS processes backing this browser, recorded by pid-diff around the
        # launch. Valid because chrome_manager serializes construction
        # (_create_lock): no other driver spawns processes concurrently.
        self._procs = []
        pre_launch = {p.pid for p in psutil.Process().children()}
        self._thread = threading.Thread(
            target=self._main,
            args=(_proxy_dict(proxy_url), locale, humanize, block_images, list(addons or []), screen),
            daemon=True, name="camoufox-driver")
        self._thread.start()
        started = self._started.wait(STARTUP_TIMEOUT)
        self._procs = _children_since(pre_launch)
        if not started or self._start_error is not None:
            # Hung or failed launch: the owner thread may be stuck inside
            # Camoufox(...) with processes already spawned, or its own
            # cm.__exit__ attempt may have failed — reap everything ourselves,
            # re-diffing for processes that appeared after the snapshot above.
            self.close()
            self._procs = _children_since(pre_launch)
            self._force_kill()
            if not started:
                raise RuntimeError(f"camoufox startup timed out after {STARTUP_TIMEOUT}s")
            raise RuntimeError(f"camoufox startup failed: {self._start_error}")
        with _drivers_lock:
            _drivers.add(self)

    # ---- owner thread ---------------------------------------------------
    def _main(self, proxy, locale, humanize, block_images, addons, screen=None):
        cm = None
        page = None
        try:
            cm = Camoufox(
                headless=HEADLESS,
                proxy=proxy,
                geoip=True,      # timezone/locale/geolocation from the exit IP
                humanize=humanize,   # camoufox's own human-like cursor movement
                locale=locale,
                block_images=block_images,
                exclude_addons=EXCLUDE_ADDONS,
                addons=addons,
                # browserforge Screen constraint; None = derived from the
                # real display (a small Windows VM console has no matching
                # fingerprints: "No headers based on this input can be generated")
                screen=screen,
            )
            browser = cm.__enter__()
            self._browser = browser
            page = browser.new_page()
            page.set_default_timeout(30000)
        except Exception as e:
            self._start_error = e
            self._started.set()
            if cm is not None:
                try:
                    cm.__exit__(None, None, None)
                except Exception:
                    pass
            return
        self._started.set()
        while True:
            job = self._jobs.get()
            if job is None:
                break
            fn, done, box = job
            try:
                box["result"] = fn(page)
            except BaseException as e:
                box["error"] = e
            done.set()
        try:
            page.close()
        except Exception:
            pass
        try:
            cm.__exit__(None, None, None)
        except Exception:
            pass

    # ---- calling threads ------------------------------------------------
    def call(self, fn, timeout=CALL_TIMEOUT):
        """Run fn(page) on the owner thread; raise whatever it raised.
        A timeout means the page is wedged — the driver is unusable and the
        caller/pool should close it (the wedged op keeps the daemon thread)."""
        if self._closed:
            raise RuntimeError("camoufox driver is closed")
        done = threading.Event()
        box = {}
        self._jobs.put((fn, done, box))
        if not done.wait(timeout):
            raise TimeoutError(f"camoufox op timed out after {timeout}s (page wedged)")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def goto(self, url, referer=None):
        return self.call(lambda page: page.goto(
            url, referer=referer, wait_until="domcontentloaded",
            timeout=GOTO_TIMEOUT_MS))

    def evaluate(self, js, arg=None):
        return self.call(lambda page: page.evaluate(js, arg))

    def get(self, url, headers=None, timeout_ms=FETCH_TIMEOUT_MS,
            call_timeout=CALL_TIMEOUT):
        """requests-like GET run as an in-page fetch (inherits the page's
        cookies, x5sec clearance and TLS fingerprint). Returns a
        FetchResponse; never raises on HTTP/network errors — check .ok /
        .status_code. `call_timeout` is the hard owner-thread ceiling."""
        return self.request("GET", url, headers=headers, timeout_ms=timeout_ms,
                            call_timeout=call_timeout)

    def request(self, method, url, headers=None, body=None,
                timeout_ms=FETCH_TIMEOUT_MS, call_timeout=CALL_TIMEOUT):
        res = self.call(
            lambda page: page.evaluate(_FETCH_JS, {
                "url": url,
                "method": method,
                "headers": headers if headers is not None else DEFAULT_FETCH_HEADERS,
                "body": body,
                "timeoutMs": timeout_ms,
            }),
            timeout=call_timeout,
        ) or {}
        return FetchResponse(
            ok=bool(res.get("ok")),
            status_code=int(res.get("status") or 0),
            text=res.get("body") or "",
            error=res.get("error"),
        )

    def cookies(self):
        return self.call(lambda page: page.context.cookies())

    def content(self):
        return self.call(lambda page: page.content())

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._jobs.put(None)
        self._thread.join(timeout=30)
        # A wedged page op keeps the owner thread stuck inside Playwright: it
        # never reaches the sentinel, cm.__exit__ never runs, and the join
        # above times out — kill the browser's process trees directly.
        self._force_kill()
        with _drivers_lock:
            _drivers.discard(self)

    def _force_kill(self):
        """SIGKILL whatever of this driver's OS processes survived a clean
        shutdown. No-op when the owner thread already ran cm.__exit__."""
        for proc in self._procs:
            _kill_tree(proc)


class TabbedCamoufoxDriver(CamoufoxDriver):
    """One browser, one persistent tab per key (a domain). Tabs are created and
    used only on the owner thread; the pool's single-lease semantics serialize
    all access, so the plain dict needs no locking. Tabs die with the browser
    on close() — no per-tab cleanup required.

    At most MAX_TABS tabs are kept: each holds a fully-loaded page (images
    unblocked) that keeps running JS for the browser's lifetime, so unbounded
    domains would grow the content process by hundreds of MB per tab. Opening a
    tab beyond the cap evicts the least-recently-used one; its domain simply
    reseeds on its next request. The dict doubles as the LRU order (call_tab
    reinserts on use)."""

    MAX_TABS = 8

    def __init__(self, *args, **kwargs):
        self._tabs = {}  # domain -> page, least-recently-used first
        super().__init__(*args, **kwargs)

    def has_tab(self, domain):
        return domain in self._tabs

    def open_tab(self, domain):
        def _open(_default_page):
            while len(self._tabs) >= self.MAX_TABS:
                victim, page = next(iter(self._tabs.items()))
                del self._tabs[victim]
                try:
                    page.close()
                except Exception:
                    pass
                print(f"camoufox: evicted LRU tab {victim} "
                      f"({len(self._tabs)}/{self.MAX_TABS} tabs)")
            page = self._browser.new_page()
            page.set_default_timeout(30000)
            self._tabs[domain] = page
        self.call(_open)

    def close_tab(self, domain):
        def _close(_default_page):
            page = self._tabs.pop(domain, None)
            if page is not None:
                page.close()
        self.call(_close)

    def call_tab(self, domain, fn, timeout=CALL_TIMEOUT):
        """Run fn(page) on the owner thread against the domain's tab."""
        def _run(_default_page):
            page = self._tabs.pop(domain)   # reinsert -> most-recently-used
            self._tabs[domain] = page
            return fn(page)
        return self.call(_run, timeout)
