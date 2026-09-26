"""Offline parser tests for google_search: every parser runs against the
captured pages (google_search/fixtures/*.gz, raw pages captured
2026-09-24) and the shape / key facts of the output are asserted.

    cd scrapers && python -m pytest google_search/test_parsers.py -q
"""
import gzip
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_search import autocomplete, fetch, finance, news, patents, refs, serp_parsers as sp, trends  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    with gzip.open(os.path.join(FX, name + ".gz"), "rt", encoding="utf-8", errors="replace") as f:
        return f.read()


# ---- SERP: web -------------------------------------------------------------------------------

def test_web_serp_blocks():
    out = sp.parse_web(load("serp_web.html"))
    info = out["search_information"]
    assert info["query"] == "best laptop 2026"
    assert info["total_results"] == 179
    assert len(out["organic_results"]) == 9
    first = out["organic_results"][0]
    assert first["position"] == 1
    assert first["title"].startswith("Best Laptops (2026)")
    assert first["domain"] == "www.wired.com"
    assert first["link"].startswith("https://www.google.com/goto?url=")
    assert first["snippet"]
    assert out["ads"] and out["ads"][0]["link"] == "https://www.pcmag.com/"
    assert len(out["people_also_ask"]) == 4
    assert out["people_also_ask"][0]["question"].startswith("Which generation")
    assert out["ai_overview"]["is_loaded"] is True
    assert out["ai_overview"]["text"].startswith("The Apple MacBook Air")
    assert "An AI Overview is not available" not in out["ai_overview"]["text"]
    assert len(out["related_searches"]) == 8
    assert out["related_searches"][0]["query"] == "Best laptop 2026 with touch screen"
    assert out["pagination"]["has_next"] and out["pagination"]["next_start"] == 10


def test_web_serp_entity_blocks():
    out = sp.parse_web(load("serp_web_entity.html"))
    kp = out["knowledge_panel"]
    assert kp["title"] == "Elon Musk"
    assert kp["description"].startswith("Elon Reeve Musk")
    assert kp["description_source"]["name"] == "Wikipedia"
    names = [a["name"] for a in kp["attributes"]]
    assert "Born" in names and "Spouse" in names
    assert out["top_stories"] and out["top_stories"][0]["stories"][0]["source"] == "Semafor"
    assert out["inline_videos"] and out["inline_videos"][0]["link"].startswith("https://www.youtube.com/")
    assert out["inline_videos"][0]["duration"] == "1:12"
    assert out["latest_posts"]["heading"] == "Latest posts from Elon Musk"
    assert out["latest_posts"]["posts"][0]["likes"] == 1000
    assert out["discussions"] and out["discussions"][0]["likes"] == 24500
    assert out["featured_results"][0]["source"] == "Instagram"
    assert out["knowledge_carousels"][0]["items"][0]["name"] == "Thank You for Smoking"


def test_web_serp_question():
    out = sp.parse_web(load("serp_web_q.html"))
    assert out["search_information"]["query"] == "how tall is mount everest"
    assert out["organic_results"][0]["domain"] == "en.wikipedia.org"
    assert out["short_videos"] and out["short_videos"][0]["channel"] == "Cleo Abram"
    assert out["discussions"][0]["platform"] == "Reddit"
    # meta-text cites ("7 months ago") are dates, not links; no Google host as a domain
    meta = [r for r in out["organic_results"] if r["displayed_link"] is None]
    assert meta and all(r["date"] for r in meta) and all(r["domain"] is None for r in meta)   # filled after /goto resolution
    # AI overview: the answer only (source cards go to `sources`, with site names)
    aio = out["ai_overview"]
    assert aio["text"].startswith("Mount Everest stands at") and "Not to be confused" not in aio["text"]
    assert [s["source"] for s in aio["sources"]] == ["Wikipedia", "Britannica"]
    assert aio["sources"][0]["title"] == "Mount Everest - Wikipedia"


# ---- SERP: verticals --------------------------------------------------------------------------

def test_images():
    out = sp.parse_images(load("serp_images.html"))
    assert out["search_information"]["total_results"] == 148000000
    assert len(out["images"]) == 50
    first = out["images"][0]
    assert first["link"].startswith("https://blog.ollie.com/") and first["width"] == 1707 and first["height"] == 2560
    assert first["thumbnail"]["link"].startswith("https://encrypted-tbn0.gstatic.com/")
    assert first["source"]["domain"] == "blog.ollie.com" and first["source"]["page_link"].startswith("https://blog.ollie.com/")
    assert first["file_size"] == "522KB"
    assert out["suggested_filters"][0]["query"] == "golden retriever puppy"


