"""数据模型 / 映射函数单测（离线，Bangumi 数据结构）。"""

import unittest

from bangumi_query.models.bangumi import (
    EpisodeInfo,
    RankItem,
    SearchItem,
    TimelineItem,
    bangumi_type_name,
    clean_text,
    derive_status,
    format_time,
    names_of,
    subject_areas,
    subject_cover,
    subject_eps_count,
    subject_infobox_rows,
    subject_staff,
    subject_title,
    to_float,
    to_int,
)

from tests.fixtures import RANK_LIST_DATA, SEARCH_RESULT_DATA, SUBJECT_DETAIL_DATA


class TestTextTools(unittest.TestCase):
    """文本与数值工具函数测试。"""

    def test_clean_text_strips_em_tags(self) -> None:
        self.assertEqual(
            clean_text('<em class="keyword">刀剑神域</em>'), "刀剑神域"
        )
        self.assertEqual(clean_text("鬼灭之刃"), "鬼灭之刃")
        self.assertEqual(clean_text(None), "")
        self.assertEqual(clean_text(123), "123")

    def test_to_int_and_float(self) -> None:
        self.assertEqual(to_int("9"), 9)
        self.assertEqual(to_int(9.8), 9)
        self.assertEqual(to_int("abc"), None)
        self.assertEqual(to_int(None), None)
        self.assertEqual(to_float("9.8"), 9.8)
        self.assertEqual(to_float("abc"), None)
        self.assertEqual(to_float(True), None)  # bool 不计入数值

    def test_format_time(self) -> None:
        self.assertEqual(format_time("2012-07-08"), "2012-07-08")
        self.assertEqual(format_time("2024-10-01"), "2024-10-01")
        self.assertEqual(format_time(""), "")
        self.assertEqual(format_time(None), "")
        # 时间戳 0 -> 1970 年（合法值）
        self.assertTrue(format_time(0).startswith("1970-01-01"))

    def test_names_of_variants(self) -> None:
        self.assertEqual(names_of(["日本"]), ["日本"])
        self.assertEqual(names_of([{"name": "日本", "count": 3}]), ["日本"])
        self.assertEqual(names_of({"name": "热血"}), ["热血"])
        self.assertEqual(names_of("日本"), ["日本"])
        self.assertEqual(names_of(None), [])
        self.assertEqual(names_of([{"id": 1}, "中国"]), ["中国"])

    def test_derive_status_bangumi_absent(self) -> None:
        # Bangumi 不提供状态字段 -> None；保留对旧字段的兼容推断
        self.assertEqual(derive_status({"is_finish": 1}), "已完结")
        self.assertEqual(derive_status({}), None)
        self.assertEqual(derive_status(SUBJECT_DETAIL_DATA), None)

    def test_bangumi_type_name(self) -> None:
        self.assertEqual(bangumi_type_name(2), "动画")
        self.assertEqual(bangumi_type_name(6), "三次元")
        self.assertEqual(bangumi_type_name("2"), "动画")
        self.assertEqual(bangumi_type_name("abc"), "")


class TestBangumiMappingHelpers(unittest.TestCase):
    """Bangumi 字段 -> 通用值 的映射辅助函数。"""

    def test_subject_title(self) -> None:
        self.assertEqual(subject_title(SUBJECT_DETAIL_DATA), "刀剑神域")
        self.assertEqual(
            subject_title({"name": "OnlyName", "name_cn": ""}), "OnlyName"
        )
        self.assertEqual(subject_title({}), "")

    def test_subject_cover(self) -> None:
        cover = subject_cover(SUBJECT_DETAIL_DATA)
        self.assertTrue(cover.startswith("http"))
        self.assertEqual(subject_cover({}), "")
        self.assertEqual(
            subject_cover({"image": "http://x/y.jpg"}), "http://x/y.jpg"
        )

    def test_infobox_and_areas_and_staff(self) -> None:
        rows = subject_infobox_rows(SUBJECT_DETAIL_DATA)
        self.assertIn(("地区", "日本"), rows)
        self.assertEqual(subject_areas(SUBJECT_DETAIL_DATA), ["日本"])
        staff = subject_staff(SUBJECT_DETAIL_DATA)
        self.assertIn(("原作", "川原砾"), staff)
        self.assertIn(("导演", "伊藤智彦"), staff)

    def test_eps_count(self) -> None:
        self.assertEqual(subject_eps_count(SUBJECT_DETAIL_DATA), 25)
        self.assertEqual(subject_eps_count({"eps": [1, 2, 3]}), 3)
        self.assertEqual(subject_eps_count({}), None)


