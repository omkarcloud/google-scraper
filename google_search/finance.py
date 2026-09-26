"""Google Finance (www.google.com/finance), plain curl_cffi, no cookies
(validated 2026-09-24). Every page is server-rendered: the data sits in
AF_initDataCallback blocks whose ds:N numbering shifts between pages, so
blocks are recognised by SHAPE, not by number:

  quote block     [[[[null,[SYM,EXCH]], null, price, mid, low, high,
                  prev_close, ., change, ., change_pct, ., currency,
                  "SYM:EXCH", name, open, market_cap, ., industry, <ticker
                  row>, …]]] — <ticker row> = [mid, [SYM,EXCH], name, kind,
                  currency, [last, change, change_pct,…], null, prev_close,
                  colour, country, sector mid, [ts], timezone, utc_offset, …,
                  [after-hours last, change, pct], …, "SYM:EXCH", …]
  about block     [[[mid, name, description, [city, region, country, cc,
                  street], [y,m,d founded], ceo, employees, market_cap, open,
                  price, high, low, 52w_high, 52w_low, ., currency, pe,
                  dividend_yield, volume, eps, beta, shares, ., currency,
                  exchange, …, wikipedia, …, industry]]]
  analysts        [[name, currency, target_low, target_high, target_avg,
                  upside_pct, analyst_count, consensus, buy, hold, sell, …],
                  [[…, analyst, firm, rating, date, link, …]…]]
  chart blocks    [[[[SYM,EXCH], mid, currency, [[window], null|rows,
                  [[close, open, high, low, "ISO time", volume]…]]]]]
  financials      [[[[[year, quarter, [revenue, net_income, eps, …]]…]…]]]
  news blocks     [[[link, title, source, favicon, ts, …, snippet]…]]
  similar         [[[<ticker row>]…]]

  * QUOTE     GET /finance/quote/<SYM:EXCH>   (an unknown symbol 404s)
  * OVERVIEW  GET /finance/beta?hl=&gl=       the front page: indexes, equity
                                             sectors, market movers, news (every
                                             /finance/markets/<tab> path redirects
                                             here since the 2026 "beta" redesign)
"""
import json
import os
import re
import sys
from urllib.parse import quote as urlquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_search import fetch  # noqa: E402
from google_search.shared import at, clean_text, iso_date, iso_from_ts, to_float, to_int  # noqa: E402

_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^_]{1,20}:[A-Z0-9_\-]{1,20}$", re.I)


def resolve_symbol(value):
    """'AAPL:NASDAQ' | 'aapl:nasdaq' | google.com/finance/quote/AAPL:NASDAQ?x
    -> 'AAPL:NASDAQ'. A bare ticker without exchange is accepted and looked
    up through Google's redirect."""
    value = (value or "").strip()
    if "google." in value or value.startswith(("http://", "https://")):
        m = re.search(r"/finance/(?:beta/)?quote/([^/?#]+)", value)
        if not m:
            raise ValueError("finance link must look like google.com/finance/quote/AAPL:NASDAQ")
        value = m.group(1)
    value = value.strip().upper()
    if not value or " " in value:
        raise ValueError("symbol must be TICKER:EXCHANGE (e.g. AAPL:NASDAQ, BTC-USD, EURUSD)")
    return value


def _is_ticker_row(row):
    """Stock / index rows carry [SYM, EXCH] at [1]; currency and crypto pair
    rows carry None there and the pair descriptor at [15]."""
    if not (isinstance(row, list) and len(row) > 20 and isinstance(at(row, 2), str)):
        return False
    if isinstance(at(row, 1), list) and len(row[1]) == 2 and isinstance(at(row, 1, 0), str):
        return True
    return row[1] is None and isinstance(at(row, 15), list) and isinstance(at(row, 21), str)


def _pair(descriptor):
    if not isinstance(descriptor, list) or len(descriptor) < 4:
        return None
    return {"base": descriptor[0], "quote": descriptor[1], "base_name": descriptor[2], "quote_name": descriptor[3],
            "type": "crypto" if at(descriptor, 6) == 2 else "currency"}


