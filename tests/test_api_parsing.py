"""API 层解析函数与模拟网络调用测试（离线，Bangumi API）。"""

import asyncio
import json
import unittest
from unittest.mock import patch

import requests

from bilibili_bangumi import config
from bilibili_bangumi.api import bangumi as api_module
from bilibili_bangumi.models.bangumi import SeasonDetail

from tests.fixtures import (
    CALENDAR_DATA,
    CHARACTERS_RESPONSE,
    EPISODES_RESPONSE,
    RANK_LIST_DATA,
    RANK_RESPONSE_DATA,
    SEARCH_EMPTY_DATA,
    SEARCH_RESULT_DATA,
    SUBJECT_DETAIL_DATA,
)


def run(coro):
    """同步地运行一个协程（测试环境一次性事件循环）。"""
    return asyncio.run(coro)


class TestParseSearchPage(unittest.TestCase):
    """搜索响应解析测试。"""

    def test_normal(self) -> None:
        page = api_module.parse_search_page(SEARCH_RESULT_DATA, page=1, page_size=10)
        self.assertEqual(len(page.items), 3)
        self.assertEqual(page.total_results, 58)
        self.assertEqual(page.total_pages, 6)
        self.assertEqual(page.items[0].title, "刀剑神域")
        self.assertEqual(page.items[0].score, 8.8)

    def test_empty(self) -> None:
        page = api_module.parse_search_page(SEARCH_EMPTY_DATA, page=1, page_size=10)
        self.assertTrue(page.empty)
        self.assertEqual(page.total_pages, 1)

    def test_none(self) -> None:
        page = api_module.parse_search_page(None, page=1, page_size=10)
        self.assertTrue(page.empty)

    def test_search_bangumi_posts_search_subjects(self) -> None:
        """search_bangumi 应调用 POST /v0/search/subjects 并解析结果。"""
        captured: dict = {}

        def fake_post(url, body=None):
            captured["url"] = url
            captured["body"] = body
            return SEARCH_RESULT_DATA

        with patch.object(api_module, "_post_json", side_effect=fake_post):
            page = run(api_module.search_bangumi("刀剑", page=2))
        self.assertIn("/v0/search/subjects", captured["url"])
        body = captured["body"]
        self.assertEqual(body["keyword"], "刀剑")
        self.assertEqual(body["sort"], "match")
        self.assertEqual(body["limit"], config.SEARCH_PAGE_SIZE)
        self.assertEqual(body["offset"], 10)  # 第 2 页
        self.assertEqual(body["filter"]["type"], [2, 6])
        self.assertEqual(page.items[0].season_id, 33346)

    def test_search_empty_keyword(self) -> None:
        with self.assertRaises(api_module.BangumiQueryError):
            run(api_module.search_bangumi("   "))


