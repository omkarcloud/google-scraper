"""Marshmallow request schemas for every /google-search/* route.

Generic fields live in the shared schema_fields.py; this module adds the
Google resolvers and per-route schemas. Every schema's load() output is
the kwargs dict its endpoint function takes.

ONE param per input (tripadvisor QueryOrLinkField convention, never a
sibling `url`/`id` pair): `query` takes a search term OR a google.com/search
link (whose gl / hl / start / tbs are honoured when the matching params
are absent), `patent` a publication number OR a patents.google.com link,
`symbol` TICKER:EXCHANGE OR a google.com/finance/quote link, `article` a
Google News id OR its link, `topic` a topic name OR a topics/<id> link,
`location` a Google Ads canonical name OR a ready uule token.
"""
from marshmallow import ValidationError, post_load, pre_load, validate

from google_search import autocomplete as ac, finance, news, patents, refs, serp, trends
from schema_fields import (BaseSchema, ChoiceField, CommaListField, CountryCodeField, DateField, Flag,
                           LanguageCodeField, LimitField, PageField, PositiveInt, Price, QueryField, RefField,
                           StrippedString)


# ---- fields ---------------------------------------------------------------------------------------------------

class SearchQueryField(RefField):
    resolver = staticmethod(refs.resolve_query)


class PatentRefField(RefField):
    resolver = staticmethod(lambda v: patents.resolve_patent(v)[0])


class SymbolField(RefField):
    resolver = staticmethod(finance.resolve_symbol)


class ArticleRefField(RefField):
    resolver = staticmethod(refs.resolve_article)


class TopicRefField(RefField):
    resolver = staticmethod(refs.resolve_topic)


class LocationField(RefField):
    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        kwargs.setdefault("validate", validate.Length(max=63))
        super().__init__(resolver=refs.resolve_location, **kwargs)


class KeywordsField(CommaListField):
    """Up to 5 comma-separated Trends keywords, case kept."""

    def __init__(self, **kwargs):
        from marshmallow import missing
        kwargs.setdefault("max_items", trends.MAX_KEYWORDS)
        kwargs.pop("required", True)
        super().__init__(upper=False, **kwargs)
        # CommaListField defaults to optional; this one is mandatory.
        self.required = True
        self.load_default = missing

    def _deserialize(self, value, attr, data, **kwargs):
        items = super()._deserialize(value, attr, data, **kwargs)
        if not items:
            raise ValidationError("Must list at least one keyword.")
        return items


class TimeframeField(StrippedString):
    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", "past_12_months")
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return "past_12_months"
        try:
            trends.timeframe_token(value)
        except ValueError as e:
            raise ValidationError(str(e))
        return value


class CountryField(CountryCodeField):
    """ISO country, default US ("UK" accepted for GB)."""

    def __init__(self, default="US", **kwargs):
        kwargs.setdefault("load_default", default)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, str) and value.strip().upper() == "UK":
            value = "GB"
        return super()._deserialize(value, attr, data, **kwargs)


class LanguageField(LanguageCodeField):
    def __init__(self, default="en", **kwargs):
        kwargs.setdefault("load_default", default)
        super().__init__(**kwargs)


class NumField(PositiveInt):
    """Results per page (1-100), default 10."""

    def __init__(self, default=serp.DEFAULT_NUM, max_value=serp.MAX_NUM, **kwargs):
        kwargs.setdefault("load_default", default)
        super().__init__(max_value=max_value, **kwargs)


# ---- SERP ------------------------------------------------------------------------------------------------------

