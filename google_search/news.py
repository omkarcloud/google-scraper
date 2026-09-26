"""Google News feeds over the RSS surface (news.google.com/rss/*), plain
curl_cffi, no cookies (validated 2026-09-24; the HTML /search page is a
hard captcha for every client, the RSS never is).

  /rss?hl=&gl=&ceid=                                  top headlines
  /rss/headlines/section/topic/<TOPIC>?...            topic headlines (302 -> /rss/topics/<id>)
  /rss/topics/<id>?...                                a topic / publication id
  /rss/headlines/section/geo/<place>?...              local headlines
  /rss/search?q=<query>&...                           search (operators: when:7d,
                                                      after:/before:YYYY-MM-DD, site:, source:CNN, intitle:, -x, "…", OR)

Feeds carry ~100 items and no pagination. Item = title "headline - source",
`<link>` = news.google.com/rss/articles/<CBMi…> encoded redirect, guid,
pubDate, `<source url>`; top / topic feeds nest a cluster of related
articles as an <ol><li> HTML list inside <description>.

Article links: the CBMi… id is a protobuf whose payload is NOT the URL any
more (the base64 shortcut died in 2024). `resolve_link` replays what the
site does: GET /rss/articles/<id> for its `data-n-a-sg` / `data-n-a-ts`
signature, then POST the DotsSplashUi batchexecute rpc `Fbv4je`
(garturlreq) which answers ["garturlres", <publisher url>, …].
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_search import fetch  # noqa: E402
from google_search.shared import clean_text, locale_tag  # noqa: E402

TOPICS = ("WORLD", "NATION", "BUSINESS", "TECHNOLOGY", "ENTERTAINMENT", "SPORTS", "SCIENCE", "HEALTH")
TIME_WINDOWS = {"1h": "1h", "1d": "1d", "7d": "7d", "1y": "1y", "any": None,
                "hour": "1h", "day": "1d", "week": "7d", "year": "1y"}
MAX_LIMIT = 100
_ARTICLE_ID_RE = re.compile(r"/(?:rss/)?articles/([A-Za-z0-9_\-]+)")
_LI_RE = re.compile(r"<li>(.*?)</li>", re.S)
_A_RE = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_FONT_RE = re.compile(r"<font[^>]*>(.*?)</font>", re.S)
_SIG_RE = re.compile(r'data-n-a-sg="([^"]+)"[^>]*data-n-a-ts="([^"]+)"|data-n-a-ts="([^"]+)"[^>]*data-n-a-sg="([^"]+)"')


def _edition(country, language):
    country = (country or "US").upper()
    hl = locale_tag(language, country)
    return {"hl": hl, "gl": country, "ceid": f"{country}:{hl.split('-')[0]}"}


def _feed(path, country, language, extra=None, label="news"):
    params = _edition(country, language)
    params.update({k: v for k, v in (extra or {}).items() if v not in (None, "")})
    return fetch.get_text(f"{fetch.NEWS_SITE}{path}?{urlencode(params)}", label=label, headers=fetch.PAGE_HEADERS)


def _iso(pubdate):
    if not pubdate:
        return None
    try:
        return parsedate_to_datetime(pubdate).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return None


def article_id(link):
    m = _ARTICLE_ID_RE.search(link or "")
    return m.group(1) if m else None


def _split_title(title):
    """'Headline - Source' -> (headline, source)."""
    title = clean_text(title) or ""
    head, sep, source = title.rpartition(" - ")
    if sep and source and len(source) < 60:
        return head, source
    return title, None


def _related(description):
    """The <ol><li> cluster inside a top/topic item's description."""
    out = []
    for li in _LI_RE.findall(description or ""):
        a = _A_RE.search(li)
        if not a:
            continue
        link, title = a.group(1), clean_text(a.group(2))
        font = _FONT_RE.search(li)
        out.append({"id": article_id(link), "title": title, "link": link,
                    "source": clean_text(font.group(1)) if font else None})
    return out


def _parse_feed(xml_text, label):
    try:
        root = ElementTree.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ElementTree.ParseError:
        fetch.dump_debug(f"badrss_{label}", xml_text)
        raise fetch.GoogleUpstreamError(f"{label}: the feed is not valid RSS")
    channel = root.find("channel")
    if channel is None:
        raise fetch.GoogleUpstreamError(f"{label}: the feed has no channel")
    items = []
    for item in channel.findall("item"):
        title_raw = item.findtext("title")
        headline, source_from_title = _split_title(title_raw)
        source_el = item.find("source")
        link = (item.findtext("link") or "").strip()
        description = item.findtext("description") or ""
        related = _related(description)
        # the first related row is the item itself in cluster feeds
        if related and related[0].get("link") == link:
            related = related[1:]
        items.append({
            "id": article_id(link) or (item.findtext("guid") or "").strip() or None,
            "title": headline or None,
            "link": link or None,
            "source": {
                "name": clean_text(source_el.text) if source_el is not None and source_el.text else source_from_title,
                "link": source_el.get("url") if source_el is not None else None,
            },
            "published_at": _iso(item.findtext("pubDate")),
            "related_articles": related,
        })
    return {
        "feed_title": clean_text(channel.findtext("title")),
        "feed_link": (channel.findtext("link") or "").strip() or None,
        "updated_at": _iso(channel.findtext("lastBuildDate")),
        "articles": items,
    }