class TestParseSeasonView(unittest.TestCase):
    """条目详情解析测试（Bangumi Subject + Episodes）。"""

    def test_full_view(self) -> None:
        detail = api_module.parse_season_view(
            SUBJECT_DETAIL_DATA, episodes_data=EPISODES_RESPONSE
        )
        self.assertIsInstance(detail, SeasonDetail)
        self.assertEqual(detail.season_id, 33346)
        self.assertEqual(detail.title, "刀剑神域")
        self.assertEqual(detail.original_title, "ソードアート・オンライン")
        self.assertEqual(detail.status, "已完结")  # infobox 含“放送结束”
        self.assertEqual(detail.score, 8.8)
        self.assertEqual(detail.score_count, 12345)
        self.assertEqual(detail.areas, ["日本"])
        self.assertEqual(detail.episode_total, 25)
        self.assertEqual(detail.follows, 365)  # collection.* 求和
        self.assertIsNone(detail.views)
        self.assertEqual(len(detail.episodes), 2)
        self.assertEqual(detail.episodes[0].display_title, "剑的世界")
        self.assertEqual(len(detail.casts), 0)  # Bangumi v0 无声优表
        roles = {s.role for s in detail.staff}
        self.assertIn("原作", roles)
        self.assertIn("导演", roles)
        self.assertTrue(detail.share_url.endswith("/subject/33346"))

    def test_without_episodes_data(self) -> None:
        detail = api_module.parse_season_view(SUBJECT_DETAIL_DATA)
        self.assertEqual(detail.title, "刀剑神域")
        self.assertEqual(detail.episodes, [])

    def test_subject_eps_list_as_episodes(self) -> None:
        # 个别响应把分集直接放在 eps 数组里
        subject = dict(SUBJECT_DETAIL_DATA)
        subject["eps"] = [
            {"id": 1, "name_cn": "剑的世界", "ep": 1, "airdate": "2012-07-08"}
        ]
        detail = api_module.parse_season_view(subject)
        self.assertEqual(len(detail.episodes), 1)

    def test_bad_data(self) -> None:
        detail = api_module.parse_season_view("not a dict")
        self.assertIsNone(detail.season_id)
        self.assertEqual(detail.title, "")

    def test_get_season_detail_fetches_subject_and_episodes(self) -> None:
        """get_season_detail 应请求 /v0/subjects/{id} 与 /v0/episodes。"""
        urls: list = []
        params_seen: list = []

        def fake_get(url, params=None):
            urls.append(url)
            params_seen.append(params or {})
            if url.endswith("/episodes"):
                return EPISODES_RESPONSE
            return SUBJECT_DETAIL_DATA

        with patch.object(api_module, "_get_json", side_effect=fake_get):
            detail = run(api_module.get_season_detail(33346))
        self.assertEqual(detail.title, "刀剑神域")
        self.assertEqual(len(detail.episodes), 2)
        self.assertTrue(any(url.endswith("/subjects/33346") for url in urls))
        self.assertTrue(any(url.endswith("/episodes") for url in urls))
        # 分集接口：/v0/episodes?subject_id=…
        ep_call = [
            (u, p) for u, p in zip(urls, params_seen) if u.endswith("/episodes")
        ]
        self.assertTrue(ep_call)
        self.assertEqual(ep_call[0][1].get("subject_id"), 33346)

    def test_get_season_detail_episodes_failure_ignored(self) -> None:
        """分集接口失败不影响主体详情。"""

        def fake_get(url, params=None):
            if url.endswith("/episodes"):
                raise api_module.BangumiHTTPError(500, "boom")
            return SUBJECT_DETAIL_DATA

        with patch.object(api_module, "_get_json", side_effect=fake_get):
            detail = run(api_module.get_season_detail(33346))
        self.assertEqual(detail.title, "刀剑神域")
        self.assertEqual(detail.episodes, [])

    def test_get_season_detail_404(self) -> None:
        def fake_get(url, params=None):
            raise api_module.BangumiHTTPError(404, "not found")

        with patch.object(api_module, "_get_json", side_effect=fake_get):
            with self.assertRaises(api_module.BangumiHTTPError):
                run(api_module.get_season_detail(999999999))

    def test_parse_character_casts(self) -> None:
        """角色列表 -> “角色（中文）/声优” 解析。"""
        casts = api_module.parse_character_casts(CHARACTERS_RESPONSE)
        self.assertEqual(len(casts), 2)  # 无配音角色被跳过
        self.assertEqual(casts[0].role, "桐谷和人")  # 取 “/ ” 后中文部分
        self.assertEqual(casts[0].name, "松冈祯丞")
        self.assertEqual(casts[1].role, "结城明日奈")
        self.assertEqual(casts[1].name, "户松遥")

    def test_get_season_detail_fetches_casts(self) -> None:
        """动画详情应请求 /characters 并以角色声优填充 casts。"""

        def fake_get(url, params=None):
            if url.endswith("/episodes"):
                return EPISODES_RESPONSE
            if url.endswith("/characters"):
                return CHARACTERS_RESPONSE
            return SUBJECT_DETAIL_DATA

        with patch.object(api_module, "_get_json", side_effect=fake_get):
            detail = run(api_module.get_season_detail(33346))
        self.assertEqual(len(detail.casts), 2)
        self.assertEqual(detail.casts[0].name, "松冈祯丞")

    def test_get_season_detail_casts_failure_ignored(self) -> None:
        """角色接口失败不影响详情其它信息。"""

        def fake_get(url, params=None):
            if url.endswith("/characters"):
                raise api_module.BangumiHTTPError(500, "boom")
            if url.endswith("/episodes"):
                return EPISODES_RESPONSE
            return SUBJECT_DETAIL_DATA

        with patch.object(api_module, "_get_json", side_effect=fake_get):
            detail = run(api_module.get_season_detail(33346))
        self.assertEqual(detail.title, "刀剑神域")
        self.assertEqual(detail.casts, [])


