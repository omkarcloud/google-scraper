"""Google Trends over the surfaces trends.google.com still serves to a
cookie-less client (validated 2026-09-24, plain curl_cffi):

  * TRENDING NOW   GET /trending?geo=US&hl=en-US[&hours=24&category=N]
                   SSR page; ds:0 = [null, [[title, null, geo, [start_ts],
                   null, null, search_volume, null, percent_increase,
                   [related queries], [category ids], [[news id, lang,
                   geo]...], canonical_title], ...]]
                   GET /trending/rss?geo=US — the same list (10 items) with
                   headline / source / picture per trend (merged by title)
  * EXPLORE        GET /trends/explore?q=…   -> HTTP 429 but it SETS the
                   NID cookie every /trends/api/* call needs (session jar)
                   GET /trends/api/explore?hl&tz&req={"comparisonItem":[...],
                   "category":N,"property":""}  -> widgets, each with a
                   `request` and a `token`
                   GET /trends/api/widgetdata/multiline|comparedgeo|
                   relatedsearches?hl&tz&req=<widget.request>&token=<token>
                   All answer `)]}',` + JSON. The old api/dailytrends and
                   api/realtimetrends endpoints are gone (404).

Timeframes are the site's own tokens ("now 1-d", "today 12-m", "today 5-y",
"all", or "YYYY-MM-DD YYYY-MM-DD").
"""
import json
import os
import re
import sys
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_search import fetch  # noqa: E402
from google_search.shared import at, clean_text, iso_from_ts, locale_tag, to_int  # noqa: E402

TIMEFRAMES = {
    "past_hour": "now 1-H", "past_4_hours": "now 4-H", "past_day": "now 1-d", "past_7_days": "now 7-d",
    "past_30_days": "today 1-m", "past_90_days": "today 3-m", "past_12_months": "today 12-m",
    "past_5_years": "today 5-y", "all": "all",
}
PROPERTIES = {"web": "", "images": "images", "news": "news", "youtube": "youtube", "shopping": "froogle"}
RESOLUTIONS = {"country": "COUNTRY", "region": "REGION", "city": "CITY", "dma": "DMA"}
TRENDING_HOURS = (4, 24, 48, 168)
TRENDING_CATEGORIES = {
    1: "Autos and Vehicles", 2: "Beauty and Fashion", 3: "Business and Finance", 4: "Entertainment",
    5: "Food and Drink", 6: "Games", 7: "Health", 8: "Hobbies and Leisure", 9: "Jobs and Education",
    10: "Law and Government", 11: "Other", 12: "Pets and Animals", 14: "Politics", 15: "Science",
    16: "Shopping", 17: "Sports", 18: "Technology", 19: "Travel and Transportation", 20: "Climate",
}
MAX_KEYWORDS = 5
_HT = "{https://trends.google.com/trending/rss}"
_DATE_RANGE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{4}-\d{2}-\d{2}$")
_SESSION_KEY = "trends"


def timeframe_token(value):
    """A public timeframe name, a site token or a 'YYYY-MM-DD YYYY-MM-DD' range."""
    value = (value or "past_12_months").strip()
    low = value.lower().replace("-", "_").replace(" ", "_")
    if low in TIMEFRAMES:
        return TIMEFRAMES[low]
    if value in TIMEFRAMES.values() or _DATE_RANGE_RE.match(value) or re.match(r"^(now|today) \d+-[HdmyY]$", value):
        return value
    raise ValueError(f"timeframe must be one of {', '.join(TIMEFRAMES)} or a 'YYYY-MM-DD YYYY-MM-DD' range")


# ---- trending now -------------------------------------------------------------------

