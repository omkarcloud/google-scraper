"""Parsers for www.google.com/search HTML as a replayed identity receives it
(desktop layout, Firefox user-agent, hl=en). Verified 2026-09-24 against
saved pages of every vertical; selectors prefer data-* / role / jsname /
id hooks and fall back to the obfuscated class names of that day.

Shared markup facts:
  * result links are wrapped: href="/goto?url=<opaque token>" (the real
    URL is not in the HTML). serp.py resolves them through the redirect;
    the displayed link (<cite>) still carries domain + breadcrumb.
  * lazy thumbnails: Google emits `var s='data:image/…';var ii=['dimg_1',…]`
    per image and the <img id="dimg_1"> carries a 1x1 placeholder — the
    map is rebuilt from the script text.
  * `id="result-stats"` holds "About N results (0.25 seconds)", escaped
    inside a script on some layouts.
  * pagination: <a id="pnnext" href="/search?…&start=10">.
"""
import html as htmllib
import json
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from lxml import html as lxml_html

SITE = "https://www.google.com"
_WS_RE = re.compile(r"\s+")
_PLACEHOLDER_GIF = "data:image/gif;base64,R0lGODlhAQABAIAAAP"


# ---- helpers -----------------------------------------------------------------------------

def parse_document(html):
    return lxml_html.fromstring(html.encode("utf-8", "replace") if isinstance(html, str) else html)


def text(el):
    if el is None:
        return None
    return _WS_RE.sub(" ", el.text_content()).strip() or None


def first(el, css):
    if el is None:
        return None
    found = el.cssselect(css)
    return found[0] if found else None


def first_of(el, *css):
    for c in css:
        found = first(el, c)
        if found is not None:
            return found
    return None


def absolute(href):
    if not href:
        return None
    return urljoin(SITE, htmllib.unescape(href))


def is_goto(href):
    return bool(href) and "/goto?" in href


def js_unescape(value):
    """Decode a JS string-literal body (\\x3c, \\u003d, \\/ …)."""
    value = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: "\\u00" + m.group(1), value or "")
    try:
        return json.loads('"' + value + '"')
    except ValueError:
        try:
            return value.encode("utf-8").decode("unicode_escape")
        except Exception:
            return value


def deferred_images(html):
    """id -> data URI for the lazily attached thumbnails."""
    out = {}
    for src, ids in re.findall(r"var s='(data:image/[^']+)';var ii=\[([^\]]*)\]", html or ""):
        for i in re.findall(r"'([^']+)'", ids):
            out[i] = src
    return out


def image_src(img, dmap):
    if img is None:
        return None
    if img.get("id") in dmap:
        return dmap[img.get("id")]
    src = img.get("src") or img.get("data-src")
    if src and src.startswith(_PLACEHOLDER_GIF):
        return None
    return src or None


def result_stats(html):
    m = re.search(r'id=\\"result-stats\\">(.*?)\\x3cnobr>\s*\(([\d.]+)s\)', html or "") or \
        re.search(r'id="result-stats">(.*?)<nobr>\s*\(([\d.]+)s\)', html or "")
    if not m:
        return None, None
    txt = htmllib.unescape(js_unescape(m.group(1)))
    n = re.search(r"([\d,.]+)\s+results", txt)
    total = int(n.group(1).replace(",", "").replace(".", "")) if n else None
    return total, float(m.group(2))


def page_query(doc):
    el = first(doc, "[data-async-context]")
    if el is not None:
        m = re.match(r"query:(.*)", el.get("data-async-context") or "")
        if m:
            return unquote(m.group(1))
    return None


def pagination_of(doc):
    nxt = first(doc, "#pnnext")
    start = None
    if nxt is not None:
        start = int(parse_qs(urlparse(absolute(nxt.get("href"))).query).get("start", ["0"])[0])
    pages = []
    for a in doc.cssselect('a[aria-label^="Page "]'):
        label = a.get("aria-label") or ""
        num = re.search(r"\d+", label)
        pages.append({"page": int(num.group(0)) if num else None, "link": absolute(a.get("href"))})
    return {"has_next": nxt is not None, "next_start": start, "pages": pages}


_HOST_RE = re.compile(r"^(?=.*[a-z])[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$", re.I)


def cite_parts(cite):
    """<cite>https://www.wired.com<span> › Gear › laptops</span></cite>"""
    if cite is None:
        return None, None, []
    displayed = text(cite)
    base = (cite.text or "").strip()
    if base.startswith("http"):
        domain = urlparse(base).netloc
    else:
        head = base.split(" ")[0] if base else ""
        # a hostname only: forum / video results put meta text in <cite>
        # ("2.3K+ views · 1 month ago", "5 days ago") — "2.3K+" is not a domain
        domain = head if _HOST_RE.match(head) else None
    crumbs = [c for c in ((text(s) or "").lstrip("› ").strip() for s in cite.cssselect("span")) if c]
    return displayed, (domain or None), crumbs


def query_of(href):
    return parse_qs(urlparse(htmllib.unescape(href or "")).query).get("q", [None])[0]


