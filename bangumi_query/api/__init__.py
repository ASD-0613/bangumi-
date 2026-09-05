"""Bangumi API 子包。"""

from bangumi_query.api.bangumi import (  # noqa: F401
    BangumiQueryError,
    apply_network_settings,
    describe_error,
    fetch_ranking,
    fetch_timeline,
    get_season_detail,
    parse_rank_list,
    parse_search_page,
    parse_season_view,
    parse_timeline,
    search_bangumi,
)

__all__ = [
    "BangumiQueryError",
    "apply_network_settings",
    "describe_error",
    "fetch_ranking",
    "fetch_timeline",
    "get_season_detail",
    "parse_rank_list",
    "parse_search_page",
    "parse_season_view",
    "parse_timeline",
    "search_bangumi",
]
