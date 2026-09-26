"""Live endpoint smoke tests: one call per /google-search/* route against a
running service, with example values proven to return data (2026-09-24).
The listing tooling reads each route's FIRST call from this file's AST as
its working example, so the values stay literals.

Skipped unless GOOGLE_SEARCH_BASE points at a running service:

    ONLY_SCRAPER=google-search python run.py            # or any bottle runner
    GOOGLE_SEARCH_BASE=http://127.0.0.1:6002 python -m pytest google_search/test_endpoints.py -q

The SERP routes mint an identity on first use (a headed Camoufox + the
CapSolver extension, 30-120 s), so the first SERP call is slow.
"""
import os

import pytest

BASE = os.environ.get("GOOGLE_SEARCH_BASE", "").rstrip("/")

pytestmark = pytest.mark.skipif(not BASE, reason="set GOOGLE_SEARCH_BASE to run live endpoint tests")


def call(path, **params):
    from curl_cffi import requests
    resp = requests.get(BASE + path, params=params, timeout=400)
    assert resp.status_code == 200, f"{path} {params} -> {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body, f"{path} returned an empty body"
    return body


def status(path, **params):
    from curl_cffi import requests
    return requests.get(BASE + path, params=params, timeout=400).status_code


# ---- SERP ------------------------------------------------------------------------------------

def test_search():
    body = call("/google-search/search", query="best laptop 2026")
    assert body["organic_results"] and body["organic_results"][0]["title"] and body["organic_results"][0]["link"].startswith("http")
    assert "goto?url=" not in body["organic_results"][0]["link"]
    assert body["related_searches"] and body["people_also_ask"] and body["current_page"] == 1 and body["next"]
    body = call("/google-search/search", query="elon musk", country="US", language="en")
    assert body["knowledge_panel"]["title"] == "Elon Musk"
    body = call("/google-search/search", query="python tutorial", page="2", num="20", time_period="year")
    assert body["current_page"] == 2 and body["organic_results"][0]["position"] == 21


def test_search_light():
    body = call("/google-search/search/light", query="nike shoes", num="50", country="GB")
    assert 30 <= len(body["organic_results"]) <= 50 and body["organic_results"][0]["domain"]


def test_people_also_ask():
    body = call("/google-search/people-also-ask", query="how tall is mount everest")
    assert body["count"] >= 3 and body["questions"][0]["question"]


def test_ai_overview():
    body = call("/google-search/ai-overview", query="how to make pizza")
    assert body["ai_overview"] is not None and "is_loaded" in body["ai_overview"]


def test_images():
    body = call("/google-search/images", query="golden retriever", limit="10", size="large", type="photo")
    assert body["count"] == 10 and body["images"][0]["link"].startswith("http") and body["images"][0]["width"]


def test_videos():
    body = call("/google-search/videos", query="python tutorial", duration="long")
    assert body["videos"] and body["videos"][0]["link"].startswith("http") and body["videos"][0]["duration"]


def test_news():
    body = call("/google-search/news", query="openai", time_period="week", sort="date")
    assert body["articles"] and body["articles"][0]["source"] and body["articles"][0]["date"]


def test_shopping():
    body = call("/google-search/shopping", query="apple watch", min_price="100", max_price="400", limit="20")
    assert body["products"] and body["products"][0]["price"] and body["products"][0]["seller"]


def test_local():
    body = call("/google-search/local", query="pizza in new york", location="New York,New York,United States")
    assert body["places"] and body["places"][0]["name"] and body["places"][0]["rating"]


def test_jobs():
    body = call("/google-search/jobs", query="software engineer jobs", location="Austin, TX")
    assert body["jobs"] and body["jobs"][0]["title"] and body["jobs"][0]["company"]


def test_books():
    body = call("/google-search/books", query="python programming")
    assert body["books"] and body["books"][0]["id"] and body["books"][0]["link"].startswith("https://books.google.com/")


def test_forums():
    body = call("/google-search/forums", query="best mechanical keyboard")
    assert body["threads"] and body["threads"][0]["source"]


def test_identities():
    body = call("/google-search/identities")
    assert "identities" in body


# ---- open surfaces --------------------------------------------------------------------------------

def test_autocomplete():
    body = call("/google-search/autocomplete", query="pyth", client="google")
    assert body["count"] >= 5 and body["suggestions"][0]["text"].startswith("pyth")
    body = call("/google-search/autocomplete", query="pyth", client="chrome")
    assert body["suggestions"][0]["relevance"]


def test_news_feeds():
    body = call("/google-search/news/top-headlines", country="US", language="en", limit="10")
    assert body["count"] == 10 and body["articles"][0]["source"]["name"]
    body = call("/google-search/news/topic", topic="TECHNOLOGY", limit="5")
    assert body["articles"]
    body = call("/google-search/news/local", location="Chicago", limit="5")
    assert body["articles"]
    body = call("/google-search/news/search", query="tesla", country="GB", time_window="7d", limit="5")
    assert body["articles"]
    resolved = call("/google-search/news/resolve-link", article=body["articles"][0]["link"])
    assert resolved["link"].startswith("http") and "news.google.com" not in resolved["link"]


def test_trends():
    body = call("/google-search/trends/trending", country="US", hours="24", limit="10")
    assert body["count"] == 10 and body["trends"][0]["title"] and body["trends"][0]["search_volume"]
    body = call("/google-search/trends/interest-over-time", keywords="python,javascript", timeframe="past_12_months")
    assert body["timeline"] and body["averages"]["python"]
    body = call("/google-search/trends/interest-by-region", keywords="python", country="US", limit="5")
    assert body["regions"][0]["code"].startswith("US-")
    body = call("/google-search/trends/related", keywords="python", type="queries")
    assert body["results"][0]["top"] and body["results"][0]["rising"]


def test_patents():
    body = call("/google-search/patents/search", query="machine learning", num="10", after="2020-01-01", status="granted")
    assert body["patents"] and body["count"] > 100 and body["patents"][0]["id"]
    body = call("/google-search/patents/details", patent="US10000000B2")
    assert body["title"].startswith("Coherent LADAR") and body["inventors"] == ["Joseph Marron"] and body["claims"]


def test_finance():
    body = call("/google-search/finance/quote", symbol="AAPL:NASDAQ")
    assert body["price"] and body["company"]["ceo"] and body["financials"] and body["news"]
    body = call("/google-search/finance/quote", symbol="BTC-USD", include_news="false")
    assert body["type"] == "crypto" and body["price"]
    body = call("/google-search/finance/overview", country="US")
    assert body["indexes"] and body["market_movers"]


def test_validation():
    assert status("/google-search/search", query="x", bogus="1") == 400
    assert status("/google-search/finance/quote", symbol="AAPL") == 404
    assert status("/google-search/trends/interest-over-time", keywords="python", timeframe="bogus") == 400