def _ticker(row):
    """The recurring ticker row -> a compact quote."""
    if not _is_ticker_row(row):
        return None
    pair = _pair(at(row, 15)) if row[1] is None else None
    if pair:
        sym, exch, symbol = pair["base"], None, at(row, 21)
    else:
        sym, exch = row[1][0], row[1][1]
        symbol = f"{sym}:{exch}"
    last = at(row, 5) if isinstance(at(row, 5), list) else []
    after = at(row, 17) if isinstance(at(row, 17), list) and len(at(row, 17)) >= 3 else []
    return {
        "symbol": symbol,
        "ticker": sym,
        "exchange": exch,
        "name": row[2],
        "link": f"{fetch.SITE}/finance/quote/{symbol}",
        "currency": at(row, 4) or (pair["quote"] if pair else None),
        "pair": pair,
        "price": to_float(at(last, 0)),
        "change": to_float(at(last, 1)),
        "change_percent": to_float(at(last, 2)),
        "previous_close": to_float(at(row, 7)),
        "country": at(row, 9),
        "timezone": at(row, 12),
        "quoted_at": iso_from_ts(at(row, 11, 0)),
        "after_hours": ({"price": to_float(at(after, 0)), "change": to_float(at(after, 1)),
                         "change_percent": to_float(at(after, 2)), "quoted_at": iso_from_ts(at(row, 18, 0))}
                        if after else None),
        "is_index": at(row, 3) == 1,
    }


def _find_rows(data, depth=0, out=None):
    """Every ticker row anywhere inside a block (document order)."""
    if out is None:
        out = []
    if depth > 8 or not isinstance(data, list):
        return out
    if _is_ticker_row(data):
        out.append(data)
        return out
    for item in data:
        _find_rows(item, depth + 1, out)
    return out


def _quote_block(blocks, symbol):
    for data in blocks.values():
        head = at(data, 0, 0)
        if isinstance(head, list) and isinstance(at(head, 0), list) and len(head) > 19 and isinstance(at(head, 13), str) \
                and isinstance(at(head, 14), str) and _is_ticker_row(at(head, 19)):
            if at(head, 13) == symbol or symbol is None:
                return head
    return None


def _find_about_pair(blocks):
    """The about block of a currency / crypto page: its description sits in
    a nested [code, name, description] list instead of at index 2."""
    for data in blocks.values():
        head = at(data, 0, 0)
        if isinstance(head, list) and len(head) > 40 and head[1] is None and isinstance(at(head, 0), str) \
                and any(isinstance(x, list) and len(x) >= 3 and isinstance(at(x, 2), str) and len(at(x, 2)) > 40 for x in head):
            return [[[x for x in head if isinstance(x, list) and len(x) >= 3 and isinstance(at(x, 2), str)]]]
    return None


def _about_block(blocks):
    for data in blocks.values():
        head = at(data, 0, 0)
        if isinstance(head, list) and len(head) > 30 and isinstance(at(head, 2), str) and isinstance(at(head, 3), list) and len(head[2]) > 40:
            return head
    return None


def _analyst_block(blocks):
    for data in blocks.values():
        head = at(data, 0)
        if isinstance(head, list) and len(head) >= 11 and isinstance(at(head, 0), str) and isinstance(at(head, 7), str) \
                and at(head, 7) in ("Buy", "Hold", "Sell", "Strong Buy", "Strong Sell") and isinstance(at(data, 1), list):
            return data
    return None


def _news_blocks(blocks):
    out = []
    for key, data in blocks.items():
        rows = at(data, 0)
        if not isinstance(rows, list) or not rows:
            continue
        first = rows[0]
        if isinstance(first, list) and isinstance(at(first, 0), str) and at(first, 0).startswith("http") \
                and isinstance(at(first, 1), str) and isinstance(at(first, 2), str):
            out.append((key, rows))
    return out