def test_videos():
    out = sp.parse_videos(load("serp_videos.html"))
    assert len(out["videos"]) == 10
    v = out["videos"][0]
    assert v["link"].startswith("https://www.youtube.com/watch?v=_uQrJ0TkZlc")
    assert v["platform"] == "YouTube" and v["channel"] == "Programming with Mosh" and v["duration"] == "6:14:07"
    assert out["search_information"]["total_results"] == 11400000


def test_news_tab():
    out = sp.parse_news(load("serp_news.html"))
    assert len(out["articles"]) == 19 and len(out["clusters"]) == 3   # 3 clusters of 4 + 7 standalone cards
    a = out["articles"][0]
    assert a["source"] == "NPR" and a["date"] == "2 hours ago" and a["position"] == 1
    assert out["clusters"][0]["full_coverage_link"].startswith("https://www.google.com/search?")


def test_news_tab_by_date():
    out = sp.parse_news(load("serp_news_by_date.html"))
    assert len(out["articles"]) == 10 and not out["clusters"]
    a = out["articles"][0]
    assert a["id"] and a["title"] and a["source"] and a["date"] and a["link"].startswith("https://www.google.com/goto?")


def test_web_serp_german():
    out = sp.parse_web(load("serp_web_de.html"))
    assert len(out["organic_results"]) >= 5 and out["organic_results"][0]["domain"]
    assert out["related_searches"]


def test_shopping():
    out = sp.parse_shopping(load("serp_shopping.html"))
    assert len(out["products"]) == 40
    p = out["products"][0]
    assert p["id"] == "13765427821893058841" and p["price"] == 99.99 and p["currency"] == "$"
    assert p["seller"] == "Walmart" and p["rating"] == 4.5 and p["review_count"] == 27000
    assert p["link"].endswith("/shopping/product/13765427821893058841")


def test_wall_classification():
    from google_search import identity
    # the botguard wall (plain curl, 2026-09-25) vs real pages of every vertical
    assert identity.classify(load("serp_wall.html")) == "wall"
    for name in ("serp_web.html", "serp_images.html", "serp_news.html", "serp_shopping.html", "serp_videos.html",
                 "serp_local.html", "serp_jobs.html", "serp_books.html", "serp_forums.html"):
        assert identity.classify(load(name)) == "ok", name
    # an EMPTY shopping result: #search but no #rso, enablejs <noscript> — not a wall
    empty = load("serp_shopping_no_rso.html")
    assert identity.classify(empty) == "ok"
    assert sp.parse_shopping(empty)["products"] == []
    assert identity.classify("", "https://www.google.com/sorry/index?continue=x") == "sorry"


def test_local():
    out = sp.parse_local(load("serp_local.html"))
    assert len(out["places"]) == 20 and out["search_information"]["total_results"] == 219
    p = out["places"][0]
    assert p["name"] == "NY Pie (Capitol View)" and p["place_id"].startswith("ChIJ")
    assert p["address"].endswith("TN 37203") and p["phone"] == "(615) 439-9211" and p["category"] == "Pizza restaurant"
    assert p["rating"] == 4.7 and p["review_count"] == 29 and p["website"].startswith("https://www.nypienashville.com")
    assert out["pagination"]["next_start"] == 20


def test_jobs():
    out = sp.parse_jobs(load("serp_jobs.html"))
    assert len(out["jobs"]) == 10
    j = out["jobs"][0]
    assert j["id"] == "JXI7nUMxQG_UZX9_AAAAAA==" and j["company"] == "Zipliens" and j["via"] == "LinkedIn"
    assert j["location"] == "Franklin, TN" and j["link"].startswith("https://www.google.com/search?ibp=htl;jobs")
    assert j["apply_links"]


def test_books():
    out = sp.parse_books(load("serp_books.html"))
    assert len(out["books"]) == 10
    b = out["books"][0]
    assert b["id"] == "g1FczgEACAAJ" and b["authors"] == ["Michael Learn"]
    assert b["link"].startswith("https://books.google.com/books?id=g1FczgEACAAJ")


def test_forums():
    out = sp.parse_forums(load("serp_forums.html"))
    assert len(out["threads"]) == 10
    t = out["threads"][0]
    assert t["source"] == "Reddit · r/buildapc" and t["reply_count"] == 250 and t["date"] == "1 month ago"