class TestRanking(unittest.TestCase):
    """排行榜解析与获取测试（sort=rank）。"""

    def test_parse_rank_list(self) -> None:
        items = api_module.parse_rank_list(RANK_LIST_DATA)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].rank, 1)
        self.assertEqual(items[0].title, "榜首作品")
        self.assertEqual(items[0].heat_value, 9876543.0)
        self.assertEqual(items[0].heat_kind, "收藏")
        self.assertIsNone(items[2].score)

    @staticmethod
    def _fake_paged(items: list) -> object:
        """模拟 GET /v0/subjects 分页：第一页返回 items，之后抛错结束。"""

        def fake_get(url, params=None):
            if params and params.get("offset", 0) == 0:
                return {"total": len(items), "data": items}
            raise api_module.BangumiHTTPError(500, "no more pages")

        return fake_get

    def test_fetch_ranking_uses_get_subjects(self) -> None:
        """fetch_ranking 通过 GET /v0/subjects?type=&sort=rank 拉取候选池。"""
        captured: dict = {}
        items = [
            {"id": 1, "type": 2, "name_cn": "低热度", "rating": {"score": 8.0, "total": 100}},
            {"id": 2, "type": 2, "name_cn": "高热度", "rating": {"score": 7.5, "total": 900}},
        ]

        def fake_get(url, params=None):
            captured["url"] = url
            captured["params"] = params
            if params and params.get("offset", 0) == 0:
                return {"total": len(items), "data": items}
            raise api_module.BangumiHTTPError(500, "end")

        with patch.object(api_module, "_get_json", side_effect=fake_get):
            result = run(api_module.fetch_ranking(config.RANK_CATEGORIES[0]))
        self.assertTrue(captured["url"].endswith("/subjects"))
        self.assertEqual(captured["params"]["type"], 2)
        self.assertEqual(captured["params"]["sort"], "rank")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].title, "高热度")  # 默认“热度”=评分人数降序
        self.assertEqual(result[0].heat_kind, "热度")

    def test_fetch_ranking_sort_by_score(self) -> None:
        items = [
            {"id": 1, "type": 2, "name_cn": "8分", "rating": {"score": 8.0, "total": 10}},
            {"id": 2, "type": 2, "name_cn": "9分", "rating": {"score": 9.0, "total": 10}},
            {"id": 3, "type": 2, "name_cn": "无评分", "rating": {}},
        ]
        with patch.object(api_module, "_get_json",
                          side_effect=self._fake_paged(items)):
            result = run(
                api_module.fetch_ranking(config.RANK_CATEGORIES[0], sort_key="评分")
            )
        titles = [it.title for it in result]
        self.assertEqual(titles, ["9分", "8分", "无评分"])
        self.assertEqual(result[0].heat_kind, "评分")

    def test_fetch_ranking_sort_by_collect(self) -> None:
        items = [
            {"id": 1, "type": 2, "name_cn": "少收藏",
             "collection": {"wish": 1, "collect": 2, "doing": 0, "on_hold": 0, "dropped": 0}},
            {"id": 2, "type": 2, "name_cn": "多收藏",
             "collection": {"wish": 10, "collect": 20, "doing": 5, "on_hold": 1, "dropped": 1}},
        ]
        with patch.object(api_module, "_get_json",
                          side_effect=self._fake_paged(items)):
            result = run(
                api_module.fetch_ranking(config.RANK_CATEGORIES[0], sort_key="收藏数")
            )
        self.assertEqual([it.title for it in result], ["多收藏", "少收藏"])
        self.assertEqual(result[0].heat_kind, "收藏数")

    def test_fetch_ranking_region_filter_for_guochuang(self) -> None:
        """国漫：在候选池中按条目地区标签过滤。"""
        cn_item = {
            "id": 3001, "type": 2, "name_cn": "中国作品",
            "tags": [{"name": "中国", "count": 1}], "rating": {"total": 5},
        }
        other = {"id": 3002, "type": 2, "name_cn": "日本作品",
                 "tags": [{"name": "日本", "count": 1}], "rating": {"total": 9}}
        guochuang = next(
            cat for cat in config.RANK_CATEGORIES if cat.get("short") == "国漫"
        )
        with patch.object(api_module, "_get_json",
                          side_effect=self._fake_paged([cn_item, other])):
            items = run(api_module.fetch_ranking(guochuang))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "中国作品")

    def test_fetch_ranking_region_filter_empty_raises(self) -> None:
        """地区过滤后为空时给出明确提示，而不是混入他国条目。"""
        sb = {"id": 7777, "type": 2, "name_cn": "海绵宝宝",
              "meta_tags": ["美国", "欧美"], "rating": {"total": 99}}
        guochuang = next(
            cat for cat in config.RANK_CATEGORIES if cat.get("short") == "国漫"
        )
        with patch.object(api_module, "_get_json",
                          side_effect=self._fake_paged([sb])):
            with self.assertRaises(api_module.BangumiQueryError) as ctx:
                run(api_module.fetch_ranking(guochuang))
        self.assertIn("暂无该地区条目", str(ctx.exception))

    def test_fetch_ranking_all_empty_raises(self) -> None:
        def fake_get(url, params=None):
            return {"total": 0, "data": []}

        with patch.object(api_module, "_get_json", side_effect=fake_get):
            with self.assertRaises(api_module.BangumiQueryError):
                run(api_module.fetch_ranking(config.RANK_CATEGORIES[0]))