class TestSearchItem(unittest.TestCase):
    """SearchItem 解析测试（Bangumi Subject）。"""

    def setUp(self) -> None:
        self.raw = SEARCH_RESULT_DATA["data"][0]

    def test_basic_fields(self) -> None:
        item = SearchItem.from_dict(self.raw)
        self.assertEqual(item.season_id, 33346)
        self.assertIsNone(item.media_id)  # Bangumi 无 media_id
        self.assertEqual(item.title, "刀剑神域")  # name_cn 优先
        self.assertEqual(item.category, "动画")  # type=2
        self.assertIsNone(item.status)  # Bangumi 无状态
        self.assertEqual(item.score, 8.8)  # rating.score
        self.assertEqual(item.score_count, 12345)  # rating.total
        self.assertEqual(item.areas, ["日本"])
        self.assertEqual(item.styles, ["战斗", "奇幻"])  # tags
        self.assertEqual(item.pub_time, "2012-07-08")
        self.assertIn("共 25 话", item.update_desc)  # eps -> 总集数
        self.assertIn("2022年", item.evaluate)  # summary
        self.assertTrue(item.cover.startswith("http"))

    def test_missing_optional_fields(self) -> None:
        item = SearchItem.from_dict(SEARCH_RESULT_DATA["data"][2])
        self.assertIsNone(item.score)
        self.assertIsNone(item.score_count)
        self.assertEqual(item.title, "你的名字。")
        self.assertEqual(item.category, "三次元")  # type=6
        self.assertEqual(item.cover, "")
        self.assertEqual(item.update_desc, "")

    def test_title_prefers_name_cn(self) -> None:
        item = SearchItem.from_dict(SEARCH_RESULT_DATA["data"][1])
        self.assertEqual(item.title, "鬼灭之刃")
        self.assertEqual(item.areas, [])


class TestEpisodeAndMisc(unittest.TestCase):
    """分集 / 榜单 / 时间线条目解析测试（Bangumi）。"""

    def test_episode_info(self) -> None:
        ep = EpisodeInfo.from_dict(
            {"id": 33171, "name_cn": "剑的世界", "ep": 1, "airdate": "2012-07-08"}
        )
        self.assertEqual(ep.ep_id, 33171)
        self.assertEqual(ep.display_title, "剑的世界")
        self.assertEqual(ep.pub_time, "2012-07-08")
        # 无标题时退化为 “第 N 话”
        ep2 = EpisodeInfo.from_dict({"id": 2, "ep": 5, "airdate": ""})
        self.assertEqual(ep2.display_title, "第 5 话")

    def test_rank_item_heat_candidates(self) -> None:
        item = RankItem.from_dict(RANK_LIST_DATA[0], rank=1)
        self.assertEqual(item.season_id, 1001)
        self.assertEqual(item.score, 9.8)
        self.assertEqual(item.heat_value, 9876543.0)
        self.assertEqual(item.heat_kind, "收藏")
        # 无 collects 时退化为 rating.total（评分人数）
        item2 = RankItem.from_dict(
            {"id": 9, "name_cn": "B", "rating": {"total": 50, "score": 9.0}},
            rank=2,
        )
        self.assertEqual(item2.heat_value, 50.0)
        self.assertEqual(item2.heat_kind, "评分人数")

    def test_timeline_item_mapping(self) -> None:
        item = TimelineItem.from_dict(
            {"id": 5001, "type": 2, "name_cn": "周一更新作品", "date": "2025-01-06"}
        )
        self.assertEqual(item.season_id, 5001)
        self.assertEqual(item.title, "周一更新作品")
        self.assertEqual(item.category, "动画")
        self.assertEqual(item.pub_time, "2025-01-06")


if __name__ == "__main__":
    unittest.main()
