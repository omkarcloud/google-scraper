"""SERP identities: how www.google.com/search pages are fetched.

Every /search request from a bare HTTP client answers the botguard "enable
JavaScript" wall (92 KB, HTTP 200, no results) whatever the exit,
impersonation, user-agent or consent cookie, and most fresh residential
exits answer the /sorry enterprise reCAPTCHA even to a real browser —
verified 2026-09-24 across ~40 exits (residential US/GB/CA/DE/FR/NL/IN/BR/
JP/AU from two providers, a datacenter egress; Camoufox and headed
patchright; direct navigation, human-typed queries, consent-accepted EU
sessions).

What passes is an IDENTITY minted like this:
  1. a fresh STICKY residential exit (config.google_search_mint_proxy());
  2. a headed Camoufox on it (geoip=True: timezone / locale / geolocation
     from the exit IP) with the CapSolver Firefox extension loaded
     (google_search/capsolver_extension, api key from config);
  3. google.com, then one SERP. About half the exits get the SERP straight
     away (no captcha, no solver needed). On /sorry the extension solves the
     captcha (25-55 s) and Google answers the SERP with
     GOOGLE_ABUSE_EXEMPTION + DV cookies; the jar + user-agent are read back.
     Without config.CAPSOLVER_API_KEY the extension is not loaded and a
     /sorry exit is skipped at once (the next exit may need no captcha);
     when every exit asks for one, the error says to add a CapSolver key;
  4. one curl_cffi replay THROUGH THE SAME EXIT validates the identity
     (the exemption is bound to the exit: from another egress the same
     cookies wall or /sorry).
Measured: 2 of 3 exits mint; 25+ replays per identity without
degradation; an in-browser wall after the solve happens too (retry on a
new exit).

Identities are consumables: each serves ONE search at a time (bursts
through one exit wall it in seconds; spacing beyond that does not stretch
its life — tuning 2026-09-25) until the JS wall (~35-40 SERPs per exit,
over curl or in the browser alike; nothing to solve, a new exit is the
only way on) or GOOGLE_SEARCH_IDENTITY_TTL seconds. A /sorry captcha on
the way (often after ~20-26 searches) is solved in the background over
curl — CapSolver THROUGH THE IDENTITY'S EXIT, form POSTed through it too —
up to GOOGLE_SEARCH_SORRY_SOLVES times, while the request moves on to
another identity. The walled / captcha'd request is retried on another
identity. config.GOOGLE_SEARCH_IDENTITY_QUOTA may cap the uses (None = no
cap). A spare is minted in the background after GOOGLE_SEARCH_SPARE_AFTER
uses so a replacement is warm when the wall comes.

serp_html(params) is the only entry point: it returns (html, final_url)
of a real SERP or raises GoogleBlocked when no identity could be minted.
"""
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from google_search import fetch

SITE = fetch.SITE
HOME = SITE + "/?hl=en&gl=us"
MINT_SERP = SITE + "/search?q=best+laptop+2026&hl=en&gl=us"
HOME_SETTLE = 5          # seconds on the home page before the SERP
SERP_SETTLE = 4          # seconds on a loaded SERP before the cookies are read
SOLVE_POLL = 3
WALL_GRACE = 25          # seconds a wall page may persist in-browser before the mint gives up
REPLAY_TIMEOUT = 40
MAX_ATTEMPTS = 3         # identities tried per SERP request
CAPSOLVER_SIGNUP = "https://dashboard.capsolver.com/passport/register?inviteCode=lvdYBC4sYKRm"
EXTENSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capsolver_extension")

SERP_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "referer": SITE + "/",
}


def classify(text, url=""):
    """'sorry' | 'wall' | 'ok' for a /search response. The wall is a ~92 KB
    page titled plain "Google Search" with a "click here" link carrying
    emsg=SG_REL and NO search column (`id="search"`, `id="rso"`). Every
    real page carries the enablejs <noscript> retry meta too, and an EMPTY
    result page (e.g. shopping for a query with no products) has #search
    but no #rso — so neither "enablejs" nor a missing #rso means a wall
    (tuning run 2026-09-25: that misread retired healthy identities on
    every empty shopping page). Images / news / shopping have no <h3>, so
    the heading count is not a signal either."""
    body = text or ""
    head = body[:8000]
    if "/sorry/" in (url or "") or ("unusual traffic" in head and "captcha" in head):
        return "sorry"
    if 'id="rso"' not in body and 'id="search"' not in body and ("emsg=SG_REL" in body or "enablejs" in body):
        return "wall"
    return "ok"


