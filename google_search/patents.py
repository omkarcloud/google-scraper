"""Google Patents (patents.google.com), plain curl_cffi, no cookies
(validated 2026-09-24):

  * SEARCH   GET /xhr/query?url=<the site's own query string>&exp=
             -> JSON {results: {total_num_results, total_num_pages, num_page,
             cluster: [{result: [{id, rank, patent: {title, snippet,
             priority_date, filing_date, grant_date, publication_date,
             inventor, assignee, publication_number, language, thumbnail,
             pdf, figures}}]}]}}. The inner query string is what the site
             puts after /?: q=, num=, page= (0-based), before=/after=
             (priority|filing|publication:YYYYMMDD), inventor=, assignee=,
             country=, language=, status=GRANT|APPLICATION, type=PATENT|
             DESIGN, litigation=YES|NO, sort=new|old (default relevance).
  * DETAILS  GET /patent/<publication number>/<lang> -> server-rendered
             page with schema.org itemprop markup (abstract, claims,
             description, inventors, assignees, dates, classifications,
             citations, cited-by, similar documents, legal events, family)
             plus <meta name="citation_*"> / DC.* tags.
"""
import json
import os
import re
import sys
from urllib.parse import quote, urlencode, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_search import fetch  # noqa: E402
from google_search.shared import clean_text, paged_result, to_int  # noqa: E402

PAGE_SIZES = (10, 25, 50, 100)
DEFAULT_NUM = 10
SORTS = {"relevance": None, "newest": "new", "oldest": "old"}
STATUSES = {"any": None, "granted": "GRANT", "application": "APPLICATION"}
TYPES = {"any": None, "patent": "PATENT", "design": "DESIGN"}
DATE_FIELDS = ("priority", "filing", "publication")
IMAGE_BASE = "https://patentimages.storage.googleapis.com/"
_PATENT_ID_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{4,20}$")


def _date_token(value, field):
    """'2020-01-31' -> 'priority:20200131'."""
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) not in (4, 6, 8):
        raise ValueError("dates must be YYYY-MM-DD")
    return f"{field}:{digits}"


def _search_query(query, num, page, before, after, date_field, inventor, assignee, country, language, status,
                  kind, sort, litigation):
    parts = [("q", query), ("num", num), ("page", page - 1)]
    if before:
        parts.append(("before", _date_token(before, date_field)))
    if after:
        parts.append(("after", _date_token(after, date_field)))
    for name, value in (("inventor", inventor), ("assignee", assignee), ("country", country),
                        ("language", language), ("status", STATUSES.get(status, status) if status else None),
                        ("type", TYPES.get(kind, kind) if kind else None), ("sort", SORTS.get(sort, sort) if sort else None)):
        if value:
            parts.append((name, value))
    if litigation is not None:
        parts.append(("litigation", "YES" if litigation else "NO"))
    return "&".join(f"{k}={quote(str(v), safe='')}" for k, v in parts)


def _image(path):
    return IMAGE_BASE + path if path and not path.startswith("http") else (path or None)


def _row(item):
    p = item.get("patent") or {}
    ident = item.get("id") or ""
    number = p.get("publication_number") or ident.split("/")[1] if "/" in ident else p.get("publication_number")
    return {
        "id": number,
        "title": clean_text(p.get("title")),
        "link": f"{fetch.PATENTS_SITE}/{ident}" if ident else None,
        "snippet": clean_text(p.get("snippet")),
        "language": p.get("language"),
        "inventors": [clean_text(x) for x in re.split(r",\s*", p.get("inventor") or "") if clean_text(x)],
        "assignees": [clean_text(x) for x in re.split(r",\s*", p.get("assignee") or "") if clean_text(x)],
        "dates": {
            "priority": p.get("priority_date") or None,
            "filing": p.get("filing_date") or None,
            "publication": p.get("publication_date") or None,
            "grant": p.get("grant_date") or None,
        },
        "pdf_link": _image(p.get("pdf")),
        "thumbnail": _image(p.get("thumbnail")),
        "figures": [{"thumbnail": _image(f.get("thumbnail")), "image": _image(f.get("full"))}
                    for f in p.get("figures") or [] if isinstance(f, dict)],
        "position": to_int(item.get("rank")),
    }