class TestTimeline(unittest.TestCase):
    """追番日历（/v0/calendar）解析测试。"""

    def test_parse_calendar_shape(self) -> None:
        days = api_module.parse_timeline(CALENDAR_DATA)
        self.assertEqual(len(days), 3)
        # 按 周一(=1) … 周日(=7) 排序
        weekdays = [d.weekday for d in days]
        self.assertEqual(weekdays, [1, 2, 7])
        self.assertEqual(days[0].weekday_cn, "周一")
        self.assertEqual(days[0].items[0].title, "周一更新作品")
        self.assertEqual(days[2].weekday_cn, "周日")

    def test_parse_none(self) -> None:
        self.assertEqual(api_module.parse_timeline(None), [])
        self.assertEqual(api_module.parse_timeline({}), [])
        self.assertEqual(api_module.parse_timeline([]), [])

    def test_fetch_timeline_calls_calendar(self) -> None:
        """fetch_timeline 应请求 GET /v0/calendar。"""
        urls: list = []

        def fake_get(url, params=None):
            urls.append(url)
            return CALENDAR_DATA

        with patch.object(api_module, "_get_json", side_effect=fake_get):
            days = run(api_module.fetch_timeline(config.SEASON_TYPE_ANIME))
        self.assertTrue(any(url.endswith("/calendar") for url in urls))
        self.assertEqual(len(days), 3)

    def test_fetch_timeline_unfiltered_fallback(self) -> None:
        """影视类型（三次元）过滤无结果时应退回完整日历。"""

        def fake_get(url, params=None):
            return CALENDAR_DATA  # 全部为动画数据

        with patch.object(api_module, "_get_json", side_effect=fake_get):
            days = run(api_module.fetch_timeline(3))  # 影视
        self.assertEqual(len(days), 3)  # 无三次元数据 -> 退回全部


    def test_fetch_airing_episode_number(self) -> None:
        """按放送日期解析“更新集数”（追番日历用）。"""
        with patch.object(
            api_module, "_fetch_episodes", return_value=EPISODES_RESPONSE
        ):
            n1 = run(api_module.fetch_airing_episode_number(33346, "2012-07-08"))
            n2 = run(api_module.fetch_airing_episode_number(33346, "2012-07-15"))
            n3 = run(api_module.fetch_airing_episode_number(33346, "2000-01-01"))
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 2)
        self.assertIsNone(n3)


    def test_fetch_first_episode_date(self) -> None:
        """首话日期：正片分集里最早的 airdate（追番日历“开播时间”）。"""
        with patch.object(
            api_module,
            "_fetch_episodes",
            side_effect=[EPISODES_RESPONSE, None],
        ):
            d1 = run(api_module.fetch_first_episode_date(33346))
            d2 = run(api_module.fetch_first_episode_date(1))
        self.assertEqual(d1, "2012-07-08")
        self.assertIsNone(d2)