def _news(rows):
    items, seen = [], set()
    for r in rows:
        link = at(r, 0)
        if not isinstance(link, str) or link in seen:
            continue
        seen.add(link)
        snippet = next((x for x in r[8:] if isinstance(x, str) and len(x) > 40 and not x.startswith("http")), None)
        items.append({"title": clean_text(at(r, 1)), "link": link, "source": clean_text(at(r, 2)),
                      "published_at": iso_from_ts(at(r, 4)), "thumbnail": at(r, 3) if isinstance(at(r, 3), str) and "faviconV2" not in at(r, 3) else None,
                      "snippet": clean_text(snippet)})
    return items


def _chart_blocks(blocks):
    """Chart series: [[[[SYM,EXCH], mid, currency, [[window], rows|null, [[close, open, high, low, time, volume]…]]]]]."""
    out = []
    for key, data in blocks.items():
        head = at(data, 0, 0)
        if not (isinstance(head, list) and isinstance(at(head, 0), list) and len(head[0]) == 2 and isinstance(at(head, 2), str)):
            continue
        series = at(head, 3, 2) or at(head, 3, 1)
        if not isinstance(series, list) or not series:
            continue
        points = []
        for p in series:
            if isinstance(p, list) and len(p) >= 5 and isinstance(at(p, 4), str):
                points.append({"time": p[4], "close": to_float(p[0]), "open": to_float(p[1]), "high": to_float(p[2]),
                               "low": to_float(p[3]), "volume": to_int(at(p, 5))})
            elif isinstance(p, list) and isinstance(at(p, 0), list) and isinstance(at(p, 1), list):
                d = p[0]
                points.append({"time": (f"{iso_date(d)}T{int(at(d, 3) or 0):02d}:{int(at(d, 4) or 0):02d}:00" if iso_date(d) else None),
                               "close": to_float(at(p, 1, 0)), "change": to_float(at(p, 1, 1)),
                               "change_percent": to_float(at(p, 1, 2)), "volume": to_int(at(p, 2))})
        if points:
            out.append(points)
    return out


def _financials(blocks):
    for data in blocks.values():
        periods = at(data, 0, 0, 0)
        if isinstance(periods, list) and periods and isinstance(at(periods, 0), list) and isinstance(at(periods, 0, 0), int) \
                and 1990 < at(periods, 0, 0) < 2100 and isinstance(at(periods, 0, 2), list):
            out = []
            for p in periods:
                v = at(p, 2) or []
                out.append({
                    "year": at(p, 0), "quarter": at(p, 1), "period_end": iso_date(at(v, 17)),
                    "currency": at(v, 16),
                    "revenue": to_float(at(v, 0)), "net_income": to_float(at(v, 1)), "eps": to_float(at(v, 2)),
                    "net_profit_margin": to_float(at(v, 3)), "operating_income": to_float(at(v, 4)),
                    "net_change_in_cash": to_float(at(v, 5)), "cash_and_short_term_investments": to_float(at(v, 7)),
                    "total_assets": to_float(at(v, 23)), "total_liabilities": to_float(at(v, 24)),
                    "total_equity": to_float(at(v, 25)), "shares_outstanding": to_float(at(v, 27)),
                    "cash_from_operations": to_float(at(v, 28)), "cash_from_investing": to_float(at(v, 29)),
                    "cash_from_financing": to_float(at(v, 30)), "free_cash_flow": to_float(at(v, 31)),
                    "return_on_assets": to_float(at(v, 33)), "return_on_capital": to_float(at(v, 34)),
                    "price_to_book": to_float(at(v, 18)), "effective_tax_rate": to_float(at(v, 21)),
                })
            return out
    return []