def search(query, page=1, num=DEFAULT_NUM, before=None, after=None, date_field="priority", inventor=None,
           assignee=None, country=None, language=None, status=None, kind=None, sort="relevance", litigation=None):
    """Search patents; num in PAGE_SIZES."""
    inner = _search_query(query, num, page, before, after, date_field, inventor, assignee, country, language,
                          status, kind, sort, litigation)
    data = fetch.get_json(f"{fetch.PATENTS_SITE}/xhr/query", params={"url": inner, "exp": ""}, label="patents search",
                          headers={**fetch.JSON_HEADERS, "referer": f"{fetch.PATENTS_SITE}/?{inner}"})
    results = (data or {}).get("results") or {}
    rows = []
    for cluster in results.get("cluster") or []:
        for item in cluster.get("result") or []:
            if isinstance(item, dict):
                rows.append(_row(item))
    for i, r in enumerate(rows):
        r["position"] = (page - 1) * num + i + 1
    total = to_int(results.get("total_num_results"))
    return paged_result("patents", rows, page, num, total=total, query=query,
                        filters={"before": before, "after": after, "date_field": date_field, "inventor": inventor,
                                 "assignee": assignee, "country": country, "language": language, "status": status,
                                 "type": kind, "sort": sort, "litigation": litigation})


# ---- details ---------------------------------------------------------------------------------

