"""Google search suggestions: GET www.google.com/complete/search.

Four client formats, each with something the others lack (validated
2026-09-24, plain curl_cffi, no cookies):

  chrome    ["q", [text...], [desc...], [], {"google:suggestrelevance":
            [...], "google:suggestsubtypes": [[...]], "google:suggesttype":
            ["QUERY"...], "google:verbatimrelevance": N}]  — 15 items with
            relevance scores
  firefox   ["q", [text...], [], {"google:suggestsubtypes": [[...]]}] — 10
  gws-wiz   )]}' [[["pyth<b>on</b>", 0, [512,433]], ["python snake", 46,
            [...], {"zh": "Python", "zi": "Snake", "zs": "<thumb>"}], ...],
            {...}]  — the site's own dropdown, with ENTITY suggestions
            (title / subtitle / thumbnail)
  youtube   window.google.ac.h(["q", [[text, 0, [subtypes]], ...], {"k":1}])
            — YouTube search suggestions (ds=yt)
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_search import fetch  # noqa: E402
from google_search.shared import clean_text  # noqa: E402

CLIENTS = {"chrome": "chrome", "firefox": "firefox", "google": "gws-wiz", "youtube": "youtube"}
DEFAULT_CLIENT = "google"
MAX_LIMIT = 20
_JSONP_RE = re.compile(r"^\s*window\.google\.ac\.h\((.*)\)\s*;?\s*$", re.S)

# gws-wiz suggestion kinds (second element of a suggestion row)
_KINDS = {0: "query", 46: "entity", 33: "entity", 35: "query", 5: "query"}


def _request(query, client, country, language):
    params = {"q": query, "client": client, "hl": language, "gl": country.lower()}
    if client == "youtube":
        params["ds"] = "yt"
    if client == "gws-wiz":
        params["xssi"] = "t"
    return fetch.get_text(f"{fetch.SITE}/complete/search", params=params, label="autocomplete")


def _parse_chrome_like(data, client):
    texts = data[1] if len(data) > 1 and isinstance(data[1], list) else []
    meta = next((x for x in data[2:] if isinstance(x, dict)), {})
    relevance = meta.get("google:suggestrelevance") or []
    subtypes = meta.get("google:suggestsubtypes") or []
    kinds = meta.get("google:suggesttype") or []
    out = []
    for i, text in enumerate(texts):
        if isinstance(text, list):          # youtube rows: [text, 0, [subtypes]]
            row = text
            text = row[0] if row else None
            row_subtypes = row[2] if len(row) > 2 and isinstance(row[2], list) else None
        else:
            row_subtypes = subtypes[i] if i < len(subtypes) and isinstance(subtypes[i], list) else None
        if not text:
            continue
        out.append({
            "position": len(out) + 1,
            "text": clean_text(text),
            "type": (kinds[i].lower() if i < len(kinds) and isinstance(kinds[i], str) else "query"),
            "relevance": relevance[i] if i < len(relevance) and isinstance(relevance[i], (int, float)) else None,
            "subtypes": row_subtypes,
            "entity": None,
        })
    return out, (meta.get("google:verbatimrelevance") if isinstance(meta, dict) else None)


def _parse_gws(data):
    rows = data[0] if data and isinstance(data[0], list) else []
    out = []
    for row in rows:
        if not isinstance(row, list) or not row:
            continue
        text = clean_text(row[0])
        if not text:
            continue
        kind = row[1] if len(row) > 1 and isinstance(row[1], int) else 0
        extra = next((x for x in row[2:] if isinstance(x, dict)), {})
        entity = None
        if extra.get("zh") or extra.get("zi") or extra.get("zs"):
            entity = {"title": extra.get("zh") or None, "subtitle": extra.get("zi") or None,
                      "image": extra.get("zs") or None}
        out.append({
            "position": len(out) + 1,
            "text": text,
            "type": "entity" if entity else _KINDS.get(kind, "query"),
            "relevance": None,
            "subtypes": row[2] if len(row) > 2 and isinstance(row[2], list) else None,
            "entity": entity,
        })
    return out


def autocomplete(query, client=DEFAULT_CLIENT, country="US", language="en", limit=10):
    """Suggestions for a partial query."""
    upstream = CLIENTS.get(client, client)
    text = _request(query, upstream, country, language)
    if upstream == "youtube":
        m = _JSONP_RE.match(text)
        text = m.group(1) if m else text
    data = fetch.parse_json(text, "autocomplete")
    verbatim = None
    if upstream == "gws-wiz":
        items = _parse_gws(data)
    else:
        items, verbatim = _parse_chrome_like(data, upstream)
    items = items[:limit]
    return {
        "query": query,
        "client": client,
        "country": country.upper(),
        "language": language,
        "verbatim_relevance": verbatim,
        "count": len(items),
        "suggestions": items,
    }


if __name__ == "__main__":
    print(json.dumps(autocomplete(sys.argv[1] if len(sys.argv) > 1 else "pyth",
                                  client=sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CLIENT),
                     indent=2, ensure_ascii=False))
