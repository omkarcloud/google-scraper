"""Reference parsing: ONE param per input that auto-detects its forms
(tripadvisor QueryOrLinkField convention — never a sibling `url`/`id`
pair). Resolvers return plain values so the validated params stay usable
as a response-cache key.

  query      "best laptop" | google.<tld>/search?q=best+laptop&gl=uk…
             -> the query text (the link's gl / hl / tbs / start / udm are
             picked up by the schema through `params_of`)
  patent     US10000000B2 | patents.google.com/patent/US10000000B2/en -> "US10000000B2"
  symbol     AAPL:NASDAQ | google.com/finance/quote/AAPL:NASDAQ -> "AAPL:NASDAQ"
  article    <CBMi… id> | news.google.com/(rss/)articles/<id>?oc=5 -> the id
  topic      WORLD | news.google.com/topics/<CAAq…>[/sections/…] -> WORLD | the id
  location   "Austin,Texas,United States" (a Google Ads canonical name) -> the uule token
"""
import base64
import re
from urllib.parse import parse_qs, urlparse

_GOOGLE_HOST_RE = re.compile(r"(^|\.)google\.[a-z.]{2,10}$")
_UULE_KEY = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _is_link(value):
    low = value.lower()
    return low.startswith(("http://", "https://", "//")) or "google." in low


def _parsed(value):
    return urlparse(value if "://" in value else "https://" + value.lstrip("/"))


def is_google_link(value):
    value = (value or "").strip()
    return _is_link(value) and bool(_GOOGLE_HOST_RE.search((_parsed(value).hostname or "").lower()))


def params_of(link):
    """The query params of a google search link (q, gl, hl, tbs, start, num, udm, tbm…)."""
    if not is_google_link(link):
        return {}
    return {k: v[0] for k, v in parse_qs(_parsed(link).query).items() if v}


def resolve_query(value):
    value = (value or "").strip()
    if is_google_link(value):
        q = params_of(value).get("q")
        if not q:
            raise ValueError("a Google search link must carry a q= query")
        return q.strip()
    if _is_link(value) and not is_google_link(value):
        raise ValueError("query must be a search term or a google.com/search link")
    if not value:
        raise ValueError("query must not be empty")
    return value


def resolve_article(value):
    value = (value or "").strip()
    m = re.search(r"/(?:rss/)?articles/([A-Za-z0-9_\-]+)", value)
    if m:
        return m.group(1)
    if _is_link(value):
        raise ValueError("article must be a Google News article id or a news.google.com/articles/<id> link")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,}", value):
        raise ValueError("article must be a Google News article id (CBMi…) or its news.google.com link")
    return value


def resolve_topic(value):
    value = (value or "").strip()
    m = re.search(r"/(?:topics|publications)/([A-Za-z0-9_\-]+)", value)
    if m:
        return m.group(1)
    if _is_link(value):
        raise ValueError("topic must be a topic name (WORLD, BUSINESS…) or a news.google.com/topics/<id> link")
    if not value:
        raise ValueError("topic must not be empty")
    return value


def uule_of(canonical_name):
    """The `uule` token Google reads a search origin from: 'w+CAIQICI' +
    a length key + base64 of the canonical location name (Google Ads
    geotargets 'City,Region,Country' form)."""
    name = (canonical_name or "").strip()
    if not name:
        return None
    raw = name.encode("utf-8")
    if len(raw) >= len(_UULE_KEY):
        raise ValueError("location must be shorter than 64 bytes")
    return "w+CAIQICI" + _UULE_KEY[len(raw)] + base64.b64encode(raw).decode("ascii")


def resolve_location(value):
    value = (value or "").strip()
    if value.startswith("w+CAIQICI"):
        return value
    if _is_link(value):
        raise ValueError("location must be a place name like 'Austin,Texas,United States'")
    return value
