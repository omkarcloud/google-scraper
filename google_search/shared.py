"""Helpers shared by the google_search endpoint modules: the paged-result
envelope route_glue lifts, value coercion and the locale plumbing."""
import html as htmllib
import re
from datetime import datetime, timezone

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def pagination(page, per_page, total=None, has_more=None):
    if total is not None and per_page:
        total_pages = max((total + per_page - 1) // per_page, 1 if total else 0)
    elif has_more is not None:
        total_pages = page + 1 if has_more else page
    else:
        total_pages = page
    return {"page": page, "items_per_page": per_page, "total_pages": total_pages, "total_count": total}


def paged_result(key, results, page, per_page, total=None, has_more=None, **extra):
    """Endpoint result with a `pagination` block route_glue lifts into the
    flat gateway shape (count / per_page / current_page / total_pages /
    next / previous)."""
    if has_more is None and total is None:
        has_more = len(results) >= per_page
    out = dict(extra)
    out[key] = results
    out["pagination"] = pagination(page, per_page, total, has_more)
    return out


def clean_text(value):
    """Strip tags, unescape entities and collapse whitespace; '' -> None."""
    if value is None:
        return None
    text = _TAG_RE.sub("", str(value))
    text = htmllib.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def to_int(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^\d\-]", "", str(value))
    try:
        return int(digits) if digits not in ("", "-") else None
    except ValueError:
        return None


def to_float(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def at(seq, *path, default=None):
    """Safe nested index into Google's positional arrays: at(d, 0, 3, 1)."""
    cur = seq
    for key in path:
        try:
            cur = cur[key]
        except (IndexError, KeyError, TypeError):
            return default
        if cur is None:
            return default
    return cur


def iso_from_ts(ts):
    """Unix seconds -> ISO-8601 UTC string."""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def iso_date(parts):
    """Google's [year, month, day, ...] date array -> 'YYYY-MM-DD'."""
    if not isinstance(parts, list) or len(parts) < 3:
        return None
    try:
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    except (TypeError, ValueError):
        return None


def locale_tag(language, country):
    """'en' + 'US' -> 'en-US'; a locale given as 'pt-BR' is kept."""
    language = (language or "en").replace("_", "-")
    if "-" in language:
        return language
    return f"{language}-{(country or 'US').upper()}"