# ---- the extension -------------------------------------------------------------------------

_ext_lock = threading.Lock()
_ext_path = {"dir": None}


def extension_dir():
    """A private copy of the vendored CapSolver extension with the api key
    written into assets/config.js (the repo copy keeps the key empty)."""
    with _ext_lock:
        if _ext_path["dir"] and os.path.exists(os.path.join(_ext_path["dir"], "manifest.json")):
            return _ext_path["dir"]
        if not os.path.exists(os.path.join(EXTENSION_DIR, "manifest.json")):
            raise RuntimeError(f"CapSolver extension missing at {EXTENSION_DIR}")
        target = os.path.join(tempfile.gettempdir(), f"google-search-capsolver-{os.getpid()}")
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(EXTENSION_DIR, target)
        cfg = os.path.join(target, "assets", "config.js")
        with open(cfg, encoding="utf-8", newline="") as f:
            text = f.read().replace("\r\n", "\n")
        text = text.replace("apiKey: '',", f"apiKey: '{config.CAPSOLVER_API_KEY}',", 1)
        text = text.replace("appId: '',", f"appId: '{config.CAPSOLVER_APP_ID}',", 1)
        # LF only: the extension's config parser strips "\n" but not "\r", so
        # a CRLF file (Windows text mode) turns the first key into "\rapiKey"
        # and the extension runs without a key — every Windows trial on
        # 2026-09-24 failed on exactly this until the harness wrote LF.
        with open(cfg, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        _ext_path["dir"] = target
        return target


# ---- identities ----------------------------------------------------------------------------

class Identity:
    __slots__ = ("key", "cookies", "ua", "proxy", "born", "uses", "retired", "lock", "busy", "next_free",
                 "solving", "solves")

    def __init__(self, cookies, ua, proxy):
        self.key = uuid.uuid4().hex
        self.cookies, self.ua, self.proxy = cookies, ua, proxy
        self.born = time.time()
        self.uses = 0
        self.retired = False
        self.lock = threading.Lock()
        # One search at a time per identity, spaced: Google walls an identity
        # that fires a burst through one exit (stress test 2026-09-25: 8
        # concurrent requests walled every fresh identity within 0-3 s).
        self.busy = False
        self.next_free = 0.0
        # /sorry revival in progress (the identity stays busy meanwhile) and
        # how many captchas were solved for it after the mint
        self.solving = False
        self.solves = 0

    @property
    def exit(self):
        return self.proxy.rsplit("@", 1)[-1] if self.proxy else "direct"

    def expired(self):
        return time.time() - self.born > config.GOOGLE_SEARCH_IDENTITY_TTL

    def left(self):
        quota = config.GOOGLE_SEARCH_IDENTITY_QUOTA
        return (quota - self.uses) if quota else None

    def usable(self):
        return not self.retired and not self.expired() and (self.left() is None or self.left() > 0)

    def take(self):
        with self.lock:
            if self.left() is not None and self.left() <= 0:
                return False
            self.uses += 1
            return True


def _mint_once(proxy, outcome=None):
    """One headed Camoufox on `proxy`: home page, one SERP (captcha solved
    by the extension when a CapSolver key is set), cookie jar. Returns
    (cookies, ua) or None when the exit ended on the captcha / the wall;
    `outcome["verdict"]` (when a dict is passed) says which."""
    outcome = outcome if outcome is not None else {}
    solver = bool(config.CAPSOLVER_API_KEY)
    from chrome_manager import create_scope
    from camoufox_driver import CamoufoxDriver
    label = proxy.rsplit("@", 1)[-1] if proxy else "direct"
    started = time.monotonic()
    from browserforge.fingerprints import Screen
    with create_scope():
        # Explicit screen range: on a Windows VM console (small display) the
        # default, derived from the real display, matches no fingerprint.
        driver = CamoufoxDriver(proxy_url=proxy, block_images=False, addons=[extension_dir()] if solver else [],
                                screen=Screen(min_width=1024, max_width=1920, min_height=700, max_height=1080))
    try:
        driver.goto(HOME)
        time.sleep(HOME_SETTLE)
        driver.call(lambda page: page.goto(MINT_SERP, referer=SITE + "/", wait_until="domcontentloaded", timeout=40000))
        # In-browser sequence on a fresh exit: the JS wall page (which runs
        # botguard and redirects itself) -> /sorry -> the extension solves
        # (25-95 s) -> the SERP renders. A wall is therefore only final when
        # it persists for WALL_GRACE seconds; the captcha only when the
        # solve deadline passes.
        deadline = time.monotonic() + config.GOOGLE_SEARCH_SOLVE_TIMEOUT
        wall_since = None
        verdict, html = "wall", ""
        while True:
            time.sleep(SOLVE_POLL)
            try:
                url = driver.call(lambda page: page.url)
                html = driver.content()
            except Exception as e:
                # "Unable to retrieve content because the page is navigating":
                # usually the solved captcha redirecting to the SERP. Poll again
                # instead of abandoning a mint that is about to succeed.
                if "navigating" not in str(e) or time.monotonic() > deadline:
                    raise
                continue
            verdict = classify(html, url)
            if verdict == "ok" and 'id="rso"' in html:
                break
            if verdict == "sorry" and not solver:
                break                       # nothing will solve it: try the next exit
            if verdict == "wall":
                wall_since = wall_since or time.monotonic()
                if time.monotonic() - wall_since > WALL_GRACE:
                    break
            else:
                wall_since = None
            if time.monotonic() > deadline:
                break
        outcome["verdict"] = verdict
        if verdict != "ok" or 'id="rso"' not in html:
            print(f"google-search: mint on {label} ended on the {verdict} page ({time.monotonic() - started:.0f}s)")
            return None
        time.sleep(SERP_SETTLE)
        ua = driver.evaluate("navigator.userAgent")
        cookies = {c["name"]: c["value"] for c in driver.cookies() if "google" in (c.get("domain") or "")}
        print(f"google-search: identity minted on {label} in {time.monotonic() - started:.0f}s "
              f"(cookies: {', '.join(sorted(cookies))})")
        return cookies, ua
    finally:
        try:
            driver.close()
        except Exception:
            pass


def _new_session(proxy):
    from curl_cffi import requests as curl_requests
    sess = curl_requests.Session(impersonate=fetch.IMPERSONATE)
    if proxy:
        sess.proxies = {"http": proxy, "https": proxy}
    return sess


def _curl_validates(cookies, ua, proxy):
    """One replay through the identity's exit must answer a real SERP."""
    sess = _new_session(proxy)
    try:
        resp = sess.get(SITE + "/search?" + urlencode({"q": "python programming", "hl": "en", "gl": "us"}),
                        headers={**SERP_HEADERS, "user-agent": ua}, cookies=cookies, timeout=REPLAY_TIMEOUT)
    except Exception as e:
        print(f"google-search: replay validation failed: {type(e).__name__}: {e}")
        return False
    finally:
        try:
            sess.close()
        except Exception:
            pass
    verdict = classify(resp.text, str(resp.url))
    if verdict != "ok":
        print(f"google-search: the minted identity does not replay over curl ({verdict})")
    return verdict == "ok"


_cv = threading.Condition()          # guards the pool state below
_identities = deque()
_minting = 0                         # mints started and not yet finished
_waiting = 0                         # requests waiting for a free identity
_failed_until = 0.0
_last_failure = ""                   # why the last mint round failed (shown to callers)
_keeper_started = False
KEEPER_PERIOD = 20                   # seconds between warm-floor checks
WARM_HEADROOM = 180                  # an identity this close to its TTL no longer counts as warm
_mint_slots = threading.BoundedSemaphore(config.GOOGLE_SEARCH_PARALLEL_MINTS)


def _mint():
    """Mint one identity, trying up to GOOGLE_SEARCH_MINT_ATTEMPTS exits."""
    global _failed_until, _last_failure
    captchas = 0
    for attempt in range(config.GOOGLE_SEARCH_MINT_ATTEMPTS):
        proxy = config.google_search_mint_proxy()
        outcome = {}
        try:
            minted = _mint_once(proxy, outcome)
        except Exception as e:
            print(f"google-search: mint attempt {attempt + 1} failed: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
            continue
        if minted is None:
            captchas += outcome.get("verdict") == "sorry"
            continue
        cookies, ua = minted
        if not _curl_validates(cookies, ua, proxy):
            continue
        ident = Identity(cookies, ua, proxy)
        with _cv:
            _identities.append(ident)
            _cv.notify_all()
        return ident
    _failed_until = time.time() + config.GOOGLE_SEARCH_MINT_COOLDOWN
    if captchas and not config.CAPSOLVER_API_KEY:
        _last_failure = (f"Google asked for a captcha on {captchas} of {config.GOOGLE_SEARCH_MINT_ATTEMPTS} exits "
                         f"tried. Set CAPSOLVER_API_KEY to solve it automatically (one captcha per ~30 searches): "
                         f"{CAPSOLVER_SIGNUP}")
    else:
        _last_failure = (f"Google answered the captcha or the wall on every mint exit "
                         f"({config.GOOGLE_SEARCH_MINT_ATTEMPTS} tried)")
    raise fetch.GoogleBlocked(_last_failure)


def _mint_worker(reason):
    global _minting
    try:
        with _mint_slots:
            _mint()
    except Exception as e:
        print(f"google-search: mint ({reason}) failed: {e}")
    finally:
        with _cv:
            _minting -= 1
            _cv.notify_all()


def _start_mint_locked(reason):
    global _minting
    _minting += 1
    threading.Thread(target=_mint_worker, args=(reason,), name="google-search-mint", daemon=True).start()


def _prune_locked():
    for ident in list(_identities):
        if not ident.usable():
            ident.retired = True
            _identities.remove(ident)


def _plan_mints_locked():
    """Start mints so identities keep up with demand: about one identity per
    two requests in flight or waiting (each identity serves one search at a
    time, ~2 s plus spacing), capped at GOOGLE_SEARCH_MAX_IDENTITIES; plus one
    spare once every live identity has served GOOGLE_SEARCH_SPARE_AFTER
    searches, so a replacement is warm when the wall comes."""
    if time.time() < _failed_until:
        return
    live = [i for i in _identities if i.usable()]
    demand = _waiting + sum(i.busy and not i.solving for i in live)
    # one identity per two requests in flight/waiting, plus one warming up
    # under real load (an identity lasts ~1 min / 10-40 searches, a mint takes
    # 1-3 min, so supply must run ahead of demand)
    desired = min(config.GOOGLE_SEARCH_MAX_IDENTITIES, max(1, -(-demand // 2)) + (1 if demand >= 2 else 0))
    while len(live) + _minting < desired:
        _start_mint_locked(f"demand {demand}")
    # idle floor: identities that stay usable for another WARM_HEADROOM seconds
    floor = min(getattr(config, "GOOGLE_SEARCH_MIN_IDENTITIES", 0), config.GOOGLE_SEARCH_MAX_IDENTITIES)
    fresh = [i for i in live if time.time() - i.born < config.GOOGLE_SEARCH_IDENTITY_TTL - WARM_HEADROOM]
    while len(fresh) + _minting < floor:
        _start_mint_locked("warm floor")
        fresh.append(None)
    if live and _minting == 0 and len(live) < config.GOOGLE_SEARCH_MAX_IDENTITIES \
            and all(i.uses >= config.GOOGLE_SEARCH_SPARE_AFTER for i in live):
        _start_mint_locked("spare")


def _keeper():
    """Background: keep GOOGLE_SEARCH_MIN_IDENTITIES identities warm while
    idle, so the first search after a quiet spell doesn't wait 1-5 minutes
    for a mint (live QA 2026-09-26: a cold /search took 286 s)."""
    while True:
        time.sleep(KEEPER_PERIOD)
        try:
            with _cv:
                _prune_locked()
                _plan_mints_locked()
        except Exception as e:
            print(f"google-search: keeper failed: {type(e).__name__}: {e}")


def start_keeper():
    """Start the warm-floor keeper once (no-op when the floor is 0)."""
    global _keeper_started
    with _cv:
        if _keeper_started or getattr(config, "GOOGLE_SEARCH_MIN_IDENTITIES", 0) <= 0:
            return
        _keeper_started = True
    threading.Thread(target=_keeper, name="google-search-keeper", daemon=True).start()


def checkout():
    """A live identity for exactly one search (busy until checkin / retire).
    Waits for one to free up or be minted, up to GOOGLE_SEARCH_ACQUIRE_TIMEOUT."""
    global _waiting
    deadline = time.monotonic() + config.GOOGLE_SEARCH_ACQUIRE_TIMEOUT
    with _cv:
        _waiting += 1
        try:
            while True:
                _prune_locked()
                now = time.time()
                free = [i for i in _identities if i.usable() and not i.busy]
                ready = [i for i in free if i.next_free <= now]
                if ready:
                    # the most-used one first: spares stay fresh for the wall
                    ident = max(ready, key=lambda i: i.uses)
                    ident.busy = True
                    ident.uses += 1
                    _plan_mints_locked()
                    return ident
                _plan_mints_locked()
                if not any(i.usable() for i in _identities) and _minting == 0 and time.time() < _failed_until:
                    raise fetch.GoogleBlocked(_last_failure or "Google refused every mint exit recently; "
                                                               "retrying after the cooldown")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise fetch.GoogleBlocked("no Google identity became available in time")
                soonest = min((i.next_free - now for i in free), default=remaining)
                _cv.wait(timeout=max(0.05, min(remaining, soonest, 5.0)))
        finally:
            _waiting -= 1


def checkin(ident):
    """The search finished without a block: free the identity after spacing."""
    with _cv:
        ident.busy = False
        ident.next_free = time.time() + config.GOOGLE_SEARCH_MIN_INTERVAL + random.uniform(0, 0.6)
        _cv.notify_all()


def _solve_sorry(ident, html, sorry_url):
    """Solve a /sorry page for `ident` over curl: CapSolver's enterprise
    reCAPTCHA task THROUGH THE IDENTITY'S OWN EXIT (a proxyless token is
    always refused, 0/4 on 2026-09-25; with the exit 2/2 in 17-40 s), then
    the captcha form (q, continue, g-recaptcha-response) POSTed through that
    exit. Returns the refreshed cookie jar, or None."""
    import html as htmlmod
    import re
    from urllib.parse import urljoin
    from curl_cffi import requests as curl_requests
    key = re.search(r'data-sitekey="([^"]+)"', html)
    tag = re.search(r'<form[^>]*id="captcha-form"[^>]*>', html)
    form = re.search(r'<form[^>]*id="captcha-form"[^>]*>(.*?)</form>', html, re.S)
    if not (key and tag and form and config.CAPSOLVER_API_KEY and ident.proxy):
        print(f"google-search: /sorry on {ident.exit} not solvable over curl (sitekey={bool(key)} form={bool(form)} "
              f"key={bool(config.CAPSOLVER_API_KEY)}; {len(html)} B, url {sorry_url[:80]})")
        fetch.dump_debug("sorry_unsolvable", html)
        return None
    action = urljoin(sorry_url, htmlmod.unescape((re.search(r'action="([^"]*)"', tag.group(0)) or [None, "index"])[1]))
    fields = {htmlmod.unescape(n): htmlmod.unescape(v) for n, v in
              re.findall(r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']', form.group(1))}
    task = {"type": "ReCaptchaV2EnterpriseTask", "websiteURL": sorry_url, "websiteKey": key.group(1),
            "userAgent": ident.ua, "proxy": ident.proxy}
    s_param = re.search(r'data-s="([^"]+)"', html)
    if s_param:
        task["enterprisePayload"] = {"s": htmlmod.unescape(s_param.group(1))}
    api = "https://api.capsolver.com"
    created = curl_requests.post(api + "/createTask", json={"clientKey": config.CAPSOLVER_API_KEY,
                                                            "appId": config.CAPSOLVER_APP_ID, "task": task},
                                 timeout=30).json()
    if created.get("errorId"):
        print(f"google-search: capsolver createTask failed: {created.get('errorCode')} {created.get('errorDescription', '')[:120]}")
        return None
    token = None
    deadline = time.monotonic() + config.GOOGLE_SEARCH_SOLVE_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(3)
        got = curl_requests.post(api + "/getTaskResult", json={"clientKey": config.CAPSOLVER_API_KEY,
                                                               "taskId": created["taskId"]}, timeout=30).json()
        if got.get("status") == "ready":
            token = (got.get("solution") or {}).get("gRecaptchaResponse")
            break
        if got.get("errorId"):
            print(f"google-search: capsolver task failed: {got.get('errorCode')} {got.get('errorDescription', '')[:120]}")
            return None
    if not token:
        print(f"google-search: capsolver gave no token for {ident.exit} in {config.GOOGLE_SEARCH_SOLVE_TIMEOUT}s")
        return None
    fields["g-recaptcha-response"] = token
    sess = _new_session(ident.proxy)
    try:
        resp = sess.post(action, data=fields, cookies=ident.cookies, timeout=REPLAY_TIMEOUT, allow_redirects=True,
                         headers={**SERP_HEADERS, "user-agent": ident.ua, "referer": sorry_url, "origin": SITE})
        verdict = classify(resp.text, str(resp.url))
        if verdict != "ok":
            print(f"google-search: Google refused the solved captcha on {ident.exit} ({verdict} page after the form)")
            return None
        cookies = dict(ident.cookies)
        for c in sess.cookies.jar:
            if "google" in (c.domain or ""):
                cookies[c.name] = c.value
        return cookies
    finally:
        try:
            sess.close()
        except Exception:
            pass


def _revive(ident, html, sorry_url):
    """Background: solve the identity's /sorry and put it back in the pool,
    or retire it. Much cheaper than a mint (no browser; 17-40 s): a revived
    identity served ~10 more searches, up to the exit's ~35-40 budget."""
    started = time.monotonic()
    try:
        cookies = _solve_sorry(ident, html, sorry_url)
    except Exception as e:
        print(f"google-search: sorry solve on {ident.exit} failed: {type(e).__name__}: {e}")
        cookies = None
    if cookies is None:
        with _cv:
            ident.solving = False
        retire(ident, "sorry page, solve failed")
        return
    with _cv:
        ident.cookies = cookies
        ident.solves += 1
        ident.solving = False
        ident.busy = False
        ident.next_free = time.time()
        _cv.notify_all()
    print(f"google-search: identity on {ident.exit} revived after {ident.uses} uses "
          f"(captcha solved over curl in {time.monotonic() - started:.0f}s)")


def _start_revive(ident, html, sorry_url):
    """True when a background revival of the identity was started (it stays
    busy until then); False when it may not be revived (retire it)."""
    with _cv:
        if ident.retired or ident.solving or ident.solves >= config.GOOGLE_SEARCH_SORRY_SOLVES \
                or not config.CAPSOLVER_API_KEY:
            return False
        ident.solving = True
    threading.Thread(target=_revive, args=(ident, html, sorry_url), name="google-search-revive", daemon=True).start()
    return True


def retire(ident, reason):
    with _cv:
        if not ident.retired:
            ident.retired = True
            print(f"google-search: identity on {ident.exit} retired ({reason}; {ident.uses} uses, "
                  f"age {time.time() - ident.born:.0f}s)")
        ident.busy = False
        _prune_locked()
        _cv.notify_all()


# ---- replay --------------------------------------------------------------------------------------

_local = threading.local()


def _session_for(ident):
    """Per-thread curl session bound to one identity's exit."""
    sessions = getattr(_local, "sessions", None)
    if sessions is None:
        sessions = _local.sessions = {}
    entry = sessions.get(ident.key)
    if entry is None:
        for other in list(sessions):
            if sessions[other][0].retired:
                try:
                    sessions.pop(other)[1].close()
                except Exception:
                    pass
        entry = sessions[ident.key] = (ident, _new_session(ident.proxy))
    return entry[1]


def _drop_session(ident):
    sessions = getattr(_local, "sessions", None) or {}
    entry = sessions.pop(ident.key, None)
    if entry is not None:
        try:
            entry[1].close()
        except Exception:
            pass


def serp_html(params, label="serp"):
    """(html, final_url) of www.google.com/search?<params> as a real
    browser sees it. Raises GoogleBlocked when no identity can serve it."""
    url = SITE + "/search?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})
    last = "no identity"
    for attempt in range(MAX_ATTEMPTS):
        ident = checkout()
        try:
            resp = _session_for(ident).get(url, headers={**SERP_HEADERS, "user-agent": ident.ua}, cookies=ident.cookies,
                                           timeout=REPLAY_TIMEOUT, allow_redirects=True)
        except Exception as e:
            checkin(ident)
            _drop_session(ident)
            raise fetch.GoogleUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
        verdict = classify(resp.text, str(resp.url))
        if verdict == "ok":
            checkin(ident)
            return resp.text, str(resp.url)
        fetch.dump_debug(f"{verdict}_{label}", resp.text)
        last = verdict
        _drop_session(ident)
        # /sorry is solvable over curl through the identity's exit; the wall
        # is not (a new exit is the only way on). Either way this request
        # moves on to another identity right away.
        if not (verdict == "sorry" and _start_revive(ident, resp.text, str(resp.url))):
            retire(ident, f"{verdict} page")
    raise fetch.GoogleBlocked(f"{label}: Google served the {last} page for every identity")


def pool_status():
    with _cv:
        _prune_locked()
        return {
            "minting": _minting,
            "waiting": _waiting,
            "identities": [{"exit": i.exit, "uses": i.uses, "busy": i.busy, "solving": i.solving, "solves": i.solves,
                            "age_s": int(time.time() - i.born), "retired": i.retired} for i in _identities],
        }
