"""SERP endpoints: www.google.com/search verticals fetched through a
minted identity (identity.py) and parsed by serp_parsers.py.

  web        /search?q&hl&gl&num&start&tbs&safe&filter&nfpr&lr&cr&uule
  images     udm=2   + tbs=isz:,ic:,itp:,iar:,ift:,sur:,qdr:
  videos     udm=7   + tbs=dur:,qdr:
  news       tbm=nws + tbs=qdr:,sbd:1
  shopping   udm=28  + tbs=mr:1,price:1,ppr_min:,ppr_max:,new:1 / p_ord:
  local      udm=local
  jobs       udm=8
  books      tbm=bks
  forums     udm=18

Every result link Google wraps as /goto?url=<token> is resolved to the
publisher URL by one redirect-free GET (no cookies, no proxy needed —
verified 2026-09-24), fanned out on a thread pool per page.
"""
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_search import fetch, identity, serp_parsers as sp  # noqa: E402
from google_search.refs import uule_of  # noqa: E402

DEFAULT_NUM = 10
MAX_NUM = 100

identity.start_keeper()   # keeps config.GOOGLE_SEARCH_MIN_IDENTITIES warm (0 = off)
TIME_PERIODS = {"any": None, "hour": "qdr:h", "day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
SAFE = {"off": "off", "active": "active", "moderate": "images"}
IMAGE_SIZES = {"any": None, "large": "isz:l", "medium": "isz:m", "icon": "isz:i"}
IMAGE_COLORS = {"any": None, "color": "ic:color", "grayscale": "ic:gray", "transparent": "ic:trans",
                "red": "ic:specific,isc:red", "orange": "ic:specific,isc:orange", "yellow": "ic:specific,isc:yellow",
                "green": "ic:specific,isc:green", "teal": "ic:specific,isc:teal", "blue": "ic:specific,isc:blue",
                "purple": "ic:specific,isc:purple", "pink": "ic:specific,isc:pink", "white": "ic:specific,isc:white",
                "gray": "ic:specific,isc:gray", "black": "ic:specific,isc:black", "brown": "ic:specific,isc:brown"}
IMAGE_TYPES = {"any": None, "face": "itp:face", "photo": "itp:photo", "clipart": "itp:clipart", "lineart": "itp:lineart",
               "animated": "itp:animated"}
IMAGE_ASPECTS = {"any": None, "tall": "iar:t", "square": "iar:s", "wide": "iar:w", "panoramic": "iar:xw"}
IMAGE_FORMATS = {"any": None, "jpg": "ift:jpg", "gif": "ift:gif", "png": "ift:png", "bmp": "ift:bmp", "svg": "ift:svg",
                 "webp": "ift:webp", "ico": "ift:ico", "raw": "ift:craw"}
IMAGE_RIGHTS = {"any": None, "creative_commons": "sur:cl", "commercial": "sur:ol"}
VIDEO_DURATIONS = {"any": None, "short": "dur:s", "medium": "dur:m", "long": "dur:l"}
NEWS_SORTS = {"relevance": None, "date": "sbd:1"}
SHOPPING_SORTS = {"relevance": None, "price_low_to_high": "p_ord:p", "price_high_to_low": "p_ord:pd",
                  "review_score": "p_ord:rv"}
SHOPPING_CONDITIONS = {"any": None, "new": "new:1", "used": "used:1"}
RESOLVE_WORKERS = 8
_GOTO_RE = re.compile(r"^https://www\.google\.com/goto\?")
_resolve_cache = {}
_resolve_lock = threading.Lock()


# ---- request assembly ------------------------------------------------------------------------

def _tbs(*parts):
    parts = [p for p in parts if p]
    return ",".join(parts) if parts else None


def _base_params(query, country, language, location, num=None, start=None, tbs=None, safe=None, no_autocorrect=None,
                 no_duplicates=None, results_language=None, results_country=None):
    params = {"q": query, "hl": language or "en", "gl": (country or "us").lower()}
    if num:
        params["num"] = num
    if start:
        params["start"] = start
    if tbs:
        params["tbs"] = tbs
    if safe:
        params["safe"] = SAFE.get(safe, safe)
    if no_autocorrect:
        params["nfpr"] = 1
    if no_duplicates is False:
        params["filter"] = 0
    if results_language:
        params["lr"] = f"lang_{results_language}"
    if results_country:
        params["cr"] = f"country{results_country.upper()}"
    if location:
        params["uule"] = uule_of(location)
    return params


def _date_tbs(time_period, date_from, date_to):
    if date_from or date_to:
        def fmt(d):
            y, m, dd = d.split("-")
            return f"{int(m)}/{int(dd)}/{y}"
        return "cdr:1" + (f",cd_min:{fmt(date_from)}" if date_from else "") + (f",cd_max:{fmt(date_to)}" if date_to else "")
    return TIME_PERIODS.get(time_period or "any")


def _pagination(parsed, page, num):
    pg = parsed.get("pagination") or {}
    total = (parsed.get("search_information") or {}).get("total_results")
    has_next = bool(pg.get("has_next"))
    return {"page": page, "items_per_page": num, "total_pages": page + 1 if has_next else page,
            "total_count": total}


# ---- link resolution -----------------------------------------------------------------------------

def resolve_goto(link):
    """A /goto?url=<token> wrapper -> the publisher URL (cached per token)."""
    if not link or not _GOTO_RE.match(link):
        return link
    with _resolve_lock:
        hit = _resolve_cache.get(link)
    if hit:
        return hit
    from curl_cffi import requests as curl_requests
    try:
        resp = curl_requests.get(link, impersonate=fetch.IMPERSONATE, timeout=15, allow_redirects=False,
                                 headers={"accept": "*/*", "accept-language": "en-US,en;q=0.9"})
        target = resp.headers.get("location") if resp.status_code in (301, 302, 303, 307, 308) else None
    except Exception:
        target = None
    if target:
        with _resolve_lock:
            if len(_resolve_cache) > 5000:
                _resolve_cache.clear()
            _resolve_cache[link] = target
        return target
    return link


def _walk_links(obj, out):
    """Collect every dict with a goto `link` / `tracking_link` anywhere in the result."""
    if isinstance(obj, dict):
        for key in ("link", "page_link"):
            v = obj.get(key)
            if isinstance(v, str) and _GOTO_RE.match(v):
                out.append((obj, key))
        for v in obj.values():
            _walk_links(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_links(v, out)


def resolve_links(result):
    """Rewrite every /goto wrapper in a parsed result to the publisher URL
    (the wrapper is kept as `google_link` next to it)."""
    targets = []
    _walk_links(result, targets)
    if not targets:
        return result
    unique = list({obj[key] for obj, key in targets})
    with ThreadPoolExecutor(max_workers=min(RESOLVE_WORKERS, len(unique))) as pool:
        resolved = dict(zip(unique, pool.map(resolve_goto, unique)))
    for obj, key in targets:
        wrapper = obj[key]
        obj[key] = resolved.get(wrapper, wrapper)
        if obj[key] != wrapper and key == "link":
            obj["google_link"] = wrapper
            # an organic result whose <cite> held meta text has no domain yet
            if "domain" in obj and not obj["domain"]:
                obj["domain"] = urlparse(obj[key]).netloc or None
    return result


def _fetch(params, label, parser, resolve=True):
    html, final = identity.serp_html(params, label=label)
    result = parser(html)
    if resolve:
        resolve_links(result)
    return result


# ---- endpoints ---------------------------------------------------------------------------------------

PAGE_SIZE = 10   # Google serves 10 organic results per request whatever `num` says (2026-09-24)


def _web_pages(params, page, num, label, resolve):
    """`num` organic results starting at page `page` (of `num`): Google
    ignores num>10, so deeper counts are assembled from consecutive
    10-result requests (each spends one identity use)."""
    first_start = (page - 1) * num
    hops = max(1, (num + PAGE_SIZE - 1) // PAGE_SIZE)
    merged = None
    for hop in range(hops):
        hop_params = dict(params)
        start = first_start + hop * PAGE_SIZE
        if start:
            hop_params["start"] = start
        hop_params["num"] = PAGE_SIZE
        out = _fetch(hop_params, label, sp.parse_web, resolve)
        offset = first_start + (len(merged["organic_results"]) if merged else 0)
        for r in out["organic_results"]:
            r["position"] = offset + r["position"]
        if merged is None:
            merged = out
        else:
            merged["organic_results"] += out["organic_results"]
            merged["pagination"] = out["pagination"]
            for key in ("ads", "people_also_ask", "related_searches"):
                merged[key] = merged.get(key) or out.get(key) or []
        if not out["pagination"].get("has_next") or not out["organic_results"]:
            break
    merged["organic_results"] = merged["organic_results"][:num]
    return merged


def search(query, country="US", language="en", location=None, page=1, num=DEFAULT_NUM, time_period=None,
           date_from=None, date_to=None, safe=None, no_autocorrect=None, no_duplicates=None, results_language=None,
           results_country=None, resolve=True):
    """The full web SERP: organic results, ads, AI overview, knowledge
    panel, People also ask, top stories, videos, discussions, related
    searches."""
    params = _base_params(query, country, language, location, tbs=_date_tbs(time_period, date_from, date_to), safe=safe,
                          no_autocorrect=no_autocorrect, no_duplicates=no_duplicates, results_language=results_language,
                          results_country=results_country)
    out = _web_pages(params, page, num, "search", resolve)
    out["pagination"] = _pagination(out, page, num)
    return out


def search_light(query, country="US", language="en", location=None, page=1, num=DEFAULT_NUM, time_period=None,
                 date_from=None, date_to=None, safe=None, no_autocorrect=None, results_language=None,
                 results_country=None, resolve=True):
    """Organic results only; `num` up to 100 (assembled from 10-result pages)."""
    params = _base_params(query, country, language, location, tbs=_date_tbs(time_period, date_from, date_to), safe=safe,
                          no_autocorrect=no_autocorrect, results_language=results_language, results_country=results_country)
    full = _web_pages(params, page, num, "search-light", resolve)
    return {"search_information": full["search_information"], "organic_results": full["organic_results"],
            "pagination": _pagination(full, page, num)}


def people_also_ask(query, country="US", language="en", location=None, resolve=True):
    params = _base_params(query, country, language, location)
    full = _fetch(params, "people-also-ask", sp.parse_web, resolve)
    return {"search_information": full["search_information"], "count": len(full["people_also_ask"]),
            "questions": full["people_also_ask"], "related_searches": full["related_searches"]}


def ai_overview(query, country="US", language="en", location=None, resolve=True):
    params = _base_params(query, country, language, location)
    full = _fetch(params, "ai-overview", sp.parse_web, resolve)
    aio = full["ai_overview"] or {"is_available": False, "text": None, "sources": []}
    return {"search_information": full["search_information"], "ai_overview": aio,
            "people_also_ask": full["people_also_ask"]}


def images(query, country="US", language="en", location=None, size=None, color=None, image_type=None, aspect_ratio=None,
           file_type=None, usage_rights=None, time_period=None, safe=None, limit=50, resolve=True):
    tbs = _tbs(IMAGE_SIZES.get(size), IMAGE_COLORS.get(color), IMAGE_TYPES.get(image_type), IMAGE_ASPECTS.get(aspect_ratio),
               IMAGE_FORMATS.get(file_type), IMAGE_RIGHTS.get(usage_rights), TIME_PERIODS.get(time_period or "any"))
    params = _base_params(query, country, language, location, tbs=tbs, safe=safe)
    params["udm"] = 2
    out = _fetch(params, "images", sp.parse_images, resolve)
    out["images"] = out["images"][:limit]
    out["count"] = len(out["images"])
    return out


def videos(query, country="US", language="en", location=None, page=1, num=DEFAULT_NUM, duration=None, time_period=None,
           safe=None, resolve=True):
    params = _base_params(query, country, language, location, num=num, start=(page - 1) * num,
                          tbs=_tbs(VIDEO_DURATIONS.get(duration), TIME_PERIODS.get(time_period or "any")), safe=safe)
    params["udm"] = 7
    out = _fetch(params, "videos", sp.parse_videos, resolve)
    out["pagination"] = _pagination(out, page, num)
    return out


def news(query, country="US", language="en", location=None, page=1, num=DEFAULT_NUM, time_period=None, date_from=None,
         date_to=None, sort="relevance", resolve=True):
    params = _base_params(query, country, language, location, num=num, start=(page - 1) * num,
                          tbs=_tbs(_date_tbs(time_period, date_from, date_to), NEWS_SORTS.get(sort)))
    params["tbm"] = "nws"
    out = _fetch(params, "news", sp.parse_news, resolve)
    out["pagination"] = _pagination(out, page, num)
    return out


def shopping(query, country="US", language="en", location=None, min_price=None, max_price=None, sort="relevance",
             condition=None, free_shipping=None, on_sale=None, limit=40, resolve=True):
    parts = []
    if min_price is not None or max_price is not None:
        parts.append("mr:1,price:1" + (f",ppr_min:{min_price:g}" if min_price is not None else "")
                     + (f",ppr_max:{max_price:g}" if max_price is not None else ""))
    if condition and SHOPPING_CONDITIONS.get(condition):
        parts.append("mr:1," + SHOPPING_CONDITIONS[condition])
    if free_shipping:
        parts.append("mr:1,ship:1")
    if on_sale:
        parts.append("mr:1,sales:1")
    if SHOPPING_SORTS.get(sort):
        parts.append("mr:1," + SHOPPING_SORTS[sort])
    params = _base_params(query, country, language, location, tbs=_tbs(*parts))
    params["udm"] = 28
    out = _fetch(params, "shopping", sp.parse_shopping, resolve)
    out["products"] = out["products"][:limit]
    out["count"] = len(out["products"])
    return out


def local(query, country="US", language="en", location=None, page=1, resolve=True):
    per_page = 20
    params = _base_params(query, country, language, location, start=(page - 1) * per_page)
    params["udm"] = "local"
    out = _fetch(params, "local", sp.parse_local, resolve)
    out["pagination"] = _pagination(out, page, per_page)
    return out


def jobs(query, country="US", language="en", location=None, resolve=True):
    q = f"{query} {location}" if location and not location.startswith("w+") else query
    params = _base_params(q, country, language, None)
    params["udm"] = 8
    out = _fetch(params, "jobs", sp.parse_jobs, resolve)
    out["count"] = len(out["jobs"])
    return out


def books(query, country="US", language="en", page=1, num=DEFAULT_NUM, time_period=None, resolve=True):
    params = _base_params(query, country, language, None, num=num, start=(page - 1) * num,
                          tbs=TIME_PERIODS.get(time_period or "any"))
    params["tbm"] = "bks"
    out = _fetch(params, "books", sp.parse_books, resolve)
    out["pagination"] = _pagination(out, page, num)
    return out


def forums(query, country="US", language="en", location=None, page=1, num=DEFAULT_NUM, time_period=None, resolve=True):
    params = _base_params(query, country, language, location, num=num, start=(page - 1) * num,
                          tbs=TIME_PERIODS.get(time_period or "any"))
    params["udm"] = 18
    out = _fetch(params, "forums", sp.parse_forums, resolve)
    out["pagination"] = _pagination(out, page, num)
    return out


def identities():
    """Diagnostics: the live identity pool."""
    return identity.pool_status()