def rating_of(el):
    """(rating, review_count) from aria-label='Rated 4.5 out of 5, 29 user reviews'."""
    r = first(el, '[aria-label^="Rated "]')
    if r is None:
        return None, None
    label = r.get("aria-label") or ""
    m = re.search(r"Rated ([\d.]+) out of 5", label)
    rating = float(m.group(1)) if m else None
    m2 = re.search(r"([\d.,]+[KM]?)\s+(?:user )?reviews", (text(r.getparent()) or "") + " " + label)
    reviews = m2.group(1) if m2 else None
    if reviews is None:
        rd = first(el, ".RDApEe")
        reviews = (text(rd) or "").strip("()") or None if rd is not None else None
    return rating, count_of(reviews)


def count_of(value):
    """'27K' | '1,234' | '250+' -> int."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"([\d.,]+)\s*([KkMmBb])?", str(value))
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get((m.group(2) or "").lower(), 1)
    return int(num * mult)


def _date_like(s):
    return bool(re.search(r"\bago\b|\b(19|20)\d{2}\b|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", s or ""))


# ---- web ------------------------------------------------------------------------------------

def _organic(node, dmap):
    a = first_of(node, "a.zReHs", 'a[jsname="UWckNb"]', "a[href] h3")
    if a is not None and a.tag == "h3":
        a = a.getparent()
    h3 = first(node, "h3")
    if a is None or h3 is None:
        return None
    cite = first(node, "cite")
    displayed, domain, crumbs = cite_parts(cite)
    item = {
        "position": None,
        "title": text(h3),
        "link": absolute(a.get("href")),
        "displayed_link": displayed,
        "domain": domain,
        "breadcrumbs": crumbs,
        "source": text(first(node, ".VuuXrf")),
        "favicon": image_src(first(node, "img.XNo5Ab"), dmap),
        "snippet": None,
        "highlighted_words": [],
        "date": None,
        "thumbnail": None,
        "sitelinks": [],
        "rich_snippet": None,
        "sub_results": [],
        "video": None,
        "type": "organic",
    }
    sn = first(node, '[data-sncf="1"]')
    if sn is not None:
        snippet = text(sn)
        d = first(sn, ".YrbPuc")
        if d is not None and text(d):
            item["date"] = text(d).rstrip(" —").strip()
            snippet = (snippet or "").replace(text(d), "", 1).strip(" —").strip() or None
        item["snippet"] = snippet
        item["highlighted_words"] = [t for t in (text(x) for x in sn.cssselect("em, b")) if t]
    if not item["domain"] and item["link"] and not is_goto(item["link"]):
        item["domain"] = urlparse(item["link"]).netloc or None    # (a /goto link: serp.resolve_links fills it)
    if displayed and "›" not in displayed and not displayed.startswith("http") and not _HOST_RE.match(displayed):
        # meta text, not a URL ("5 days ago", "2.3K+ views · 1 month ago"): keep its date
        tail = displayed.split("·")[-1].strip()
        item["date"] = item["date"] or (tail if _date_like(tail) else None)
        item["displayed_link"] = None
    th = first_of(node, '[data-snf="Vjbam"] img', "a.rIRoqf img")
    item["thumbnail"] = image_src(th, dmap)
    item["sitelinks"] = [{"title": text(l), "link": absolute(l.get("href"))} for l in node.cssselect('[data-sncf="2"] a.brKmxb') if text(l)]
    rich = {}
    lst = first(node, '[data-snf="kZ2HQc"]')
    if lst is not None:
        rich["list"] = [t for t in (text(x) for x in lst.cssselect(".ADx4Yb")) if t]
    rating, reviews = rating_of(node)
    if rating:
        rich["rating"] = rating
        rich["review_count"] = reviews
        rich["extras"] = [t for t in (text(x) for x in node.cssselect('[data-snf="mCCBcf"] .KJloJf')) if t]
    item["rich_snippet"] = rich or None
    sub = first(node, '[data-snf="M7eMpf"]')
    if sub is not None:
        for l in sub.cssselect('a[href^="/goto"], a[href^="http"]'):
            meta = [t for t in (text(s) for s in l.itersiblings("span")) if t][:2]
            item["sub_results"].append({"title": text(l), "link": absolute(l.get("href")), "meta": meta})
    v = first(node, ".WVV5ke[data-surl]")
    if v is not None:
        dur = first_of(node, ".rIRoqf span", ".gY2b2c span")
        item["video"] = {"link": v.get("data-surl"), "duration": text(dur),
                         "meta": text(first_of(node, ".gqF9jc", ".byrV5b"))}
        item["type"] = "video"
    elif re.search(r"followers|reactions|comments", displayed or ""):
        item["type"] = "social"
    return item


def parse_ads(doc, dmap):
    ads = []
    for ad in doc.cssselect("[data-text-ad]"):
        a = first_of(ad, "a.sVXRqc", "a[data-pcu]")
        if a is None:
            continue
        if ad.xpath('ancestor::*[@id="tads" or @id="tvcap"]'):
            block = "top"
        elif ad.xpath('ancestor::*[@id="tadsb"]'):
            block = "bottom"
        else:
            block = "inline"
        ads.append({
            "position": len(ads) + 1,
            "block": block,
            "title": text(first(a, '[role="heading"]')) or text(a),
            "link": (a.get("data-pcu") or "").split(",")[0] or absolute(a.get("href")),
            "tracking_link": absolute(a.get("href")),
            "displayed_link": text(first_of(ad, ".wbJOMb", ".yIn8Od")),
            "description": text(first_of(ad, ".hKLoRb", ".Va3FIb.r025kc")),
            "extras": [t for t in (text(x) for x in ad.cssselect(".aiL7Jf")) if t],
            "sitelinks": [{"title": text(l), "link": absolute(l.get("href"))} for l in ad.cssselect("a.brKmxb, a.fCBnFe") if text(l)],
            "thumbnail": image_src(first(ad, "img.YQ4gaf"), dmap),
        })
    return ads


def parse_people_also_ask(doc):
    out = []
    for q in doc.cssselect("div.related-question-pair[data-q]"):
        ans = first(q, ".bCOlv")
        answer = text(ans)
        if answer and answer.startswith("An error has occurred"):
            answer = None
        src = first(ans, "a[href]") if ans is not None else None
        out.append({"question": q.get("data-q"), "answer": answer,
                    "source": {"title": text(src), "link": absolute(src.get("href"))} if src is not None else None})
    return out


def parse_related_searches(doc):
    """'People also search for' / 'Related searches' chips at the page foot
    (and the inline variant): every /search?q= link inside #bres /
    #botstuff / the y6Uyqe list, plus any a.ngTNl chip elsewhere."""
    out, seen = [], set()
    for a in doc.cssselect("#bres a[href*='/search?'], #botstuff a[href*='/search?'], .y6Uyqe a[href*='/search?'], a.ngTNl[href*='/search?']"):
        q = query_of(a.get("href"))
        if not q or q.lower() in seen:
            continue
        seen.add(q.lower())
        out.append({"query": q, "link": absolute(a.get("href"))})
    return out