# ---- open surfaces ------------------------------------------------------------------------------

def test_autocomplete_formats():
    gws = fetch.parse_json(load("autocomplete_gwswiz.txt"), "t")
    items = autocomplete._parse_gws(gws)
    assert items[0]["text"] == "python" and items[0]["type"] == "query"
    entity = next(i for i in items if i["entity"])
    assert entity["text"] == "python snake" and entity["entity"]["title"] == "Python" and entity["entity"]["image"].startswith("https://")
    chrome, verbatim = autocomplete._parse_chrome_like(fetch.parse_json(load("autocomplete_chrome.txt"), "t"), "chrome")
    assert chrome[0]["text"] == "python" and chrome[0]["relevance"] == 1250 and verbatim == 851


def test_news_rss():
    feed = news._parse_feed(load("news_rss_top.xml"), "t")
    assert feed["feed_title"] == "Top stories - Google News"
    assert len(feed["articles"]) >= 20
    a = feed["articles"][0]
    assert a["id"].startswith("CBMi") and a["source"]["name"] and a["published_at"].endswith("Z")
    assert a["related_articles"] and a["related_articles"][0]["source"]


def test_trending_page():
    blocks = fetch.af_blocks(load("trends_trending_us.html"))
    rows = next(b[1] for b in blocks.values() if isinstance(b, list) and len(b) > 1 and isinstance(b[1], list) and b[1])
    assert rows[0][0] == "jonathan taylor thomas" and rows[0][6] == 50000 and rows[0][10] == [4]
    assert trends.TRENDING_CATEGORIES[4] == "Entertainment"


def test_patent_search_rows():
    data = json.loads(load("patents_search.json"))
    rows = [patents._row(i) for i in data["results"]["cluster"][0]["result"]]
    assert rows[0]["id"] == "US12438891B1" and rows[0]["assignees"] == ["Splunk Inc."]
    assert rows[0]["dates"]["grant"] == "2025-10-07" and rows[0]["figures"][0]["image"].startswith("https://patentimages")
    assert data["results"]["total_num_results"] == 119420


def test_patent_page_markup():
    root = patents._lxml(load("patent_us10000000b2.html"))
    assert patents._prop(root, "title").startswith("Coherent LADAR")
    assert patents._props(root, "inventor") == ["Joseph Marron"]
    assert patents._reference_rows(root, "backwardReferences")[0]["id"] == "US5093563A"
    assert any(e["type"] == "litigation" for e in patents._events(root))


def test_finance_quote_blocks():
    blocks = fetch.af_blocks(load("finance_quote_aapl.html"))
    q = finance._quote_block(blocks, "AAPL:NASDAQ")
    assert q is not None and q[14] == "Apple Inc" and q[2] == 341.075
    about = finance._about_block(blocks)
    assert about[5] == "John Ternus" and about[6] == 166000
    analysts = finance._analyst_block(blocks)
    assert analysts[0][7] == "Buy" and analysts[0][6] == 30
    assert finance._financials(blocks)[0]["revenue"] == 109417000000
    rows = [finance._ticker(r) for r in finance._find_rows(blocks["ds:5"])]
    assert rows[0]["symbol"] == "NVDA:NASDAQ" and rows[0]["price"] == 225.51


# ---- refs ---------------------------------------------------------------------------------------

def test_refs():
    assert refs.resolve_query("https://www.google.com/search?q=nike+shoes&gl=de") == "nike shoes"
    assert refs.params_of("https://www.google.co.uk/search?q=x&gl=uk&start=20")["start"] == "20"
    assert refs.resolve_article("https://news.google.com/rss/articles/CBMiabcdefghijklmnopqrstuvwxyz?oc=5") == "CBMiabcdefghijklmnopqrstuvwxyz"
    assert refs.resolve_topic("https://news.google.com/topics/CAAqJggKIiBDQkFT/sections/CAQi?hl=en") == "CAAqJggKIiBDQkFT"
    assert refs.uule_of("Austin,Texas,United States") == "w+CAIQICIaQXVzdGluLFRleGFzLFVuaXRlZCBTdGF0ZXM="
    assert patents.resolve_patent("https://patents.google.com/patent/US10000000B2/de") == ("US10000000B2", "de")
    assert finance.resolve_symbol("https://www.google.com/finance/quote/AAPL:NASDAQ?window=1M") == "AAPL:NASDAQ"
    with pytest.raises(ValueError):
        refs.resolve_query("https://example.com/search?q=x")
