"""The 26 Google Search endpoints. Every path is served with and without the
`/google-search` prefix, so code generated against the hosted API on
RapidAPI (paths like /search) runs unchanged against this server.

Params are validated by the marshmallow schemas in google_search/schemas.py
(the same ones the hosted API uses); failures map to HTTP like this:
bad params -> 400, not found -> 404, Google blocked / upstream error -> 502,
anything else -> 500.
"""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from google_search import autocomplete, finance, news, patents, schemas, serp, trends
from schema_fields import load_query
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

PREFIX = "/google-search"

# (public path, schema, function, paginated) — in the order of the docs
ENDPOINTS = [
    ("/search", schemas.SearchSchema, serp.search, True),
    ("/autocomplete", schemas.AutocompleteSchema, autocomplete.autocomplete, False),
    ("/search/light", schemas.SearchLightSchema, serp.search_light, True),
    ("/ai-overview", schemas.QueryOnlySchema, serp.ai_overview, False),
    ("/people-also-ask", schemas.QueryOnlySchema, serp.people_also_ask, False),
    ("/images", schemas.ImagesSchema, serp.images, False),
    ("/videos", schemas.VideosSchema, serp.videos, True),
    ("/news", schemas.SerpNewsSchema, serp.news, True),
    ("/shopping", schemas.ShoppingSchema, serp.shopping, False),
    ("/local", schemas.LocalSchema, serp.local, True),
    ("/jobs", schemas.JobsSchema, serp.jobs, False),
    ("/forums", schemas.ForumsSchema, serp.forums, True),
    ("/books", schemas.BooksSchema, serp.books, True),
    ("/news/search", schemas.NewsSearchSchema, news.search, False),
    ("/news/top-headlines", schemas.TopHeadlinesSchema, news.top_headlines, False),
    ("/news/topic", schemas.TopicHeadlinesSchema, news.topic_headlines, False),
    ("/news/local", schemas.LocalHeadlinesSchema, news.local_headlines, False),
    ("/news/resolve-link", schemas.ArticleSchema, news.resolve_link, False),
    ("/trends/trending", schemas.TrendingSchema, trends.trending, False),
    ("/trends/interest-over-time", schemas.InterestOverTimeSchema, trends.interest_over_time, False),
    ("/trends/interest-by-region", schemas.InterestByRegionSchema, trends.interest_by_region, False),
    ("/trends/related", schemas.RelatedSchema, trends.related, False),
    ("/patents/search", schemas.PatentSearchSchema, patents.search, True),
    ("/patents/details", schemas.PatentSchema, patents.details, False),
    ("/finance/quote", schemas.QuoteSchema, finance.quote, False),
    ("/finance/overview", schemas.OverviewSchema, finance.overview, False),
]


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The query as unicode strings (bottle 0.12's .get() hands back latin-1
    decoded bytes, so a UTF-8 "Amélie" would arrive as "AmÃ©lie")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _page_link(path, params, page):
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "", False)}
    query["page"] = page
    scheme, host = request.urlparts.scheme, request.urlparts.netloc
    return f"{scheme}://{host}{path}?{urlencode(query, doseq=True)}"


def paginate(result, path, raw_params):
    """Flat pagination (count / per_page / current_page / total_pages / next /
    previous first), links rebuilt from the caller's own params."""
    pagination = result.pop("pagination", None) or {}
    result.pop("count", None)
    page = _as_int(pagination.get("page")) or _as_int(raw_params.get("page")) or 1
    total_pages = max(_as_int(pagination.get("total_pages")), 0)
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": _page_link(path, raw_params, page + 1 if page < total_pages else None),
        "previous": _page_link(path, raw_params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def handle(path, schema_cls, impl, paginated):
    label = f"google-search {path.strip('/')}"
    raw = query_dict()
    data, error = load_query(schema_cls, raw)
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"google-search rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Blocked as e:
        return json_response({"error": f"google-search blocked the request, retry later: {e}"}, 502)
    except UpstreamError as e:
        return json_response({"error": f"{label} failed: {e}"}, 502)
    except Exception as e:
        return json_response({"error": f"{label} failed: {type(e).__name__}: {e}"}, 500)
    if paginated:
        result = paginate(result, request.path, raw)
    return json_response(result)


def mount(path, schema_cls, impl, paginated):
    """Serve one endpoint at /path and /google-search/path."""
    def handler():
        return handle(path, schema_cls, impl, paginated)
    handler.__name__ = "google_search_" + path.strip("/").replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route(PREFIX + path, method="GET")(handler)


for _path, _schema, _impl, _paginated in ENDPOINTS:
    mount(_path, _schema, _impl, _paginated)


@route("/")
@route("/health")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