def _sections(doc):
    """Heading-led blocks inside #rso keyed by their heading text."""
    out = {}
    for sec in doc.cssselect('#rso g-section-with-header, #rso div[jscontroller="HWk0Gf"], #rso div.vt6azd[data-hveid]'):
        hd = first(sec, '[role="heading"]')
        if hd is not None and text(hd):
            out.setdefault(text(hd), sec)
    return out


_TAGS = ("Highly Cited", "Video", "Opinion", "Local coverage", "Live", "In depth")


def story_items(sec, dmap):
    """News cards: clustered layouts wrap each card in div.m7jPZ, the
    date-sorted news tab in div[data-news-doc-id]; both carry a.aJWbwf."""
    items, seen = [], set()
    for it in sec.cssselect("div.m7jPZ[data-hveid], div[data-news-doc-id]"):
        a = first(it, "a.aJWbwf")
        if a is None or a.get("href") in seen:
            continue
        seen.add(a.get("href"))
        spans = [t for t in (text(s) for s in a.cssselect("span")) if t]
        items.append({
            "id": it.get("data-news-doc-id"),
            "title": text(first(a, '[id^="news_title_"]')),
            "link": absolute(a.get("href")),
            "source": next((s for s in spans if not _date_like(s) and s not in _TAGS and not re.match(r"^\d+:\d\d$", s)), None),
            "date": next((s for s in spans if _date_like(s)), None),
            "tags": [s for s in spans if s in _TAGS],
            "thumbnail": image_src(first(a, 'img[id^="dimg_"]'), dmap),
        })
    return items


def parse_top_stories(doc, dmap, secs):
    sec = secs.get("Top stories")
    if sec is None:
        return []
    clusters = []
    for cl in sec.cssselect("div.C84Xbf"):
        clusters.append({"title": text(first(cl, '[role="heading"]')), "stories": story_items(cl, dmap)})
    if not clusters:
        clusters = [{"title": None, "stories": story_items(sec, dmap)}]
    return clusters


def video_cards(container, dmap):
    out = []
    for v in container.cssselect(".WVV5ke[data-surl]"):
        a = first_of(v, "a.rIRoqf[href]", "a.zReHs")
        heading = first_of(v, '[role="heading"]', "h3")
        title_el = first_of(heading, ".cHaqb", ".Yt787") if heading is not None else None
        spans = [t for t in (text(s) for s in v.cssselect("span")) if t and t != "·"]
        by = first(v, '[aria-label*=" by "]')
        channel = None
        if by is not None:
            m = re.search(r" by (.+?) on ", by.get("aria-label") or "")
            channel = m.group(1) if m else None
        out.append({
            "position": len(out) + 1,
            "title": text(title_el if title_el is not None else heading),
            "link": v.get("data-surl"),
            "tracking_link": absolute(a.get("href")) if a is not None else None,
            "platform": next((s for s in spans if s in ("YouTube", "Facebook", "TikTok", "Instagram", "Vimeo", "Dailymotion")), None),
            "channel": channel,
            "date": next((s for s in spans if _date_like(s)), None),
            "duration": next((s for s in spans if re.match(r"^\d+:\d\d(:\d\d)?$", s)), None),
            "thumbnail": image_src(first(v, 'img[id^="dimg_"]'), dmap),
        })
    return out


