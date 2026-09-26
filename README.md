# 🔍 Google Search Scraper

Google Search Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Google Search data for free.

## ✨ What Can I Get?

- 🔍 **The full Google results page in one call** — organic results, AI Overview, People also ask, ads & top stories
- 🛍️ **12 Google verticals** — Images, Videos, News, Shopping, Local, Jobs, Forums, Books & more
- 📰 **Google News, Trends & Patents** — headlines for any country, interest for up to 5 keywords, full patent text
- 📈 **Live Google Finance** — price, key stats, financials, chart & news for any stock or crypto

## 🎥 Example: A Full Google Results Page

```json
{
  "count": 315000,
  "current_page": 1,
  "search_information": { "query": "best laptop 2026", "total_results": 315000, "time_taken_seconds": 0.23 },
  "organic_results": [
    {
      "position": 3,
      "title": "Best Laptop Deal of the Day: Lock in This Lenovo Gaming ...",
      "link": "https://www.pcmag.com/deals/best-laptop-deal-of-the-day-lenovo-gaming-rig-for-650-off-sept-3",
      "domain": "www.pcmag.com",
      "date": "Sep 3, 2026",
      "snippet": "Lenovo LOQ 15 is an affordable midrange pick. Acer Predator Triton Neo 16 The Best Gaming Laptops for 2026..."
    }
  ],
  "ai_overview": {
    "is_available": true,
    "text": "The best overall laptop for most people in 2026 is the MacBook Air (13-inch, M5) , which starts at $1,099 with double the base storage and powerful neural-accelerated graphics...",
    "sources": [
      { "title": "The Best Laptops for 2026 - PCMag UK", "source": "PCMag UK", "link": "https://uk.pcmag.com/laptops/158/the-best-laptops" }
    ]
  },
  "people_also_ask": [
    { "question": "What laptop should I buy in 2026?", "answer": null, "source": null }
  ],
  "discussions": [
    { "text": "Best laptops of 2026: We tested 90+ to find the top 9", "link": "https://mashable.com/roundup/best-laptops-2026-expert-reviewed", "author": "Mashable", "date": "4 days ago" }
  ]
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Google Search Data — Get It in 3 Steps

1️⃣ Clone and install (the second command downloads Camoufox, the hardened Firefox that opens Google for you):
```bash
git clone https://github.com/omkarcloud/google-scraper
cd google-scraper
python -m pip install -r requirements.txt
python -m camoufox fetch
```

2️⃣ Start the API:
```bash
python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/search?query=best+laptop+2026"
```

```json
{
  "count": 315000,
  "current_page": 1,
  "next": "http://localhost:8000/search?query=best+laptop+2026&page=2",
  "search_information": { "query": "best laptop 2026", "total_results": 315000, "time_taken_seconds": 0.15 },
  "organic_results": [
    {
      "position": 1,
      "title": "Which laptop in 2026 ? : r/SuggestALaptop",
      "link": "https://www.reddit.com/r/SuggestALaptop/comments/1wm2dy7/which_laptop_in_2026/",
      "domain": "www.reddit.com",
      "date": "5 days ago"
    }
  ],
  "ai_overview": {
    "text": "The best overall laptop for most people in 2026 is the MacBook Air (13-inch, M5) , which starts at $1,099 with double the base storage and powerful neural-accelerated graphics...",
    "sources": [
      { "title": "Best Laptops (2026): My Top Recommendations After ... - WIRED", "link": "https://www.wired.com/story/best-laptops/" }
    ]
  },
  "people_also_ask": [{ "question": "What laptop should I buy in 2026?" }]
}
```

All 26 endpoints are now live at `http://localhost:8000`.

The first search can take a few minutes while a browser opens Google and clears its checks; after that, each search takes a second or two.

### 🔓 Google Showing a Captcha?

Google often answers a fresh browser with an "unusual traffic" captcha. The scraper first tries on its own — no key, no cost — and when Google insists, it tells you so. Then:

1. Get a [CapSolver](https://dashboard.capsolver.com/passport/register?inviteCode=lvdYBC4sYKRm) API key (one captcha is solved per ~30 searches).
2. Start the API with it:
   ```bash
   CAPSOLVER_API_KEY=CAP-... python run.py
   ```

For heavy use, add a sticky residential proxy too — Google pauses one IP after a few dozen searches:

```bash
GOOGLE_SEARCH_PROXY="http://user-session-{session}:pass@host:port" CAPSOLVER_API_KEY=CAP-... python run.py
```

`{session}` gets a fresh id for every browser session. See [config.py](config.py) for every option.

Google News, Trends, Patents, Finance and Autocomplete need none of this — they are plain HTTP and work straight away. The Google Search pages work best when you run the scraper on macOS or Windows.

## 📚 Endpoints

26 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Google Search Results | `/search` | Full results page: organic, AI Overview, People also ask, ads, top stories |
| Autocomplete Suggestions | `/autocomplete` | Google's live suggestions for a partial query |
| Search Light | `/search/light` | Organic results only, up to 100 per call |
| AI Overview | `/ai-overview` | Google's AI answer with the sources it cites |
| People Also Ask | `/people-also-ask` | The questions Google shows for a query |
| Images / Videos | `/images`, `/videos` | Image and video results with thumbnails and filters |
| News Tab | `/news` | Google Search's News tab, by relevance or date |
| Shopping | `/shopping` | Products with price, store, rating and delivery |
| Local Places | `/local` | Businesses with rating, reviews, address, hours, phone |
| Jobs / Forums / Books | `/jobs`, `/forums`, `/books` | Job listings, Reddit & forum threads, Google Books |
| Google News | `/news/search`, `/news/top-headlines`, `/news/topic`, `/news/local` | Articles by keyword, country, topic or city |
| Resolve News Link | `/news/resolve-link` | A Google News link turned into the publisher's URL |
| Google Trends | `/trends/trending`, `/trends/interest-over-time`, `/trends/interest-by-region`, `/trends/related` | Trending searches, interest over time and by region, related queries |
| Google Patents | `/patents/search`, `/patents/details` | Patent search and the full patent: claims, citations, family |
| Google Finance | `/finance/quote`, `/finance/overview` | Stock and crypto quotes, market movers and news |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-google-search-scraper-free-200-calls/pricing) — 200 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-google-search-scraper-free-200-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-google-search-scraper-free-200-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/search",
    params={"query": "best laptop 2026"},
)
print(response.json())
```

*Disclosure: the CapSolver link above is a referral link. CapSolver pays us a small commission for those solves. It costs you nothing extra. This helps support the project.*

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Google Search Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://api.whatsapp.com/send?phone=918178804274&text=I%20need%20help%20using%20the%20Google%20Search%20Scraper%20API.)

[![Ask Us by Email about Google Search Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Google%20Search%20Scraper%20API&body=I%20need%20help%20using%20the%20Google%20Search%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/google-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/google-scraper)