def _trending_rss(geo, language):
    text = fetch.get_text(f"{fetch.TRENDS_SITE}/trending/rss", params={"geo": geo, "hl": language},
                          label="trends rss", headers=fetch.PAGE_HEADERS)
    try:
        root = ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError:
        return {}
    out = {}
    for item in root.iter("item"):
        title = clean_text(item.findtext("title"))
        if not title:
            continue
        news = []
        for n in item.findall(f"{_HT}news_item"):
            news.append({
                "title": clean_text(n.findtext(f"{_HT}news_item_title")),
                "link": (n.findtext(f"{_HT}news_item_url") or "").strip() or None,
                "source": clean_text(n.findtext(f"{_HT}news_item_source")),
                "image": (n.findtext(f"{_HT}news_item_picture") or "").strip() or None,
                "snippet": clean_text(n.findtext(f"{_HT}news_item_snippet")),
            })
        out[title.lower()] = {
            "approximate_traffic": clean_text(item.findtext(f"{_HT}approx_traffic")),
            "image": (item.findtext(f"{_HT}picture") or "").strip() or None,
            "image_source": clean_text(item.findtext(f"{_HT}picture_source")),
            "news": news,
        }
    return out


def trending(country="US", language="en", hours=24, category=None, limit=50, include_news=True):
    """What is trending now in a country (the trends.google.com/trending list)."""
    geo = (country or "US").upper()
    hl = locale_tag(language, geo)
    params = {"geo": geo, "hl": hl, "hours": hours}
    if category:
        params["category"] = category
    html, _ = fetch.get_html(f"{fetch.TRENDS_SITE}/trending?{urlencode(params)}", label="trends trending",
                             key=_SESSION_KEY)
    blocks = fetch.af_blocks(html, "trending")
    rows = None
    for data in blocks.values():
        candidate = at(data, 1)
        if isinstance(candidate, list) and candidate and isinstance(candidate[0], list) and isinstance(at(candidate, 0, 0), str):
            rows = candidate
            break
    if rows is None:
        fetch.dump_debug("trending_norows", html)
        raise fetch.GoogleUpstreamError("trending page carried no trend list")
    extra = _trending_rss(geo, hl) if include_news else {}
    items = []
    for row in rows:
        title = at(row, 0)
        if not isinstance(title, str):
            continue
        cats = [c for c in (at(row, 10) or []) if isinstance(c, int)]
        merged = extra.get(title.lower()) or {}
        items.append({
            "position": len(items) + 1,
            "title": title,
            "canonical_title": at(row, 12) if isinstance(at(row, 12), str) else title,
            "country": at(row, 2) or geo,
            "started_at": iso_from_ts(at(row, 3, 0)),
            "search_volume": to_int(at(row, 6)),
            "percent_increase": to_int(at(row, 8)),
            "approximate_traffic": merged.get("approximate_traffic"),
            "categories": [{"id": c, "name": TRENDING_CATEGORIES.get(c)} for c in cats],
            "related_queries": [q for q in (at(row, 9) or []) if isinstance(q, str) and q.lower() != title.lower()],
            "image": merged.get("image"),
            "image_source": merged.get("image_source"),
            "news": merged.get("news") or [],
            "news_count": len(at(row, 11) or []),
        })
    items = items[:limit]
    return {"country": geo, "language": hl, "hours": hours,
            "category": {"id": category, "name": TRENDING_CATEGORIES.get(category)} if category else None,
            "count": len(items), "trends": items}


# ---- explore ---------------------------------------------------------------------------

def _tz():
    return 0


def _api(path, params, label):
    """One /trends/api call; a 429 on the first hop means the session has no
    NID cookie yet — load the explore page (which sets it) and retry once."""
    url = f"{fetch.TRENDS_SITE}{path}?{urlencode(params)}"
    headers = {**fetch.JSON_HEADERS, "referer": f"{fetch.TRENDS_SITE}/trends/explore"}

    def once():
        sess = fetch._session(_SESSION_KEY)
        try:
            resp = sess.get(url, headers=headers, timeout=fetch.TIMEOUT)
        except Exception as e:
            raise fetch.GoogleUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
        if resp.status_code == 429 and not getattr(sess, "_trends_primed", False):
            # the /trends/api calls 429 until the session carries the NID
            # cookie the explore PAGE sets (that page itself answers 429)
            sess._trends_primed = True
            try:
                sess.get(f"{fetch.TRENDS_SITE}/trends/explore?q=google&geo=US&hl=en-US",
                         headers=fetch.PAGE_HEADERS, timeout=fetch.TIMEOUT)
                resp = sess.get(url, headers=headers, timeout=fetch.TIMEOUT)
            except Exception as e:
                raise fetch.GoogleUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
        if resp.status_code == 400:
            raise fetch.GoogleBadRequest(f"{label}: Google Trends rejected the request")
        if resp.status_code in (403, 429):
            fetch.dump_debug(f"blocked_{label}", resp.text)
            raise fetch.GoogleBlocked(f"HTTP {resp.status_code} on {label}")
        if resp.status_code != 200:
            raise fetch.GoogleUpstreamError(f"HTTP {resp.status_code} on {label}")
        return fetch.parse_json(resp.text, label)

    return fetch._retrying(once, _SESSION_KEY)