def posts_section(sec, dmap):
    out = []
    for li in sec.cssselect('[role="listitem"]'):
        a = first(li, "a.aJWbwf")
        if a is None:
            continue
        spans = [t for t in (text(s) for s in a.cssselect("span")) if t and t != "·"]
        out.append({
            "position": len(out) + 1,
            "text": text(first(a, '[id^="news_title_"]')),
            "link": absolute(a.get("href")),
            "author": text(first(a, ".sTl1Td")),
            "platform": text(first(a, ".appd0")),
            "author_bio": text(first(a, ".uJLhNc")),
            "likes": count_of(next((s for s in spans if "likes" in s), None)),
            "date": next((s for s in spans if "ago" in s or _date_like(s)), None),
            "duration": next((s for s in spans if re.match(r"^\d+:\d\d$", s)), None),
            "thumbnail": image_src(first(a, 'img[id^="dimg_"]'), dmap),
        })
    return out


def parse_knowledge_panel(doc, dmap):
    rhs = first(doc, "#rhs")
    if rhs is None:
        return None
    title = text(first(doc, '[data-attrid="title"]'))
    if not title:
        return None
    kp = {"title": title, "subtitle": text(first(doc, '[data-attrid="subtitle"]')), "description": None,
          "description_source": None, "image": None, "attributes": [], "profiles": [], "listen_links": [],
          "people_also_search_for": []}
    d = first(rhs, '[data-attrid="description"]')
    if d is not None:
        kp["description"] = text(first_of(d, ".kno-rdesc > span", "span"))
        src = first(d, "a[href]")
        if src is not None:
            kp["description_source"] = {"name": text(src), "link": absolute(src.get("href"))}
    for w in rhs.cssselect('.wDYxhc[data-attrid^="kc:/"]'):
        aid = w.get("data-attrid")
        if aid.endswith("social media presence") or ":tv-shows" in aid or ":songs" in aid:
            continue
        txt = text(w) or ""
        key, _, value = txt.partition(":")
        if not value:
            continue
        kp["attributes"].append({"id": aid, "name": key.strip(), "value": value.strip().replace(" · See more", ""),
                                 "links": [{"text": text(l), "link": absolute(l.get("href"))} for l in w.cssselect("a[href]") if text(l)]})
    kp["profiles"] = [{"name": text(p), "link": absolute(first(p, "a").get("href"))}
                      for p in rhs.cssselect('[data-attrid="kc:/common/topic:social media presence"] .kno-vrt-t') if first(p, "a") is not None]
    la = first(rhs, '[data-attrid="action:listen_artist"]')
    if la is not None:
        kp["listen_links"] = [{"name": text(l), "link": absolute(l.get("href"))} for l in la.cssselect("a[href]") if text(l)]
    kp["people_also_search_for"] = [
        {"name": text(first(li, '[role="heading"]')), "subtitle": text(first(li, ".qXy4ec")),
         "link": absolute(first(li, "a").get("href")), "thumbnail": image_src(first(li, "img"), dmap)}
        for li in rhs.cssselect('[role="list"] [role="listitem"]') if first(li, "a") is not None and text(first(li, '[role="heading"]'))]
    kp["image"] = next((i for i in (image_src(i, dmap) for i in rhs.cssselect('img[id^="dimg_"]')) if i), None)
    return kp


def parse_knowledge_carousels(doc, dmap):
    out = []
    for w in doc.cssselect('#rso [data-attrid^="kc:/"]'):
        parent = w.getparent()
        items = [{"name": text(first(li, '[role="heading"]')), "subtitle": text(first(li, ".qXy4ec")),
                  "link": absolute(first(li, "a").get("href")) if first(li, "a") is not None else None,
                  "thumbnail": image_src(first(li, "img"), dmap)} for li in w.cssselect('[role="listitem"]')]
        if items:
            out.append({"id": w.get("data-attrid"), "heading": text(first(parent, '[role="heading"]')) if parent is not None else None,
                        "items": items})
    return out


def parse_featured(doc, dmap):
    results = []
    for w in doc.cssselect('[data-attrid="VisualDigestWebResult"]'):
        a = first(w, "a[href]")
        results.append({"id": w.get("data-docid"), "source": text(first(w, ".UynAtc")), "title": text(first(w, ".rDCETb")),
                        "snippet": text(first(w, ".WpsIbd")), "link": absolute(a.get("href")) if a is not None else None,
                        "thumbnail": image_src(first(w, "img"), dmap)})
    facts = [{"id": f.get("data-attrid"), "text": text(f)} for f in doc.cssselect('[data-attrid^="lab/fact/"]') if text(f)]
    return results, facts


_AIO_NOISE = (
    r"An AI Overview is not available for this search",
    r"Can't generate an AI overview right now\. Try again later\.",
    r"Go to product viewer dialog for this item\.",
    r"AI Mode replied:\s*",
    r"^Show (all|more|less)$",
)