class TestDescribeError(unittest.TestCase):
    """错误描述测试。"""

    def test_http_404(self) -> None:
        msg = api_module.describe_error(api_module.BangumiHTTPError(404, "x"))
        self.assertIn("404", msg)
        self.assertIn("不存在", msg)

    def test_http_500(self) -> None:
        msg = api_module.describe_error(api_module.BangumiHTTPError(503, "busy"))
        self.assertIn("服务暂时不可用", msg)

    def test_http_429(self) -> None:
        msg = api_module.describe_error(api_module.BangumiHTTPError(429, "x"))
        self.assertIn("过于频繁", msg)

    def test_network_error(self) -> None:
        msg = api_module.describe_error(api_module.BangumiNetworkError("timeout"))
        self.assertIn("网络请求失败", msg)

    def test_custom_error(self) -> None:
        msg = api_module.describe_error(api_module.BangumiQueryError("自定义问题"))
        self.assertEqual(msg, "自定义问题")

    def test_unknown(self) -> None:
        self.assertIn("未知错误", api_module.describe_error(ValueError("boom")))


class _FakeResponse:
    """极简 requests.Response 替身（用于 _request 单测）。"""

    def __init__(self, status_code: int = 200, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = (
            json.dumps(payload, ensure_ascii=False)
            if payload is not None and not isinstance(payload, bytes)
            else (payload if isinstance(payload, str) else "")
        )

    def json(self):  # noqa: ANN201
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class TestNetworkHardening(unittest.TestCase):
    """网络加固行为测试（重试 / 超时 / 无重试场景，均不访问真实网络）。"""

    def _call_get(self) -> object:
        return api_module._get_json("https://example.invalid/v0/test")

    def test_connect_timeout_retries_once(self) -> None:
        """连接超时后应自动重试一次并成功。"""
        calls: list = []

        def fake_request(method, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise requests.exceptions.ConnectTimeout("connect timeout")
            return _FakeResponse(200, {"ok": True})

        with patch.object(api_module._SESSION, "request", side_effect=fake_request):
            result = api_module._get_json("https://example.invalid/v0/test")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_read_timeout_retries_once_then_fails(self) -> None:
        """读取超时重试仍失败时抛出 BangumiNetworkError。"""
        def fake_request(method, url, **kwargs):
            raise requests.exceptions.ReadTimeout("read timeout")

        with patch.object(api_module._SESSION, "request", side_effect=fake_request):
            with self.assertRaises(api_module.BangumiNetworkError) as ctx:
                api_module._get_json("https://example.invalid/v0/test")
        self.assertIn("读取响应超时", str(ctx.exception))

    def test_http503_retries_then_succeeds(self) -> None:
        """HTTP 503 应自动重试，第二次返回 200 成功。"""
        calls: list = []

        def fake_request(method, url, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return _FakeResponse(503, None)
            return _FakeResponse(200, {"ok": True})

        with patch.object(api_module._SESSION, "request", side_effect=fake_request):
            result = api_module._get_json("https://example.invalid/v0/test")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_http404_does_not_retry(self) -> None:
        """404（数据不存在）不应重试，直接抛出 BangumiHTTPError。"""
        calls: list = []

        def fake_request(method, url, **kwargs):
            calls.append(1)
            return _FakeResponse(404, None)

        with patch.object(api_module._SESSION, "request", side_effect=fake_request):
            with self.assertRaises(api_module.BangumiHTTPError) as ctx:
                api_module._get_json("https://example.invalid/v0/subjects/1")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(len(calls), 1)

    def test_invalid_json_raises_without_retry(self) -> None:
        """HTTP 200 但内容非 JSON：直接抛 BangumiQueryError 且不重试。"""
        calls: list = []

        def fake_request(method, url, **kwargs):
            calls.append(1)
            return _FakeResponse(200, None)

        with patch.object(api_module._SESSION, "request", side_effect=fake_request):
            with self.assertRaises(api_module.BangumiQueryError):
                api_module._get_json("https://example.invalid/v0/test")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