def quote(symbol, include_news=True, include_financials=True, include_chart=True):
    """Everything on a quote page."""
    sym = resolve_symbol(symbol)
    html, final = fetch.get_html(f"{fetch.SITE}/finance/quote/{urlquote(sym, safe=":")}", label="finance quote")
    m = re.search(r"/finance/(?:beta/)?quote/([^/?#]+)", final)
    canonical = m.group(1).upper() if m else sym
    blocks = fetch.af_blocks(html, "finance")
    q = _quote_block(blocks, canonical) or _quote_block(blocks, None)
    if q is None:
        if ":" not in canonical and "-" not in canonical:
            raise fetch.GoogleNotFound(f"{sym}: not found on Google Finance (use TICKER:EXCHANGE, e.g. AAPL:NASDAQ)")
        if "we couldn't find" in html.lower() or "no results" in html.lower():
            raise fetch.GoogleNotFound(f"{sym}: not found on Google Finance")
        fetch.dump_debug("finance_noquote", html)
        raise fetch.GoogleNotFound(f"{sym}: no quote on Google Finance")
    about = _about_block(blocks) or []
    ticker = _ticker(at(q, 19)) or {}
    pair = ticker.get("pair")
    analysts = _analyst_block(blocks)
    hq = at(about, 3) or []
    pair_about = next((x for x in (at(_find_about_pair(blocks), 0, 0) or []) if isinstance(x, list) and len(x) >= 3 and isinstance(at(x, 2), str)), None) if pair else None
    out = {
        "symbol": at(q, 13) or canonical,
        "ticker": ticker.get("ticker") or at(q, 0, 1, 0),
        "exchange": ticker.get("exchange") or at(q, 0, 1, 1),
        "name": at(q, 14),
        "link": f"{fetch.SITE}/finance/quote/{at(q, 13) or canonical}",
        "type": pair["type"] if pair else ("index" if ticker.get("is_index") else "stock"),
        "pair": pair,
        "currency": at(q, 12) or ticker.get("currency"),
        "price": ticker.get("price") if pair else to_float(at(q, 2)),
        "change": to_float(at(q, 8)),
        "change_percent": to_float(at(q, 10)),
        "previous_close": ticker.get("previous_close") if pair else to_float(at(q, 6)),
        "open": None if pair else to_float(at(q, 15)),
        "day_low": None if pair else to_float(at(q, 4)),
        "day_high": None if pair else to_float(at(q, 5)),
        "year_low": to_float(at(about, 13)),
        "year_high": to_float(at(about, 12)),
        "market_cap": to_float(at(q, 16)),
        "volume": to_int(at(about, 18)),
        "pe_ratio": to_float(at(about, 16)),
        "dividend_yield": to_float(at(about, 17)),
        "eps": to_float(at(about, 19)),
        "beta": to_float(at(about, 20)),
        "shares_outstanding": to_float(at(about, 21)),
        "quoted_at": ticker.get("quoted_at"),
        "timezone": ticker.get("timezone"),
        "after_hours": ticker.get("after_hours"),
        "industry": at(q, 18) if isinstance(at(q, 18), str) else None,
        "company": {
            "description": (at(about, 2) if isinstance(at(about, 2), str) else None) or (at(pair_about, 2) if pair_about else None),
            "ceo": at(about, 5) if isinstance(at(about, 5), str) else None,
            "founded": iso_date(at(about, 4)) if isinstance(at(about, 4), list) else None,
            "employees": to_int(at(about, 6)),
            "headquarters": ({"street": at(hq, 4), "city": at(hq, 0), "region": at(hq, 1), "country": at(hq, 2),
                              "country_code": at(hq, 3)} if hq else None),
            "wikipedia_link": next((x for x in about if isinstance(x, str) and "wikipedia.org" in x), None),
            "website": next((x for x in about if isinstance(x, str) and x.startswith("http") and "wikipedia.org" not in x), None),
        },
        "analyst_ratings": None,
        "similar": [],
        "financials": _financials(blocks) if include_financials else None,
        "chart": None,
        "news": None,
    }
    if analysts:
        head = analysts[0]
        out["analyst_ratings"] = {
            "target_low": to_float(at(head, 2)), "target_high": to_float(at(head, 3)), "target_average": to_float(at(head, 4)),
            "upside_percent": to_float(at(head, 5)), "analyst_count": to_int(at(head, 6)), "consensus": at(head, 7),
            "buy": to_int(at(head, 8)), "hold": to_int(at(head, 9)), "sell": to_int(at(head, 10)),
            "ratings": [{"analyst": at(r, 1), "firm": at(r, 2), "rating": at(r, 3), "date": at(r, 4),
                         "action": at(r, 27) if isinstance(at(r, 27), str) else None,
                         "price_target": to_float(at(r, 19)) or None, "currency": at(r, 21) or None,
                         "headline": at(r, 16) if isinstance(at(r, 16), str) else None,
                         "author": at(r, 17) if isinstance(at(r, 17), str) else None, "link": at(r, 5),
                         "published_at": at(r, 26) if isinstance(at(r, 26), str) else None}
                        for r in (analysts[1] or []) if isinstance(r, list)],
        }
    # similar / related tickers: every ticker row on the page except the quote's own
    seen, similar = {out["symbol"]}, []
    for key, data in blocks.items():
        for row in _find_rows(data):
            t = _ticker(row)
            if t and t["symbol"] not in seen:
                seen.add(t["symbol"])
                similar.append(t)
    out["similar"] = [t for t in similar if not t["is_index"]][:20]
    out["indexes"] = [t for t in similar if t["is_index"]][:12]
    if include_chart:
        charts = _chart_blocks(blocks)
        if charts:
            intraday = max(charts, key=lambda c: sum(1 for p in c if "T" in (p.get("time") or "") and not (p.get("time") or "").endswith("16:00:00-04:00")))
            daily = max(charts, key=lambda c: len({(p.get("time") or "")[:10] for p in c}))
            out["chart"] = {"intraday": intraday[:400], "daily": daily[:400] if daily is not intraday else None}
    if include_news:
        news = []
        for key, rows in _news_blocks(blocks):
            news.extend(_news(rows))
        dedup, seen_links = [], set()
        for n in news:
            if n["link"] not in seen_links:
                seen_links.add(n["link"])
                dedup.append(n)
        out["news"] = dedup[:30]
    return out


