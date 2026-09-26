"""Google transport for the OPEN surfaces: plain curl_cffi with
browser-impersonated TLS, no cookies, no browser.

Validated 2026-09-24 from a direct egress (and through a US residential exit):

  * AUTOCOMPLETE  GET www.google.com/complete/search?q&client&hl&gl
  * NEWS RSS      GET news.google.com/rss[/search|/headlines/section/...]
                  + the DotsSplashUi batchexecute `Fbv4je` article resolver
  * TRENDS        GET trends.google.com/trending (AF_initDataCallback SSR),
                  /trending/rss, and the explore -> widgetdata token dance
                  (the explore PAGE answers 429 but sets the NID cookie the
                  /trends/api/* calls need; the session jar keeps it)
  * PATENTS       GET patents.google.com/xhr/query (JSON) + /patent/<id>/<lang>
  * FINANCE       GET www.google.com/finance/quote/<SYM:EXCH>, /finance/markets/<tab>
                  (server-rendered AF_initDataCallback blocks)

None of these run botguard. The SERP surfaces (/search) do — see
google_search/identity.py for the minted-identity replay.

A thread whose direct egress is refused (403 / 429 where a real answer was
due) moves to a residential exit (config.GOOGLE_SEARCH_FALLBACK_PROXY_COUNTRY)
for GOOGLE_SEARCH_DIRECT_COOLDOWN seconds.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  GoogleUpstreamError  transport failure / 5xx / unparseable body  — retryable
  GoogleBlocked        403 / 429 / captcha / JS wall               — retryable on a new identity or exit
  GoogleBadRequest     upstream rejected the params               — never retried
  GoogleNotFound       unknown symbol / patent / topic            — never retried
"""
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

SITE = "https://www.google.com"
NEWS_SITE = "https://news.google.com"
TRENDS_SITE = "https://trends.google.com"
PATENTS_SITE = "https://patents.google.com"
IMPERSONATE = "chrome"
TIMEOUT = 30
PAGE_TIMEOUT = 45          # finance / trending pages run past 1 MB
FANOUT_WORKERS = 4
XSSI_PREFIX = ")]}'"

PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "upgrade-insecure-requests": "1",
}
JSON_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

_AF_RE = re.compile(r"AF_initDataCallback\(\{key: '(ds:\d+)', hash: '[^']*', data:(.*?), sideChannel: \{\}\}\);", re.S)


class GoogleUpstreamError(UpstreamError):
    """Transport failure, 5xx or an unparseable body — retryable."""


class GoogleBlocked(GoogleUpstreamError, Blocked):
    """403 / 429 / captcha page / JS wall — retryable on a new exit or identity."""


class GoogleBadRequest(BadRequest):
    """Upstream rejected the params — never retried."""


class GoogleNotFound(NotFound):
    """The symbol / patent / topic / story does not exist — never retried."""