def parse_ai_overview(doc):
    """The AI Overview block. Google renders its text lazily on most
    layouts (a follow-up XHR after load), so a replayed page may carry the
    block with only its fallback strings: `is_loaded` says whether real
    text came with the HTML."""
    aio = first(doc, "#eKIzJc")
    if aio is None:
        return None
    for junk in aio.cssselect("style, script"):
        junk.drop_tree()
    # source chips ("Wikipedia +1" buttons, aria-label "Wikipedia (+1) - Related
    # results") name the cited sites; their text must not run into the answer
    chip_names = []
    for b in aio.cssselect("button"):
        m = re.match(r"(.+?)\s*(?:\(\+\d+\))?\s*-\s*Related results", b.get("aria-label") or "")
        if m:
            chip_names.append(m.group(1).strip())
        b.drop_tree()
    # Cited sources: each is a card (`[data-src-id]`: site, page title, snippet)
    # whose link's aria-label is the page title; inline citation links in the
    # answer carry the site name as aria-label and point at the same URL.
    site_of = {}
    for a in aio.cssselect("a[href]"):
        if is_goto(a.get("href")) and a.get("data-link-behavior") != "cobrowse" and a.get("aria-label"):
            site_of.setdefault(absolute(a.get("href")), a.get("aria-label").strip())
    sources, seen = [], set()
    cards = aio.cssselect("[data-src-id]")
    for card in cards:
        a = first(card, "a[href]")
        href = absolute(a.get("href")) if a is not None else None
        if not href or href in seen:
            continue
        seen.add(href)
        title = re.sub(r"\.\s*Opens in new tab\.?$", "", (a.get("aria-label") or "").strip()) or text(card)
        sources.append({"title": title or None, "source": site_of.get(href), "link": href})
    if not cards:                                   # older layout: plain links
        for a in aio.cssselect("a[href]"):
            href = absolute(a.get("href"))
            if not href or "google.com/search?" in href or href in seen:
                continue
            seen.add(href)
            title = text(a) or a.get("aria-label")
            title = re.sub(r"Go to product viewer dialog for this item\.", "", title or "").strip() or None
            sources.append({"title": title, "source": site_of.get(href), "link": href})
        for i, name in enumerate(chip_names[:len(sources)]):
            sources[i]["source"] = sources[i]["source"] or name
    # the source cards are listed in `sources`, not part of the answer text
    for card in cards:
        if card.getparent() is None:
            continue
        box = next((x for x in card.iterancestors() if x.tag == "ul"), None)
        box = box if box is not None and aio in list(box.iterancestors()) else card
        if box.getparent() is not None:
            box.drop_tree()
    # block elements end a line, so "Top picks for 2026" and "Best overall: …" don't run together
    for el in aio.iter("li", "h2", "h3", "h4", "p", "ul", "ol", "div"):
        el.tail = "\n" + (el.tail or "")
    lines = []
    for line in (aio.text_content() or "").split("\n"):
        for pattern in _AIO_NOISE:
            line = re.sub(pattern, " ", line)
        line = _WS_RE.sub(" ", line).strip()
        if not line or (lines and lines[-1] == line):
            continue
        if lines and (line[0].islower() or line[0] in "–—-,.;:)") and not lines[-1].endswith((".", "!", "?")):
            lines[-1] = f"{lines[-1]} {line}"      # an inline element that sat in its own <div>
        else:
            lines.append(line)
    body = re.sub(r"^AI Overview\s*", "", "\n".join(lines)).strip()
    return {"is_available": True, "is_loaded": bool(body), "text": body or None, "sources": sources}


def parse_web(html):
    doc = parse_document(html)
    dmap = deferred_images(html)
    total, seconds = result_stats(html)
    secs = _sections(doc)
    organic = []
    rso = first(doc, "#rso")
    for blk in (rso.cssselect("div.MjjYud") if rso is not None else []):
        if blk.xpath('ancestor::div[@class="MjjYud"]'):
            continue
        node = first(blk, 'div[jscontroller="SC7lYd"][data-hveid], div.PmEWq[jsname="pKB8Bc"]')
        if node is None or node.xpath("ancestor::*[@data-text-ad]"):
            continue
        item = _organic(node, dmap)
        if item:
            item["position"] = len(organic) + 1
            organic.append(item)
    featured, facts = parse_featured(doc, dmap)
    latest = next((k for k in secs if k.startswith("Latest posts from")), None)
    return {
        "search_information": {
            "query": page_query(doc),
            "total_results": total,
            "time_taken_seconds": seconds,
            "showing_results_for": text(first(doc, "#fprs")),
        },
        "organic_results": organic,
        "ads": parse_ads(doc, dmap),
        "ai_overview": parse_ai_overview(doc),
        "knowledge_panel": parse_knowledge_panel(doc, dmap),
        "knowledge_carousels": parse_knowledge_carousels(doc, dmap),
        "featured_results": featured,
        "featured_facts": facts,
        "people_also_ask": parse_people_also_ask(doc),
        "top_stories": parse_top_stories(doc, dmap, secs),
        "inline_videos": video_cards(secs["Videos"], dmap) if "Videos" in secs else [],
        "short_videos": video_cards(secs["Short videos"], dmap) if "Short videos" in secs else [],
        "discussions": posts_section(secs["What people are saying"], dmap) if "What people are saying" in secs else [],
        "latest_posts": {"heading": latest, "posts": posts_section(secs[latest], dmap)} if latest else None,
        "related_searches": [r for r in parse_related_searches(doc)
                             if r["query"].lower() != (page_query(doc) or "").lower()],
        "pagination": pagination_of(doc),
    }


