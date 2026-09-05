"""rich 展示层输出测试（离线，Console(record=True) 捕获）。"""

import io
import unittest
from unittest.mock import patch

from rich.console import Console

from bilibili_bangumi import config
from bilibili_bangumi.utils import display as view
from bilibili_bangumi.api.bangumi import (
    parse_rank_list,
    parse_search_page,
    parse_season_view,
    parse_timeline,
)

from tests.fixtures import (
    CALENDAR_DATA,
    EPISODES_RESPONSE,
    RANK_LIST_DATA,
    SEARCH_RESULT_DATA,
    SUBJECT_DETAIL_DATA,
)


class DisplayTestCase(unittest.TestCase):
    """提供可捕获输出的控制台。"""

    def setUp(self) -> None:
        self.console = Console(record=True, width=120, file=io.StringIO())
        self.patcher = patch.object(view, "CONSOLE", self.console)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()

    def render(self) -> str:
        return self.console.export_text(clear=False)


class TestSearchDisplay(DisplayTestCase):
    def test_search_page(self) -> None:
        page = parse_search_page(SEARCH_RESULT_DATA, page=1, page_size=10)
        view.print_search_page(page.items, page=1, total_pages=6, keyword="刀剑")
        text = self.render()
        self.assertIn("搜索结果", text)
        self.assertIn("刀剑神域", text)
        self.assertIn("动画", text)      # 类型：Bangumi type=2
        self.assertIn("8.8", text)       # rating.score
        self.assertIn("共 25 话", text)  # eps -> 总集数
        self.assertIn("2012-07-08", text)

    def test_search_page_empty(self) -> None:
        view.print_search_page([], page=1, total_pages=1, keyword="不存在")
        self.assertIn("没有找到相关番剧", self.render())


class TestDetailDisplay(DisplayTestCase):
    def test_subject_detail(self) -> None:
        detail = parse_season_view(
            SUBJECT_DETAIL_DATA, episodes_data=EPISODES_RESPONSE
        )
        view.print_season_detail(detail)
        text = self.render()
        for expected in (
            "番剧详情",
            "刀剑神域",
            "ソードアート・オンライン",  # 原名
            "剧情简介",
            "2022年",                     # summary
            "主要声优",
            "未获取到声优信息",
            "制作团队",
            "原作",                       # infobox -> staff
            "川原砾",
            "分集列表",
            "剑的世界",                   # episodes name_cn
            "8.8 分",
        ):
            self.assertIn(expected, text, f"缺少输出片段: {expected}")

    def test_detail_without_episodes(self) -> None:
        detail = parse_season_view({"id": 1, "name_cn": "无分集作品"})
        view.print_season_detail(detail)
        text = self.render()
        self.assertIn("暂无分集信息", text)
        self.assertIn("暂无简介", text)


class TestRankDisplay(DisplayTestCase):
    def test_ranking(self) -> None:
        items = parse_rank_list(RANK_LIST_DATA)
        view.print_ranking(items, label="日漫")
        text = self.render()
        self.assertIn("日漫", text)
        self.assertIn("榜首作品", text)
        self.assertIn("9.8", text)
        self.assertIn("987.7 万", text)   # 9876543 收藏 -> 987.7 万


class TestTimelineDisplay(DisplayTestCase):
    def test_timeline(self) -> None:
        days = parse_timeline(CALENDAR_DATA)
        view.print_timeline(days)
        text = self.render()
        self.assertIn("周一", text)
        self.assertIn("周日", text)       # Bangumi weekday id=0 -> 周日
        self.assertIn("周一更新作品", text)


class TestBasics(DisplayTestCase):
    def test_banner_and_menu(self) -> None:
        view.show_banner()
        view.show_menu()
        text = self.render()
        # 横幅中的应用名应来自 config.APP_NAME（单一来源）
        self.assertIn(config.APP_NAME, text)
        self.assertIn(config.VERSION, text)
        for label in ("搜索番剧", "查看番剧详情", "排行榜", "本周更新日历", "退出"):
            self.assertIn(label, text)

    def test_messages(self) -> None:
        view.print_info("提示消息")
        view.print_warning("警告消息")
        view.print_error("错误消息")
        view.print_ok("成功消息")
        text = self.render()
        for expected in ("提示消息", "警告消息", "错误消息", "成功消息"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