def dump_debug(name, text):
    """Write a raw response to $GOOGLE_SEARCH_DEBUG_DIR/<name>.txt."""
    dbg = os.environ.get("GOOGLE_SEARCH_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".txt"), "w") as f:
                f.write(text if isinstance(text, str) else repr(text))
        except OSError:
            pass


# ---- sessions --------------------------------------------------------------------
# One curl session per worker thread per host family (a curl handle must not
# be shared across threads). The session keeps cookies (the Trends NID) and
# keep-alive connections for the thread's lifetime.
_local = threading.local()


def _sessions():
    store = getattr(_local, "sessions", None)
    if store is None:
        store = _local.sessions = {}
    return store


def _session(key="default"):
    store = _sessions()
    via_fallback = getattr(_local, "proxy_until", 0) > time.time()
    sess = store.get(key)
    if sess is not None and getattr(sess, "_gs_fallback", False) != via_fallback:
        _drop_session(key)
        sess = None
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = config.google_search_proxy() or (config.google_search_fallback_proxy() if via_fallback else None)
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        sess._gs_fallback = via_fallback
        store[key] = sess
    return sess


def _drop_session(key="default"):
    sess = _sessions().pop(key, None)
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def _escalate(key="default"):
    """The current exit was refused: move this thread's egress to a fresh
    residential exit for a while."""
    if config.google_search_fallback_proxy() is not None:
        _local.proxy_until = time.time() + config.GOOGLE_SEARCH_DIRECT_COOLDOWN
    _drop_session(key)


def _retrying(fn, key="default"):
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except (GoogleNotFound, GoogleBadRequest):
            raise
        except GoogleBlocked as e:
            last = e
            _escalate(key)
        except GoogleUpstreamError as e:
            last = e
            _drop_session(key)
        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def is_sorry(resp):
    """Google's /sorry captcha interstitial (HTTP 429 or a redirect to it)."""
    url = str(getattr(resp, "url", "") or "")
    return "/sorry/" in url or ("unusual traffic" in (resp.text or "")[:6000] and resp.status_code in (429, 200))


def request(method, url, *, label, key="default", timeout=TIMEOUT, ok_statuses=(200,), **kwargs):
    """One request on the thread's session -> response. 403/429/captcha ->
    GoogleBlocked; 400 -> GoogleBadRequest; 404 -> GoogleNotFound; 5xx or a
    status outside `ok_statuses` -> GoogleUpstreamError."""
    sess = _session(key)
    try:
        resp = sess.request(method, url, timeout=timeout, **kwargs)
    except Exception as e:
        raise GoogleUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
    status = resp.status_code
    if status in (403, 429) or is_sorry(resp):
        dump_debug(f"blocked_{label}", resp.text)
        raise GoogleBlocked(f"HTTP {status} on {label}")
    if status == 404:
        raise GoogleNotFound(f"{label}: not found")
    if status == 400:
        raise GoogleBadRequest(f"{label}: Google rejected the request")
    if status >= 500:
        raise GoogleUpstreamError(f"HTTP {status} on {label}")
    if status not in ok_statuses:
        raise GoogleUpstreamError(f"HTTP {status} on {label}")
    return resp


def get(url, *, label, params=None, headers=None, key="default", timeout=TIMEOUT, ok_statuses=(200,)):
    """Retried GET -> response."""
    if params:
        url += ("&" if "?" in url else "?") + urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return _retrying(lambda: request("GET", url, label=label, key=key, timeout=timeout, ok_statuses=ok_statuses,
                                     headers=headers or JSON_HEADERS, allow_redirects=True), key)


def get_text(url, **kwargs):
    return get(url, **kwargs).text


def get_html(url, *, label, params=None, key="default", timeout=PAGE_TIMEOUT):
    """GET one server-rendered page -> (html, final_url)."""
    resp = get(url, label=label, params=params, headers=PAGE_HEADERS, key=key, timeout=timeout)
    html = resp.text or ""
    if "<html" not in html[:4000].lower() and "<!doctype" not in html[:200].lower():
        dump_debug(f"nonhtml_{label}", html)
        raise GoogleUpstreamError(f"{label}: non-HTML body")
    return html, str(resp.url or url)


def strip_xssi(text):
    """Drop Google's `)]}'` anti-XSSI prefix (and the newline after it)."""
    text = (text or "").lstrip()
    if text.startswith(XSSI_PREFIX):
        text = text[len(XSSI_PREFIX):].lstrip(",\n\r ")
    return text


def parse_json(text, label):
    try:
        return json.loads(strip_xssi(text))
    except ValueError:
        dump_debug(f"badjson_{label}", text)
        raise GoogleUpstreamError(f"{label}: could not parse the JSON body")


def get_json(url, *, label, params=None, headers=None, key="default", timeout=TIMEOUT):
    return parse_json(get(url, label=label, params=params, headers=headers, key=key, timeout=timeout).text, label)


def af_blocks(html, label="page"):
    """Every AF_initDataCallback block of a server-rendered Google page ->
    {"ds:N": data}. Blocks that fail to parse are skipped."""
    out = {}
    for match in _AF_RE.finditer(html or ""):
        try:
            out[match.group(1)] = json.loads(match.group(2))
        except ValueError:
            dump_debug(f"badaf_{label}_{match.group(1).replace(':', '')}", match.group(2))
    return out


# ---- parallel helpers -------------------------------------------------------------

def run_parallel(fns, workers=FANOUT_WORKERS):
    """Run zero-arg callables concurrently, results in order; exceptions propagate."""
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    with ThreadPoolExecutor(max_workers=min(workers, len(fns))) as pool:
        return list(pool.map(lambda f: f(), fns))


def run_parallel_quiet(fns, workers=FANOUT_WORKERS):
    """Like run_parallel but a failed call yields None instead of raising."""
    def safe(fn):
        def call():
            try:
                return fn()
            except Exception as e:
                print(f"google-search: parallel call failed: {type(e).__name__}: {e}")
                return None
        return call
    return run_parallel([safe(f) for f in fns], workers)
