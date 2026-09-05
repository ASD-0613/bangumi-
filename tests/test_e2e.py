"""交互流程端到端测试（离线）：模拟按键序列 + mock API 返回。

说明：自 GUI 版本（main.py）上线后，命令行交互入口已备份为
``bilibili_bangumi.cli_main``，本测试针对命令行备份版进行验证。
"""

import io
import unittest
from unittest.mock import AsyncMock, patch

from rich.console import Console

from bilibili_bangumi import cli_main as main_module
from bilibili_bangumi.api import bangumi as api_module
from bilibili_bangumi.utils import display as view

from tests.fixtures import (
    RANK_LIST_DATA,
    SEARCH_RESULT_DATA,
    SEASON_VIEW_DATA,
    TIMELINE_DATA,
)


def run_app():
    """运行完整程序入口（含退出告别语），等价于 python run.py。"""
    main_module.main()


class BaseFlowTest(unittest.TestCase):
    """基础：可捕获输出的控制台。"""

    def setUp(self) -> None:
        self.console = Console(record=True, width=130, file=io.StringIO())
        self.patcher = patch.object(view, "CONSOLE", self.console)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()

    def render(self) -> str:
        return self.console.export_text(clear=False)

    def feed_inputs(self, values):
        """把后续 input() 调用替换为依次返回 values。"""
        return patch("builtins.input", side_effect=values)


class TestSearchFlowE2E(BaseFlowTest):
    def test_search_and_exit(self) -> None:
        page = api_module.parse_search_page(SEARCH_RESULT_DATA, page=1, page_size=10)
        # 交互序列：进入菜单选 1 -> 输入关键词 -> 结果页按 q 退出搜索
        #          -> 回到主菜单选 5 退出
        with self.feed_inputs(["1", "刀剑", "q", "5"]):
            with patch.object(
                api_module, "search_bangumi", new=AsyncMock(return_value=page)
            ):
                run_app()
        text = self.render()
        self.assertIn("主菜单", text)
        self.assertIn("刀剑神域", text)
        self.assertIn("搜索结果", text)
        self.assertIn("再见", text)

    def test_search_error_handled(self) -> None:
        """网络错误应被捕获并提示，而不是崩溃。"""
        with self.feed_inputs(["1", "刀剑", "q", "5"]):
            with patch.object(
                api_module,
                "search_bangumi",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ):
                run_app()
        self.assertIn("未知错误", self.render())


class TestDetailFlowE2E(BaseFlowTest):
    def test_detail_by_id(self) -> None:
        detail = api_module.parse_season_view(SEASON_VIEW_DATA)
        with self.feed_inputs(["2", "33346", "", "q", "5"]):
            with patch.object(
                api_module, "get_season_detail", new=AsyncMock(return_value=detail)
            ):
                run_app()
        text = self.render()
        self.assertIn("番剧详情", text)
        self.assertIn("刀剑神域", text)
        self.assertIn("分集列表", text)
        self.assertIn("再见", text)


class TestRankFlowE2E(BaseFlowTest):
    def test_rank_and_back(self) -> None:
        items = api_module.parse_rank_list(RANK_LIST_DATA)
        # 选 3 -> 排行榜类别 1(全部) -> 榜单页输入 q 放弃选详情
        #     -> 类别菜单再输入 0 返回主菜单 -> 5 退出
        with self.feed_inputs(["3", "1", "q", "0", "5"]):
            with patch.object(
                api_module, "fetch_ranking", new=AsyncMock(return_value=items)
            ):
                run_app()
        text = self.render()
        self.assertIn("排行榜", text)
        self.assertIn("榜首作品", text)
        self.assertIn("再见", text)


class TestTimelineFlowE2E(BaseFlowTest):
    def test_timeline(self) -> None:
        days = api_module.parse_timeline(TIMELINE_DATA)
        # 选 4 -> 类型 1(番剧) -> 展示后回车 -> 输入 0 返回主菜单 -> 5 退出
        with self.feed_inputs(["4", "1", "", "0", "5"]):
            with patch.object(
                api_module, "fetch_timeline", new=AsyncMock(return_value=days)
            ):
                run_app()
        text = self.render()
        self.assertIn("本周更新日历", text)
        self.assertIn("周一", text)
        self.assertIn("再见", text)


class TestInvalidInputs(BaseFlowTest):
    def test_invalid_menu_choice(self) -> None:
        page = api_module.parse_search_page(SEARCH_RESULT_DATA, page=1, page_size=10)
        # 第一次菜单输入无效 -> 提示 -> 再次输入 5 退出
        with self.feed_inputs(["x", "5"]):
            with patch.object(
                api_module, "search_bangumi", new=AsyncMock(return_value=page)
            ):
                run_app()
        self.assertIn("无效输入", self.render())
        self.assertIn("再见", self.render())


if __name__ == "__main__":
    unittest.main()
