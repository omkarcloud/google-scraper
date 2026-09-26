"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. See README.md for every endpoint.
"""
import json
import os

from google_search.news import search as search_news
from google_search.serp import search
from google_search.trends import interest_over_time

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # the full Google results page: organic results, AI overview, People also ask, ...
    save("search_best_laptop_2026.json", search("best laptop 2026"))

    # Google News articles (plain HTTP, no browser)
    save("news_tesla.json", search_news("tesla", limit=20))

    # Google Trends: python vs javascript over the last 12 months
    save("trends_python_vs_javascript.json", interest_over_time(["python", "javascript"]))