def _explore(keywords, geo, timeframe, category, prop, hl):
    req = {
        "comparisonItem": [{"keyword": k, "geo": geo, "time": timeframe} for k in keywords],
        "category": category or 0,
        "property": PROPERTIES.get(prop, prop or ""),
    }
    data = _api("/trends/api/explore", {"hl": hl, "tz": _tz(), "req": json.dumps(req, separators=(",", ":"))},
                "trends explore")
    widgets = data.get("widgets") if isinstance(data, dict) else None
    if not widgets:
        raise fetch.GoogleUpstreamError("trends explore returned no widgets")
    return widgets


def _widget_data(widget, endpoint, hl, label):
    params = {"hl": hl, "tz": _tz(), "req": json.dumps(widget["request"], separators=(",", ":")),
              "token": widget["token"]}
    return _api(f"/trends/api/widgetdata/{endpoint}", params, label)


def _widgets(widgets, wid):
    return [w for w in widgets if w.get("id") == wid or str(w.get("id", "")).startswith(wid + "_")]


def _context(keywords, geo, timeframe, category, prop, hl):
    return {"keywords": keywords, "country": geo or "worldwide", "timeframe": timeframe,
            "category": category or 0, "property": prop or "web", "language": hl}


def interest_over_time(keywords, country=None, timeframe="past_12_months", category=None, property="web",
                       language="en"):
    """Relative search interest (0-100) over time for up to 5 keywords."""
    geo = (country or "").upper()
    tf = timeframe_token(timeframe)
    hl = locale_tag(language, geo or "US")
    widgets = _explore(keywords, geo, tf, category, property, hl)
    ts = _widgets(widgets, "TIMESERIES")
    if not ts:
        raise fetch.GoogleNotFound("no interest-over-time data for these keywords")
    data = _widget_data(ts[0], "multiline", hl, "trends timeseries")
    default = data.get("default") or {}
    points = []
    for p in default.get("timelineData") or []:
        values = p.get("value") or []
        points.append({
            "time": iso_from_ts(p.get("time")),
            "label": p.get("formattedTime"),
            "values": {keywords[i]: (values[i] if i < len(values) else None) for i in range(len(keywords))},
            "is_partial": bool(p.get("isPartial")),
        })
    averages = default.get("averages") or []
    out = _context(keywords, geo, tf, category, property, hl)
    out.update({"averages": {keywords[i]: (averages[i] if i < len(averages) else None) for i in range(len(keywords))},
                "count": len(points), "timeline": points})
    return out