class _SerpBase(BaseSchema):
    query = SearchQueryField()
    country = CountryField()
    language = LanguageField()
    location = LocationField()
    resolve = Flag(load_default=True, data_key="resolve_links")

    @pre_load
    def from_link(self, data, **kwargs):
        """A pasted google.com/search link fills country / language / page."""
        link_params = refs.params_of(data.get("query") or "")
        if not link_params:
            return data
        data = dict(data)
        if "gl" in link_params and not data.get("country"):
            data["country"] = link_params["gl"]
        if "hl" in link_params and not data.get("language"):
            data["language"] = link_params["hl"].split("-")[0]
        if "start" in link_params and not data.get("page") and "page" in self.fields:
            try:
                num = int(data.get("num") or serp.DEFAULT_NUM)
                data["page"] = str(int(link_params["start"]) // num + 1)
            except ValueError:
                pass
        return data


class _Paged(_SerpBase):
    page = PageField(max_page=50)
    num = NumField()


class _Dated(BaseSchema):
    time_period = ChoiceField(list(serp.TIME_PERIODS), load_default=None)
    date_from = DateField()
    date_to = DateField()

    @post_load
    def date_order(self, data, **kwargs):
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            raise ValidationError({"date_to": ["Must be after date_from."]})
        return data


class SearchSchema(_Paged, _Dated):
    safe = ChoiceField(list(serp.SAFE), load_default=None)
    no_autocorrect = Flag()
    no_duplicates = Flag()
    results_language = LanguageCodeField(allow_locale=False)
    results_country = CountryCodeField()


class SearchLightSchema(_Paged, _Dated):
    safe = ChoiceField(list(serp.SAFE), load_default=None)
    no_autocorrect = Flag()
    results_language = LanguageCodeField(allow_locale=False)
    results_country = CountryCodeField()


class QueryOnlySchema(_SerpBase):
    pass


class ImagesSchema(_SerpBase):
    size = ChoiceField(list(serp.IMAGE_SIZES), load_default=None)
    color = ChoiceField(list(serp.IMAGE_COLORS), load_default=None)
    image_type = ChoiceField(list(serp.IMAGE_TYPES), load_default=None, data_key="type")
    aspect_ratio = ChoiceField(list(serp.IMAGE_ASPECTS), load_default=None)
    file_type = ChoiceField(list(serp.IMAGE_FORMATS), load_default=None)
    usage_rights = ChoiceField(list(serp.IMAGE_RIGHTS), load_default=None)
    time_period = ChoiceField(list(serp.TIME_PERIODS), load_default=None)
    safe = ChoiceField(list(serp.SAFE), load_default=None)
    limit = LimitField(default=50, max_size=100)


class VideosSchema(_Paged):
    duration = ChoiceField(list(serp.VIDEO_DURATIONS), load_default=None)
    time_period = ChoiceField(list(serp.TIME_PERIODS), load_default=None)
    safe = ChoiceField(list(serp.SAFE), load_default=None)


class SerpNewsSchema(_Paged, _Dated):
    sort = ChoiceField(list(serp.NEWS_SORTS), load_default="relevance")


class ShoppingSchema(_SerpBase):
    min_price = Price()
    max_price = Price()
    sort = ChoiceField(list(serp.SHOPPING_SORTS), load_default="relevance")
    condition = ChoiceField(list(serp.SHOPPING_CONDITIONS), load_default=None)
    free_shipping = Flag()
    on_sale = Flag()
    limit = LimitField(default=40, max_size=60)

    @post_load
    def price_order(self, data, **kwargs):
        if data.get("min_price") is not None and data.get("max_price") is not None and data["min_price"] > data["max_price"]:
            raise ValidationError({"max_price": ["Must be greater than min_price."]})
        return data


class LocalSchema(_SerpBase):
    page = PageField(max_page=10)


class JobsSchema(_SerpBase):
    pass


class BooksSchema(BaseSchema):
    query = SearchQueryField()
    country = CountryField()
    language = LanguageField()
    page = PageField(max_page=50)
    num = NumField()
    time_period = ChoiceField(list(serp.TIME_PERIODS), load_default=None)
    resolve = Flag(load_default=True, data_key="resolve_links")


class ForumsSchema(_Paged):
    time_period = ChoiceField(list(serp.TIME_PERIODS), load_default=None)


class EmptySchema(BaseSchema):
    pass


# ---- autocomplete -------------------------------------------------------------------------------------------

class AutocompleteSchema(BaseSchema):
    query = QueryField(max_length=100)
    client = ChoiceField(list(ac.CLIENTS), load_default=ac.DEFAULT_CLIENT)
    country = CountryField()
    language = LanguageField()
    limit = LimitField(default=10, max_size=ac.MAX_LIMIT)


# ---- news feeds ---------------------------------------------------------------------------------------------

class _Edition(BaseSchema):
    country = CountryField()
    language = LanguageField()
    limit = LimitField(default=50, max_size=news.MAX_LIMIT)


class TopHeadlinesSchema(_Edition):
    pass


class TopicHeadlinesSchema(_Edition):
    topic = TopicRefField()


class LocalHeadlinesSchema(_Edition):
    location = StrippedString(required=True, validate=validate.Length(min=2, max=100))


class NewsSearchSchema(_Edition):
    query = QueryField()
    time_window = ChoiceField(["any", "1h", "1d", "7d", "1y"], load_default="any")
    source = StrippedString(load_default=None, validate=validate.Length(max=100))


class ArticleSchema(BaseSchema):
    article = ArticleRefField()


# ---- trends ----------------------------------------------------------------------------------------------------

class TrendingSchema(BaseSchema):
    country = CountryField()
    language = LanguageField()
    hours = ChoiceField({str(h): h for h in trends.TRENDING_HOURS}, load_default=24)
    category = ChoiceField({str(k): k for k in trends.TRENDING_CATEGORIES}, load_default=None)
    limit = LimitField(default=50, max_size=200)
    include_news = Flag(load_default=True)

    @post_load
    def ints(self, data, **kwargs):
        data["hours"] = int(data["hours"] or 24)
        if data.get("category") is not None:
            data["category"] = int(data["category"])
        return data


class _Explore(BaseSchema):
    keywords = KeywordsField(required=True)
    country = CountryCodeField()
    timeframe = TimeframeField()
    category = PositiveInt(max_value=2000)
    property = ChoiceField(list(trends.PROPERTIES), load_default="web")
    language = LanguageField()


class InterestOverTimeSchema(_Explore):
    pass


class InterestByRegionSchema(_Explore):
    resolution = ChoiceField(list(trends.RESOLUTIONS), load_default="region")
    limit = LimitField(default=100, max_size=500)


class RelatedSchema(_Explore):
    kind = ChoiceField(["queries", "topics"], load_default="queries", data_key="type")


# ---- patents --------------------------------------------------------------------------------------------------

class PatentSearchSchema(BaseSchema):
    query = QueryField()
    page = PageField(max_page=100)
    num = ChoiceField({str(n): n for n in patents.PAGE_SIZES}, load_default=patents.DEFAULT_NUM)
    before = DateField()
    after = DateField()
    date_field = ChoiceField(list(patents.DATE_FIELDS), load_default="priority")
    inventor = StrippedString(load_default=None, validate=validate.Length(max=100))
    assignee = StrippedString(load_default=None, validate=validate.Length(max=100))
    country = CountryCodeField()
    language = ChoiceField(["ENGLISH", "GERMAN", "CHINESE", "FRENCH", "SPANISH", "ARABIC", "JAPANESE", "KOREAN",
                            "PORTUGUESE", "RUSSIAN", "ITALIAN", "DUTCH", "SWEDISH", "FINNISH", "NORWEGIAN", "DANISH"],
                           load_default=None)
    status = ChoiceField(list(patents.STATUSES), load_default=None)
    kind = ChoiceField(list(patents.TYPES), load_default=None, data_key="type")
    sort = ChoiceField(list(patents.SORTS), load_default="relevance")
    litigation = Flag()

    @post_load
    def ints(self, data, **kwargs):
        data["num"] = int(data["num"])
        if data.get("language"):
            data["language"] = data["language"].upper()
        return data


class PatentSchema(BaseSchema):
    patent = PatentRefField()


# ---- finance --------------------------------------------------------------------------------------------------

class QuoteSchema(BaseSchema):
    symbol = SymbolField()
    include_news = Flag(load_default=True)
    include_financials = Flag(load_default=True)
    include_chart = Flag(load_default=True)


class OverviewSchema(BaseSchema):
    country = CountryField()
    language = LanguageField()