def _result(feed, limit, **head):
    articles = feed["articles"][:limit]
    out = dict(head)
    out.update({"feed_title": feed["feed_title"], "updated_at": feed["updated_at"],
                "count": len(articles), "articles": articles})
    return out


def top_headlines(country="US", language="en", limit=50):
    """The edition's top stories."""
    feed = _parse_feed(_feed("/rss", country, language, label="news top"), "news top")
    return _result(feed, limit, country=country.upper(), language=language)


def topic_headlines(topic, country="US", language="en", limit=50):
    """Headlines for a named topic (WORLD, BUSINESS, …) or a topic /
    publication id (the CAAq… token from a news.google.com/topics/<id> or
    /publications/<id> link)."""
    if topic.upper() in TOPICS:
        path = f"/rss/headlines/section/topic/{topic.upper()}"
    else:
        path = f"/rss/topics/{quote(topic, safe='')}"
    feed = _parse_feed(_feed(path, country, language, label="news topic"), "news topic")
    if not feed["articles"] and topic.upper() not in TOPICS:
        raise fetch.GoogleNotFound(f"no headlines for topic {topic!r}")
    return _result(feed, limit, topic=topic.upper() if topic.upper() in TOPICS else topic,
                   country=country.upper(), language=language)


def local_headlines(location, country="US", language="en", limit=50):
    """Local news for a place name (city / region / country)."""
    feed = _parse_feed(_feed(f"/rss/headlines/section/geo/{quote(location, safe='')}", country, language,
                             label="news local"), "news local")
    return _result(feed, limit, location=location, country=country.upper(), language=language)


def search(query, country="US", language="en", time_window=None, source=None, limit=50):
    """Search articles. `time_window` -> `when:` operator; `source` (a
    publisher domain or name) -> `site:` / `source:` operator."""
    q = query
    when = TIME_WINDOWS.get((time_window or "any").lower())
    if when:
        q += f" when:{when}"
    if source:
        q += f" site:{source}" if "." in source else f" source:{source}"
    feed = _parse_feed(_feed("/rss/search", country, language, {"q": q}, label="news search"), "news search")
    return _result(feed, limit, query=query, country=country.upper(), language=language,
                   time_window=time_window or "any", source=source)


# ---- article link resolution ------------------------------------------------------------

def _garturlreq(article, sg, ts):
    payload = ["garturlreq",
               [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
                "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
               article, int(ts) if str(ts).isdigit() else ts, sg]
    return [[["Fbv4je", json.dumps(payload, separators=(",", ":")), None, "generic"]]]


def resolve_link(article):
    """A Google News article id or news.google.com/(rss/)articles/<id> link
    -> the publisher's URL."""
    ident = article_id(article) or article.strip()
    if not ident:
        raise ValueError("article must be a Google News article id or link")
    page = fetch.get_text(f"{fetch.NEWS_SITE}/rss/articles/{quote(ident, safe='')}", label="news article",
                          headers=fetch.PAGE_HEADERS)
    m = _SIG_RE.search(page)
    if not m:
        fetch.dump_debug("news_article_nosig", page)
        raise fetch.GoogleNotFound(f"article {ident!r} not found on Google News")
    sg, ts = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
    body = urlencode({"f.req": json.dumps(_garturlreq(ident, sg, ts), separators=(",", ":"))})
    resp = fetch._retrying(lambda: fetch.request(
        "POST", f"{fetch.NEWS_SITE}/_/DotsSplashUi/data/batchexecute", label="news resolve",
        data=body, headers={**fetch.JSON_HEADERS, "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
                            "origin": fetch.NEWS_SITE, "referer": fetch.NEWS_SITE + "/"}))
    link = None
    for line in fetch.strip_xssi(resp.text).splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            chunk = json.loads(line)
        except ValueError:
            continue
        for row in chunk:
            if isinstance(row, list) and len(row) > 2 and row[0] == "wrb.fr" and isinstance(row[2], str):
                try:
                    inner = json.loads(row[2])
                except ValueError:
                    continue
                if isinstance(inner, list) and inner and inner[0] == "garturlres":
                    link = inner[1] if len(inner) > 1 else None
    if not link:
        fetch.dump_debug("news_resolve_nolink", resp.text)
        raise fetch.GoogleUpstreamError("Google News did not return the article URL")
    return {"id": ident, "google_link": f"{fetch.NEWS_SITE}/articles/{ident}", "link": link}


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "top"
    if what == "top":
        out = top_headlines(limit=5)
    elif what == "topic":
        out = topic_headlines(sys.argv[2] if len(sys.argv) > 2 else "TECHNOLOGY", limit=5)
    elif what == "local":
        out = local_headlines(sys.argv[2] if len(sys.argv) > 2 else "New York", limit=5)
    elif what == "resolve":
        out = resolve_link(sys.argv[2])
    else:
        out = search(sys.argv[2] if len(sys.argv) > 2 else "openai", limit=5)
    print(json.dumps(out, indent=2, ensure_ascii=False))