# ---- images ----------------------------------------------------------------------------------

_IMG_RE = re.compile(r'\[0,"(?P<docid>[A-Za-z0-9_\-]{6,})",\["(?P<thumb>https://encrypted-tbn[^"]+)",(?P<tw>\d+),(?P<th>\d+)\],'
                     r'\["(?P<url>[^"]+)",(?P<w>\d+),(?P<h>\d+)\](?P<rest>.*?)\}\]', re.S)


def parse_images(html):
    doc = parse_document(html)
    seen = {}
    for m in _IMG_RE.finditer(html):
        d = m.groupdict()
        if d["docid"] in seen:
            continue
        rest = d["rest"]
        m2 = re.search(r'"2003":\[null,"(?P<ref>[^"]*)","(?P<page>[^"]*)","(?P<title>(?:[^"\\]|\\.)*)"', rest)
        m3 = re.search(r'"2000":\[null,"(?P<domain>[^"]*)","(?P<size>[^"]*)"', rest)
        site = re.search(r'"2003":\[[^\]]*?(?:,"[^"]*"){2},null,0,null,"[^"]*",null,0,null,null,"(?P<site>[^"]*)"', rest)
        seen[d["docid"]] = {
            "position": None,
            "id": d["docid"],
            "title": js_unescape(m2["title"]) if m2 else None,
            "link": js_unescape(d["url"]),
            "width": int(d["w"]),
            "height": int(d["h"]),
            "file_size": (m3["size"] or None) if m3 else None,
            "thumbnail": {"link": js_unescape(d["thumb"]), "width": int(d["tw"]), "height": int(d["th"])},
            "source": {"name": site["site"] if site else None, "domain": (m3["domain"] or None) if m3 else None,
                       "page_link": js_unescape(m2["page"]) if m2 else None},
        }
    items = []
    for i, el in enumerate(doc.cssselect('[data-attrid="images universal"][data-docid]'), 1):
        it = seen.get(el.get("data-docid"))
        if it is None:
            it = {"position": None, "id": el.get("data-docid"), "title": None, "link": None, "width": None, "height": None,
                  "file_size": None, "thumbnail": None, "source": {"name": None, "domain": None, "page_link": None}}
        it["position"] = i
        it["title"] = it["title"] or text(first(el, ".Q6A6Dc"))
        it["source"]["page_link"] = it["source"]["page_link"] or el.get("data-lpage")
        it["source"]["name"] = it["source"]["name"] or text(first(el, '[data-snf="F0zcsf"] span'))
        items.append(it)
    if not items:
        items = list(seen.values())
        for i, it in enumerate(items, 1):
            it["position"] = i
    chips = [{"text": text(a), "query": query_of(a.get("href")), "link": absolute(a.get("href"))} for a in doc.cssselect("a.nPDzT") if text(a)]
    related = [{"query": query_of(a.get("href")) or text(a), "link": absolute(a.get("href"))} for a in doc.cssselect("a.bqW4cb") if text(a)]
    total, _ = result_stats(html)
    return {"search_information": {"query": page_query(doc), "total_results": total},
            "images": items, "suggested_filters": chips, "related_searches": related}


# ---- videos ------------------------------------------------------------------------------------

def parse_videos(html):
    doc = parse_document(html)
    dmap = deferred_images(html)
    out = []
    for v in doc.cssselect('#rso div.PmEWq[jsname="pKB8Bc"]'):
        a = first(v, "a.zReHs")
        displayed, domain, _ = cite_parts(first(v, "cite"))
        meta = text(first(v, ".gqF9jc")) or ""
        parts = [p.strip() for p in meta.split("·") if p.strip()]
        surl = first(v, ".WVV5ke")
        km = first(v, ".eQ9fW")
        out.append({
            "position": len(out) + 1,
            "title": text(first(v, "h3")),
            "link": surl.get("data-surl") if surl is not None else None,
            "tracking_link": absolute(a.get("href")) if a is not None else None,
            "displayed_link": displayed,
            "domain": domain,
            "platform": parts[0] if parts else None,
            "channel": parts[1] if len(parts) > 1 else None,
            "date": parts[-1] if len(parts) > 2 else None,
            "duration": text(first(v, ".gY2b2c span")),
            "snippet": text(first(v, ".ITZIwc")),
            "highlighted_words": [t for t in (text(b) for b in v.cssselect(".ITZIwc b, .ITZIwc em")) if t],
            "thumbnail": image_src(first(v, 'img[id^="dimg_"]'), dmap),
            "key_moments": text(first(km, "span")) if km is not None else None,
        })
    total, _ = result_stats(html)
    return {"search_information": {"query": page_query(doc), "total_results": total}, "videos": out,
            "pagination": pagination_of(doc)}


# ---- news ---------------------------------------------------------------------------------------