def resolve_patent(value):
    """'US10000000B2' | 'US 10,000,000 B2' | patents.google.com/patent/<id>[/<lang>] -> (id, lang)."""
    value = (value or "").strip()
    lang = "en"
    if "patents.google" in value or value.startswith(("http://", "https://")):
        parts = [p for p in urlparse(value if "://" in value else "https://" + value).path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "patent":
            value = parts[1]
            if len(parts) >= 3 and re.fullmatch(r"[a-z]{2}", parts[2]):
                lang = parts[2]
        else:
            raise ValueError("patent link must look like patents.google.com/patent/<publication number>")
    ident = re.sub(r"[\s,\-/]", "", value).upper()
    if not _PATENT_ID_RE.match(ident):
        raise ValueError("patent must be a publication number (e.g. US10000000B2) or a patents.google.com link")
    return ident, lang


def _lxml(html):
    from lxml import html as lxml_html
    return lxml_html.fromstring(html)


def _text(el):
    return clean_text(el.text_content()) if el is not None else None


def _first(root, xpath):
    found = root.xpath(xpath)
    return found[0] if found else None


def _prop(root, name):
    return _text(_first(root, f'.//*[@itemprop="{name}"]'))


def _props(root, name):
    return [t for t in (_text(e) for e in root.xpath(f'.//*[@itemprop="{name}"]')) if t]


def _date(el):
    if el is None:
        return None
    t = el.get("datetime") or _text(el)
    return t or None


def _reference_rows(root, name):
    out = []
    for tr in root.xpath(f'.//tr[@itemprop="{name}"]'):
        number = _prop(tr, "publicationNumber")
        if not number:
            continue
        out.append({
            "id": number,
            "title": _prop(tr, "title"),
            "link": f"{fetch.PATENTS_SITE}/patent/{number}/{_prop(tr, 'primaryLanguage') or 'en'}",
            "priority_date": _prop(tr, "priorityDate"),
            "publication_date": _prop(tr, "publicationDate"),
            "assignee": _prop(tr, "assigneeOriginal"),
            "is_examiner_cited": bool(tr.xpath('.//*[@itemprop="examinerCited"]')),
        })
    return out


def _events(root):
    out = []
    for dd in root.xpath('.//dd[@itemprop="events"]'):
        out.append({
            "date": _date(_first(dd, './/time[@itemprop="date"]')),
            "title": _prop(dd, "title"),
            "type": _prop(dd, "type"),
            "is_critical": bool(dd.xpath('.//*[@itemprop="critical"]')),
            "link": _prop(dd, "externalLink"),
            "notes": _props(dd, "description"),
        })
    return out


def _classifications(root):
    out, seen = [], set()
    for li in root.xpath('.//*[@itemprop="classifications"]'):
        code = _prop(li, "Code")
        desc = _prop(li, "Description")
        leaf = bool(li.xpath('.//*[@itemprop="Leaf"]'))
        if code and (code, desc) not in seen:
            seen.add((code, desc))
            out.append({"code": code, "description": desc, "is_leaf": leaf})
    return out


def _concepts(root):
    out = []
    for li in root.xpath('.//*[@itemprop="match"]'):
        name = _prop(li, "name")
        if name:
            out.append({"name": name, "domain": _prop(li, "domain"), "similarity": _prop(li, "similarity"),
                        "sections": _props(li, "sections")})
    return out


def _paragraphs(section):
    if section is None:
        return None
    text = _text(section)
    return text


def details(patent):
    """Everything on a patent page."""
    ident, lang = resolve_patent(patent)
    html, final = fetch.get_html(f"{fetch.PATENTS_SITE}/patent/{ident}/{lang}", label="patent page")
    root = _lxml(html)
    if not root.xpath('//*[@itemprop="abstract"]') and not root.xpath('//*[@itemprop="claims"]') and "patent" not in (root.findtext(".//title") or "").lower():
        raise fetch.GoogleNotFound(f"patent {ident} not found on Google Patents")
    metas = {}
    for m in root.xpath('//meta[@name]'):
        metas.setdefault(m.get("name"), []).append(m.get("content"))
    pdf = (metas.get("citation_pdf_url") or [None])[0]
    similar = []
    for tr in root.xpath('.//tr[@itemprop="similarDocuments"]'):
        number = _prop(tr, "publicationNumber")
        if number:
            similar.append({"id": number, "title": _prop(tr, "title"), "publication_date": _prop(tr, "publicationDate"),
                            "assignee": _prop(tr, "assigneeOriginal"),
                            "link": f"{fetch.PATENTS_SITE}/patent/{number}/{_prop(tr, 'primaryLanguage') or 'en'}",
                            "is_scholar": _prop(tr, "scholarTitle") is not None})
    family = []
    for tr in root.xpath('.//tr[@itemprop="family"]'):
        number = _prop(tr, "publicationNumber")
        if number:
            family.append({"id": number, "title": _prop(tr, "title"), "publication_date": _prop(tr, "publicationDate"),
                           "priority_date": _prop(tr, "priorityDate"), "assignee": _prop(tr, "assigneeOriginal"),
                           "link": f"{fetch.PATENTS_SITE}/patent/{number}/{_prop(tr, 'primaryLanguage') or 'en'}"})
    applications = []
    for li in root.xpath('.//*[@itemprop="priorityApps"]'):
        applications.append({"number": _prop(li, "applicationNumber"), "date": _prop(li, "priorityDate"),
                             "title": _prop(li, "title"), "is_representative": bool(li.xpath('.//*[@itemprop="representativePublication"]'))})
    legal = []
    for tr in root.xpath('.//tr[@itemprop="legalEvents"]'):
        legal.append({"date": _prop(tr, "date"), "code": _prop(tr, "code"), "title": _prop(tr, "title"),
                      "description": _prop(tr, "description")})
    images = [{"image": e.get("content") or e.text, } for e in root.xpath('.//meta[@itemprop="full"]')]
    figures = []
    for li in root.xpath('.//*[@itemprop="images"]'):
        full = _first(li, './/meta[@itemprop="full"]')
        thumb = _first(li, './/img[@itemprop="thumbnail"]')
        figures.append({"image": full.get("content") if full is not None else None,
                        "thumbnail": thumb.get("src") if thumb is not None else None})
    return {
        "id": ident,
        "title": _prop(root, "title") or clean_text((metas.get("DC.title") or [None])[0]),
        "link": final,
        "pdf_link": pdf,
        "language": lang,
        "abstract": _paragraphs(_first(root, './/*[@itemprop="abstract"]')),
        "status": _prop(root, "ifiStatus") or _prop(root, "status"),
        "country": _prop(root, "countryCode"),
        "application_number": _prop(root, "applicationNumber") or (metas.get("citation_patent_application_number") or [None])[0],
        "inventors": _props(root, "inventor"),
        "assignees": {"original": _props(root, "assigneeOriginal")[:1] or [], "current": _props(root, "assigneeCurrent")},
        "dates": {
            "priority": _prop(root, "priorityDate"),
            "filing": _prop(root, "filingDate"),
            "publication": _prop(root, "publicationDate"),
            "grant": _prop(root, "grantDate"),
            "expiration": _date(_first(root, './/*[@itemprop="expiration"]')) or _date(_first(root, './/time[@itemprop="expiration"]')),
        },
        "prior_art_keywords": _props(root, "priorArtKeywords"),
        "classifications": _classifications(root),
        "concepts": _concepts(root),
        "claims": _paragraphs(_first(root, './/section[@itemprop="claims"]')),
        "description": _paragraphs(_first(root, './/section[@itemprop="description"]')),
        "figures": figures or images,
        "timeline": _events(root),
        "legal_events": legal,
        "priority_applications": applications,
        "citations": _reference_rows(root, "backwardReferences"),
        "cited_by": _reference_rows(root, "forwardReferences"),
        "family": family,
        "similar_documents": similar,
        "external_links": [{"title": _text(a), "link": a.get("href")} for a in root.xpath('.//*[@itemprop="externalLinks"]//a[@href]')],
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "details":
        out = details(sys.argv[2] if len(sys.argv) > 2 else "US10000000B2")
        out["description"] = (out["description"] or "")[:300]
        out["claims"] = (out["claims"] or "")[:300]
    else:
        out = search(sys.argv[1] if len(sys.argv) > 1 else "machine learning", num=10)
    print(json.dumps(out, indent=2, ensure_ascii=False)[:8000])