def interest_by_region(keywords, country=None, timeframe="past_12_months", resolution="region", category=None,
                       property="web", language="en", limit=100):
    """Relative search interest by country / region / city / DMA."""
    geo = (country or "").upper()
    # Worldwide data only breaks down by country (Google answers HTTP 500 to a
    # worldwide REGION / CITY / DMA request — the site's own map does the same);
    # DMA (metro areas) exists only for the US.
    if not geo:
        resolution = "country"
    elif resolution == "dma" and geo != "US":
        raise fetch.GoogleBadRequest("resolution=dma (metro areas) is only available for country=US")
    tf = timeframe_token(timeframe)
    hl = locale_tag(language, geo or "US")
    widgets = _explore(keywords, geo, tf, category, property, hl)
    geo_widgets = _widgets(widgets, "GEO_MAP")
    if not geo_widgets:
        raise fetch.GoogleNotFound("no regional data for these keywords")
    widget = geo_widgets[0]
    widget["request"]["resolution"] = RESOLUTIONS.get(resolution, "REGION")
    widget["request"]["includeLowSearchVolumeGeos"] = False
    data = _widget_data(widget, "comparedgeo", hl, "trends regions")
    rows = []
    for g in (data.get("default") or {}).get("geoMapData") or []:
        values = g.get("value") or []
        rows.append({
            "code": g.get("geoCode"),
            "name": g.get("geoName"),
            "values": {keywords[i]: (values[i] if i < len(values) else None) for i in range(len(keywords))},
            "max_value_index": g.get("maxValueIndex"),
            "has_data": bool((g.get("hasData") or [False])[0]) if g.get("hasData") else None,
            "coordinates": ({"latitude": g["coordinates"].get("lat"), "longitude": g["coordinates"].get("lng")}
                            if isinstance(g.get("coordinates"), dict) else None),
        })
    rows = rows[:limit]
    out = _context(keywords, geo, tf, category, property, hl)
    out.update({"resolution": resolution, "count": len(rows), "regions": rows})
    return out


def _related_rows(section, kind):
    out = []
    for r in (section or {}).get("rankedKeyword") or []:
        topic = r.get("topic") if isinstance(r.get("topic"), dict) else None
        out.append({
            "query": r.get("query") if kind == "queries" else None,
            "topic": ({"id": topic.get("mid"), "title": topic.get("title"), "type": topic.get("type")}
                      if topic else None),
            "value": r.get("value"),
            "formatted_value": r.get("formattedValue"),
            "link": (fetch.TRENDS_SITE + r["link"]) if isinstance(r.get("link"), str) and r["link"].startswith("/") else r.get("link"),
        })
    return out


def related(keywords, kind="queries", country=None, timeframe="past_12_months", category=None, property="web",
            language="en"):
    """Related queries or topics (top + rising) per keyword."""
    geo = (country or "").upper()
    tf = timeframe_token(timeframe)
    hl = locale_tag(language, geo or "US")
    wid = "RELATED_QUERIES" if kind == "queries" else "RELATED_TOPICS"
    if kind == "topics" and len(keywords) > 1:
        # a comparison explore carries no RELATED_TOPICS widgets: one explore per keyword
        explores = fetch.run_parallel([lambda k=k: _explore([k], geo, tf, category, property, hl) for k in keywords])
        wanted = [w for ws in explores for w in _widgets(ws, wid)[:1]]
    else:
        wanted = _widgets(_explore(keywords, geo, tf, category, property, hl), wid)
    if not wanted:
        raise fetch.GoogleNotFound(f"no related {kind} for these keywords")
    results = fetch.run_parallel([lambda w=w: _widget_data(w, "relatedsearches", hl, f"trends related {kind}")
                                  for w in wanted])
    per_keyword = []
    for i, (w, data) in enumerate(zip(wanted, results)):
        ranked = (data.get("default") or {}).get("rankedList") or []
        keyword = keywords[i] if i < len(keywords) else at(w, "bullets", 0, "text")
        per_keyword.append({
            "keyword": keyword,
            "top": _related_rows(ranked[0] if ranked else None, kind),
            "rising": _related_rows(ranked[1] if len(ranked) > 1 else None, kind),
        })
    out = _context(keywords, geo, tf, category, property, hl)
    out.update({"type": kind, "results": per_keyword})
    return out


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "trending"
    if what == "trending":
        out = trending(limit=5)
    elif what == "iot":
        out = interest_over_time(sys.argv[2:] or ["python", "javascript"])
    elif what == "region":
        out = interest_by_region(sys.argv[2:] or ["python"], country="US", limit=5)
    else:
        out = related(sys.argv[2:] or ["python"], kind=what)
    print(json.dumps(out, indent=2, ensure_ascii=False)[:6000])