def parse_news(html):
    doc = parse_document(html)
    dmap = deferred_images(html)
    clusters, flat = [], []
    for sec in doc.cssselect("#rso g-section-with-header"):
        stories = story_items(sec, dmap)
        fc = first(sec, "a.jRKCUd")
        clusters.append({"title": text(first(sec, '[role="heading"]')),
                         "full_coverage_link": absolute(fc.get("href")) if fc is not None else None,
                         "stories": stories})
        flat += stories
    known = {s["link"] for s in flat}
    for it in doc.cssselect("#rso div.m7jPZ[data-hveid], #rso div[data-news-doc-id]"):
        if not it.xpath("ancestor::g-section-with-header"):
            for s in story_items(it, dmap):
                if s["link"] not in known:
                    known.add(s["link"])
                    flat.append(s)
    for i, s in enumerate(flat, 1):
        s["position"] = i
    total, _ = result_stats(html)
    return {"search_information": {"query": page_query(doc), "total_results": total}, "articles": flat,
            "clusters": clusters, "pagination": pagination_of(doc)}


# ---- shopping -------------------------------------------------------------------------------------

def _price(value):
    if not value:
        return None, None
    m = re.search(r"(?P<cur>[^\d\s]{1,3})?\s*(?P<amt>[\d.,]+)", value)
    if not m:
        return None, None
    amount = m.group("amt").replace(",", "")
    try:
        return float(amount), (m.group("cur") or None)
    except ValueError:
        return None, m.group("cur")


def parse_shopping(html):
    doc = parse_document(html)
    dmap = deferred_images(html)
    out = []
    for p in doc.cssselect("#rso div[data-oid][data-cid]"):
        price_el = first(p, ".lmQWe")
        price, currency = _price(text(price_el))
        compare, _ = _price(text(first(p, ".DoCHT")))
        rating, reviews = rating_of(p)
        img = first(p, '[role="img"][title]')
        out.append({
            "position": len(out) + 1,
            "id": p.get("data-cid"),
            "offer_id": p.get("data-oid"),
            "title": text(first(p, ".gkQHve")) or (img.get("title") if img is not None else None),
            "link": f"{SITE}/shopping/product/{p.get('data-cid')}",
            "price": price,
            "currency": currency,
            "price_text": text(price_el),
            "original_price": compare,
            "discount": text(first(p, ".NkyFue")),
            "seller": text(first(p, ".WJMUdc")),
            "has_more_sellers": bool(text(first(p, ".Ludoze"))),
            "tags": [t for t in (text(x) for x in p.cssselect(".l9Ycjb")) if t],
            "rating": rating,
            "review_count": reviews,
            "thumbnail": image_src(first(p, 'img[id^="dimg_"]'), dmap),
        })
    filters = [t for t in (text(x) for x in doc.cssselect('[role="list"] [role="listitem"]')) if t]
    return {"search_information": {"query": page_query(doc)}, "products": out, "filters": filters}


# ---- local -------------------------------------------------------------------------------------------

_LOCAL_BLOB_RE = re.compile(r'"(?P<mid>/g/[a-z0-9_]+)",\[\[\{"512247391":\[\]\}\],\[\[\[\[\{"498469725":\["(?P<name>(?:[^"\\]|\\.)*)",'
                            r'\[(?P<rating>[\d.]+|null),null,null,\[(?P<reviews>\d+)?\]\],(?:"(?P<price>[^"]*)"|null)(?P<rest>.*?)"Place operating status"\]', re.S)


def parse_local(html):
    doc = parse_document(html)
    dmap = deferred_images(html)
    blobs = {}
    for m in _LOCAL_BLOB_RE.finditer(html):
        rest = m["rest"]
        b = {"rating": float(m["rating"]) if m["rating"] != "null" else None,
             "review_count": int(m["reviews"]) if m["reviews"] else None, "price_level": m["price"] or None,
             "feature_id": (re.search(r'"(0x[0-9a-f]+:0x[0-9a-f]+)"', rest) or [None, None])[1],
             "place_id": (re.search(r'"(ChIJ[A-Za-z0-9_\-]+)"', rest) or [None, None])[1]}
        for label, key in (("Place name", "name"), ("Place category", "category"), ("Place location", "address"),
                           ("Place opening hours", "hours"), ("Place phone number", "phone"), ("Place website", "website")):
            mm = re.search(r'\["((?:[^"\\]|\\.)*)",\d+,\d+,\d+,\[[^\]]*\],"' + label + r'"\]', rest)
            b[key] = js_unescape(mm.group(1)) if mm else None
        blobs[m["mid"]] = b
    out = []
    for p in doc.cssselect("#rso div.w7Dbne[data-hveid]"):
        pv = first(p, '[id^="pv-"]')
        mid = pv.get("id")[3:] if pv is not None else None
        det = first(p, ".rllt__details")
        rating, reviews = rating_of(p)
        leaf = [t for t in (text(e) for e in det.iter() if len(e) == 0) if t] if det is not None else []
        blob = blobs.get(mid) or {}
        out.append({
            "position": len(out) + 1,
            "id": mid,
            "place_id": blob.get("place_id"),
            "feature_id": blob.get("feature_id"),
            "name": text(first(p, '[role="heading"]')) or blob.get("name"),
            "link": f"{SITE}/maps/place/?q=place_id:{blob['place_id']}" if blob.get("place_id") else None,
            "category": blob.get("category"),
            "rating": rating or blob.get("rating"),
            "review_count": reviews or blob.get("review_count"),
            "price_level": next((x for x in leaf if x.startswith("$")), None) or blob.get("price_level"),
            "address": blob.get("address"),
            "phone": blob.get("phone"),
            "website": blob.get("website"),
            "hours": blob.get("hours"),
            "open_state": next((x for x in leaf if re.match(r"^(Open|Closed|Opens|Closes)", x)), None),
            "highlights": [t for t in (text(b) for b in p.cssselect("b")) if t],
            "summary": text(det),
            "thumbnail": image_src(first(p, 'img[id^="pimg_"]'), dmap),
        })
    total, _ = result_stats(html)
    return {"search_information": {"query": page_query(doc), "total_results": total}, "places": out,
            "pagination": pagination_of(doc)}