def overview(country="US", language="en"):
    """The Google Finance front page for a country: the major indexes,
    equity sectors, the day's market movers and the top financial news.
    (The old /finance/markets/<tab> pages all redirect here now.)"""
    geo = (country or "US").upper()
    html, final = fetch.get_html(f"{fetch.SITE}/finance/beta?hl={language or 'en'}&gl={geo}", label="finance overview")
    blocks = fetch.af_blocks(html, "overview")
    groups = []
    for key, data in blocks.items():
        rows = [t for t in (_ticker(r) for r in _find_rows(data)) if t]
        if len(rows) >= 3:
            label = next((x for x in (at(data, 0, 0) or []) if isinstance(x, str) and not x.startswith("/")), None) \
                if isinstance(at(data, 0, 0), list) else None
            groups.append((key, label, rows))
    indexes, sectors, movers = [], [], []
    seen = set()
    for key, label, rows in groups:
        for r in rows:
            if r["symbol"] in seen:
                continue
            seen.add(r["symbol"])
            target = sectors if label == "sectors" else (indexes if r["is_index"] else movers)
            r["position"] = len(target) + 1
            target.append(r)
    news = []
    for key, rows in _news_blocks(blocks):
        news.extend(_news(rows))
    return {"country": geo, "link": final, "indexes": indexes, "sectors": sectors, "market_movers": movers,
            "news": news[:30]}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "overview":
        out = overview(sys.argv[2] if len(sys.argv) > 2 else "US")
        out["news"] = out["news"][:2]; out["market_movers"] = out["market_movers"][:3]; out["sectors"] = out["sectors"][:2]; out["indexes"] = out["indexes"][:2]
    else:
        out = quote(sys.argv[1] if len(sys.argv) > 1 else "AAPL:NASDAQ")
        if out.get("chart"):
            out["chart"] = {k: (v[:2] if v else v) for k, v in out["chart"].items()}
        out["financials"] = (out["financials"] or [])[:1]
        out["news"] = (out["news"] or [])[:2]
        out["similar"] = out["similar"][:2]
    print(json.dumps(out, indent=2, ensure_ascii=False)[:9000])
