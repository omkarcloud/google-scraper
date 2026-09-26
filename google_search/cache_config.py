"""Cache TTL per /google-search/* endpoint (cache.py, keyed on the
validated params — marshmallow fills the defaults, so `?page=1` and no
`page` share a row, and the id-form / link-form of a RefField share a row
too).

SERPs are expensive (a minted identity serves ~40) and rankings move
slowly, so they cache for hours; news / trends / finance move fast;
patents and suggestions barely move."""
from datetime import timedelta

# --- SERP verticals (identity-backed) --------------------------------------
SEARCH_CACHE = timedelta(hours=6)
SEARCH_LIGHT_CACHE = timedelta(hours=6)
PAA_CACHE = timedelta(hours=12)
AI_OVERVIEW_CACHE = timedelta(hours=12)
IMAGES_CACHE = timedelta(hours=12)
VIDEOS_CACHE = timedelta(hours=6)
SERP_NEWS_CACHE = timedelta(minutes=30)
SHOPPING_CACHE = timedelta(hours=3)
LOCAL_CACHE = timedelta(hours=12)
JOBS_CACHE = timedelta(hours=3)
BOOKS_CACHE = timedelta(days=1)
FORUMS_CACHE = timedelta(hours=12)

# --- open surfaces ----------------------------------------------------------
AUTOCOMPLETE_CACHE = timedelta(hours=12)
NEWS_FEED_CACHE = timedelta(minutes=15)
NEWS_SEARCH_CACHE = timedelta(minutes=15)
NEWS_RESOLVE_CACHE = timedelta(days=30)
TRENDING_CACHE = timedelta(minutes=15)
TRENDS_CACHE = timedelta(hours=6)
PATENT_SEARCH_CACHE = timedelta(days=1)
PATENT_CACHE = timedelta(days=7)
FINANCE_QUOTE_CACHE = timedelta(minutes=5)
FINANCE_OVERVIEW_CACHE = timedelta(minutes=5)