# ---- jobs --------------------------------------------------------------------------------------------

def parse_jobs(html):
    doc = parse_document(html)
    out = []
    for c in doc.cssselect("div.EimVGf[data-share-url]"):
        share = htmllib.unescape(c.get("data-share-url"))
        docid = parse_qs(urlparse(share).query).get("htidocid", [None])[0]
        loc_via = text(first(c, ".FqK3wc")) or ""
        loc, _, via = loc_via.partition("•")
        chips = {}
        for s in c.cssselect("span.Yf9oye[aria-label]"):
            chips[(s.get("aria-label") or "").split(" ")[0].lower()] = text(s)
        tmpl = first(c, "template")
        description, highlights, apply_links, logo = None, [], [], None
        if tmpl is not None:
            for b in tmpl.cssselect(".XFOJCe"):
                t = text(b) or ""
                if t.startswith("Job description"):
                    description = t[len("Job description"):].strip()
                elif t.startswith("Job highlights"):
                    highlights = [x for x in (text(li) for li in b.cssselect("li")) if x]
            apply_links = [{"title": a.get("title"), "link": absolute(a.get("href"))} for a in tmpl.cssselect("a.brKmxb[href]")]
            lg = first(tmpl, ".ZCPy9b img")
            logo = lg.get("src") if lg is not None else None
        out.append({
            "position": len(out) + 1,
            "id": docid,
            "title": text(first(c, ".PUpOsf")),
            "company": text(first(c, ".a3jPc")),
            "company_logo": logo,
            "location": loc.strip() or None,
            "via": via.replace("via", "").strip() or None,
            "link": share,
            "posted": chips.get("posted"),
            "salary": chips.get("salary"),
            "employment_type": chips.get("employment"),
            "benefits": [t for t in (text(s) for s in c.xpath("./div/span[not(@class)]")) if t],
            "description": description,
            "highlights": highlights,
            "apply_links": apply_links,
        })
    filters = [t for t in (text(x) for x in doc.cssselect('[role="listitem"]')) if t]
    return {"search_information": {"query": page_query(doc)}, "jobs": out, "filters": filters}


# ---- books ---------------------------------------------------------------------------------------------

def parse_books(html):
    doc = parse_document(html)
    dmap = deferred_images(html)
    out = []
    for b in doc.cssselect("#rso div.Lglv5b[data-hveid]"):
        a = first(b, "a.zReHs")
        link = absolute(a.get("href")) if a is not None else None
        bid = parse_qs(urlparse(link or "").query).get("id", [None])[0]
        leaf = [t for t in (text(e) for e in b.iter() if len(e) == 0 and e.tag not in ("h3", "em")) if t]
        long_spans = b.xpath(".//span[not(@class) and string-length(text())>60]")
        out.append({
            "position": len(out) + 1,
            "id": bid,
            "title": text(first(b, "h3")),
            "link": link,
            "authors": [t for t in (text(x) for x in b.cssselect('a[href*="inauthor"]')) if t],
            "year": next((x for x in leaf if re.match(r"^\d{4}$", x)), None),
            "snippet": text(long_spans[0]) if long_spans else None,
            "found_inside": text(first(b, "div.tOnVoe")),
            "highlighted_words": [t for t in (text(e) for e in b.cssselect("em")) if t],
            "thumbnail": image_src(first(b, 'img[id^="dimg_"]'), dmap),
            "actions": [t for t in (text(x) for x in b.cssselect(".Iewgqd")) if t],
        })
    total, _ = result_stats(html)
    return {"search_information": {"query": page_query(doc), "total_results": total}, "books": out,
            "pagination": pagination_of(doc)}


# ---- forums --------------------------------------------------------------------------------------------

def parse_forums(html):
    doc = parse_document(html)
    dmap = deferred_images(html)
    out = []
    for n in doc.cssselect('#rso div[jscontroller="SC7lYd"][data-hveid]'):
        it = _organic(n, dmap)
        if not it:
            continue
        m = re.match(r"(.+?)\s*·\s*(.+)", text(first(n, "cite")) or "")
        replies, date = (m.group(1), m.group(2)) if m else (None, it.get("date"))
        out.append({
            "position": len(out) + 1,
            "title": it["title"],
            "link": it["link"],
            "source": it["source"],
            "snippet": it["snippet"],
            "reply_count": count_of(replies),
            "date": date,
            "thumbnail": it["thumbnail"],
        })
    total, _ = result_stats(html)
    return {"search_information": {"query": page_query(doc), "total_results": total}, "threads": out,
            "pagination": pagination_of(doc)}
