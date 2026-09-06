"""GUI（PyQt5）模块测试（离线）。

- 纯文本格式化函数直接测试；
- 界面冒烟测试使用 ``QT_QPA_PLATFORM=offscreen``，不打开真实窗口，
  直接调用各标签页的回填方法验证表格 / 树控件被正确填充。
  所有测试均不发起网络请求（详情对象手动去掉封面 URL）。
"""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bangumi_query import main as gui  # noqa: E402
from bangumi_query.api import bangumi as api_module  # noqa: E402

from tests.fixtures import (  # noqa: E402
    CALENDAR_DATA,
    EPISODES_RESPONSE,
    RANK_LIST_DATA,
    SEARCH_RESULT_DATA,
    SUBJECT_DETAIL_DATA,
)

try:  # PyQt5 可能未安装 -> 相关用例自动跳过
    from PyQt5.QtCore import Qt  # noqa: E402
    from PyQt5.QtWidgets import QApplication  # noqa: E402

    _app = QApplication.instance() or QApplication([])
    PYQT_AVAILABLE = True
except Exception:  # noqa: BLE001
    PYQT_AVAILABLE = False


def _search_page():
    return api_module.parse_search_page(SEARCH_RESULT_DATA, page=1, page_size=10)


def _detail():
    detail = api_module.parse_season_view(
        SUBJECT_DETAIL_DATA, episodes_data=EPISODES_RESPONSE
    )
    detail.cover = ""  # 冒烟测试不触发封面下载线程
    return detail


class TestGuiFormatHelpers(unittest.TestCase):
    """GUI 格式化纯函数测试（不依赖 Qt 实例）。"""

    def test_format_score(self) -> None:
        self.assertEqual(gui.format_score(8.8), "8.8")
        self.assertEqual(gui.format_score(None), "—")

    def test_format_number(self) -> None:
        self.assertEqual(gui.format_number(12345), "1.2 万")
        self.assertEqual(gui.format_number(123456789), "1.23 亿")
        self.assertEqual(gui.format_number(999), "999")
        self.assertEqual(gui.format_number(None), "—")

    def test_join_names(self) -> None:
        self.assertEqual(gui.join_names(["日本", "中国"]), "日本、中国")
        self.assertEqual(gui.join_names([]), "—")

    def test_search_row_values(self) -> None:
        row = gui.search_row_values(_search_page().items[0])
        self.assertEqual(row[0], "刀剑神域")
        self.assertEqual(row[3], "—")  # Bangumi 无“连载/完结”状态
        self.assertIn("8.8", row)

    def test_detail_info_rows(self) -> None:
        rows = gui.detail_info_rows(_detail())
        keys = [k for k, _ in rows]
        self.assertIn("评分", keys)
        self.assertIn("地区", keys)
        self.assertIn("追番人数", keys)  # collection.* 求和
        self.assertNotIn("播放量", keys)  # Bangumi 无播放量
        rating = dict(rows)["评分"]
        self.assertIn("8.8 分", rating)
        self.assertIn("人评分", rating)

    def test_staff_lines_and_episode(self) -> None:
        detail = _detail()
        self.assertEqual(gui.staff_lines(detail.casts), "暂无")  # 无声优表
        staff_text = gui.staff_lines(detail.staff)
        self.assertIn("原作", staff_text)
        self.assertIn("川原砾", staff_text)
        ep = detail.episodes[0]
        self.assertEqual(gui.episode_row_values(ep, 1)[1], "剑的世界")

    def test_rank_row_values(self) -> None:
        items = api_module.parse_rank_list(RANK_LIST_DATA)
        row = gui.rank_row_values(items[0], "收藏")
        self.assertEqual(row[0], "1")
        self.assertEqual(row[1], "榜首作品")
        self.assertIn("987.7", row[4])  # 9876543 收藏


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt5 未安装，跳过界面冒烟测试")
class GuiSmokeTest(unittest.TestCase):
    """离屏创建主窗口并验证各标签页数据回填。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.window = gui.MainWindow()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()

    def test_search_result_fill(self) -> None:
        page = _search_page()
        for item in page.items:  # 冒烟测试不触发封面下载
            item.cover = ""
        self.window._on_search_result(page)
        table = self.window.search_table
        self.assertGreaterEqual(table.rowCount(), 3)
        # 封面 | 番剧名称 | ...
        self.assertEqual(table.item(0, 0).text(), "")
        self.assertEqual(table.item(0, 1).text(), "刀剑神域")
        self.assertEqual(self.window._search_items[1].season_id, 8)
        self.assertEqual(self.window.tabs.currentIndex(), 0)

    def test_detail_render(self) -> None:
        self.window._render_detail(_detail())
        self.assertIn("刀剑神域", self.window.detail_title.text())
        self.assertGreaterEqual(self.window.detail_info_table.rowCount(), 8)
        self.assertGreaterEqual(self.window.detail_episode_table.rowCount(), 1)
        self.assertEqual(
            self.window.detail_episode_table.item(0, 1).text(), "剑的世界"
        )
        self.assertIn("原作", self.window.detail_staff.toPlainText())
        self.assertEqual(self.window.cover_label.text(), "无封面")

    def test_rank_fill(self) -> None:
        items = api_module.parse_rank_list(RANK_LIST_DATA)
        self.window._on_rank_result(items)
        table = self.window.rank_table
        self.assertEqual(table.rowCount(), 3)
        # 封面 | 排名 | 番剧名称 | ...
        self.assertEqual(table.item(0, 0).text(), "")   # 封面列
        self.assertEqual(table.item(0, 1).text(), "1")  # 排名
        self.assertEqual(table.item(0, 2).text(), "榜首作品")
        # 数据到达不再强行切换页签（v3.1.0 修复页签自动跳转）
        self.assertEqual(table.horizontalHeaderItem(0).text(), "封面")
        self.assertEqual(table.horizontalHeaderItem(5).text(), "收藏")

    def test_timeline_fill(self) -> None:
        """追番日历：平铺所选星期条目（无折叠分组）+ 开播时间异步回填。"""
        days = api_module.parse_timeline(CALENDAR_DATA)
        with patch.object(
            api_module,
            "fetch_first_episode_date",
            new=AsyncMock(return_value="2012-07-08"),
        ):
            self.window._timeline_selected = 1  # 选“周一”再刷新
            self.window._on_timeline_result(days)
            tree = self.window.timeline_tree
            # 顶层即数据行：没有“周一/周二”可折叠分组
            self.assertEqual(tree.topLevelItemCount(), 1)
            row = tree.topLevelItem(0)
            self.assertEqual(row.data(1, Qt.UserRole), 5001)
            self.assertEqual(row.text(1), "周一更新作品")
            self.assertEqual(tree.headerItem().text(2), "开播时间")
            app = QApplication.instance()
            for _ in range(80):
                app.processEvents()
                if row.text(2) != "…":
                    break
                time.sleep(0.01)
            self.assertEqual(row.text(2), "2012-07-08")

    def test_rank_autoload_no_tab_jump(self) -> None:
        """排行榜结果到达（含首屏回调）不再强行切换页签（页签跳转修复）。"""
        raw_page = [
            {"id": 1, "type": 2, "name_cn": "甲", "rating": {"score": 8.0, "total": 5}},
            {"id": 2, "type": 2, "name_cn": "乙", "rating": {"score": 7.5, "total": 9}},
        ]
        window = self.window
        window._rank_request_label = "全部"
        window._rank_request_category = api_module.config.RANK_CATEGORIES[0]
        window._rank_request_sort = "热度"
        window._rank_pool_raw = list(raw_page)
        window._rank_pool_next_offset = len(raw_page)
        window._rank_pool_done = True   # 单页测试：无后台补全
        # 用户此刻在搜索页签（index 0），首屏回调不得把人拽去排行榜
        window.tabs.setCurrentIndex(0)
        window._on_rank_first_page(list(raw_page))
        self.assertEqual(len(window._rank_items), 2)
        self.assertEqual(window._rank_items[0].title, "乙")  # 热度降序
        self.assertEqual(window.tabs.currentIndex(), 0)      # 停留在原页签

    def test_cover_empty_path(self) -> None:
        # 无封面 URL：直接显示占位，不启动下载线程
        self.window._load_cover("")
        self.assertEqual(self.window.cover_label.text(), "无封面")
        self.assertEqual(self.window._workers, set())

    def test_clear_cache_button(self) -> None:
        """底部“清除缓存”：三选弹窗默认“仅清缓存”，保留“已看完”记录。"""
        import tempfile

        from bangumi_query.utils import cache as disk_cache
        from bangumi_query.utils import watched as watched_store

        old_dir = os.environ.get("BANGUMI_CACHE_DIR")
        tmp = tempfile.TemporaryDirectory()
        os.environ["BANGUMI_CACHE_DIR"] = os.path.join(tmp.name, "cache")
        try:
            disk_cache.store_cached_bytes("http://example.com/a.jpg", b"abc")
            watched_store.set_state(33346, "刀剑神域", "", "watched")
            self.assertEqual(disk_cache.cache_size(), (1, 3))
            with patch.object(self.window, "_ask_clear_cache",
                              return_value=False), \
                 patch.object(gui.QMessageBox, "information") as mock_info:
                self.window.on_clear_cache()
            mock_info.assert_called_once()
            self.assertEqual(disk_cache.cache_size(), (0, 0))
            self.assertEqual(watched_store.state_of(33346), "watched")  # 记录保留
        finally:
            if old_dir is None:
                os.environ.pop("BANGUMI_CACHE_DIR", None)
            else:
                os.environ["BANGUMI_CACHE_DIR"] = old_dir
            tmp.cleanup()

    def test_clear_cache_also_watched(self) -> None:
        """弹窗选“一并清除”时：缓存与“已看完”记录都被删除。"""
        import tempfile

        from bangumi_query.utils import cache as disk_cache
        from bangumi_query.utils import watched as watched_store

        old_dir = os.environ.get("BANGUMI_CACHE_DIR")
        tmp = tempfile.TemporaryDirectory()
        os.environ["BANGUMI_CACHE_DIR"] = os.path.join(tmp.name, "cache")
        try:
            disk_cache.store_cached_bytes("http://example.com/b.jpg", b"abcd")
            watched_store.set_state(8, "鬼灭之刃", "", "watched")
            with patch.object(self.window, "_ask_clear_cache",
                              return_value=True), \
                 patch.object(gui.QMessageBox, "information"):
                self.window.on_clear_cache()
            self.assertEqual(disk_cache.cache_size(), (0, 0))
            self.assertIsNone(watched_store.state_of(8))
        finally:
            if old_dir is None:
                os.environ.pop("BANGUMI_CACHE_DIR", None)
            else:
                os.environ["BANGUMI_CACHE_DIR"] = old_dir
            tmp.cleanup()

    def test_clear_cache_cancelled(self) -> None:
        """弹窗选“取消”（或关闭）：缓存与“已看完”记录都原样保留。"""
        import tempfile

        from bangumi_query.utils import cache as disk_cache
        from bangumi_query.utils import watched as watched_store

        old_dir = os.environ.get("BANGUMI_CACHE_DIR")
        tmp = tempfile.TemporaryDirectory()
        os.environ["BANGUMI_CACHE_DIR"] = os.path.join(tmp.name, "cache")
        try:
            disk_cache.store_cached_bytes("http://example.com/c.jpg", b"ab")
            watched_store.set_state(9, "取消场景", "", "watched")
            with patch.object(self.window, "_ask_clear_cache",
                              return_value=None), \
                 patch.object(gui.QMessageBox, "information") as mock_info:
                self.window.on_clear_cache()
            mock_info.assert_not_called()  # 取消：不弹结果框
            self.assertEqual(disk_cache.cache_size(), (1, 2))
            self.assertEqual(watched_store.state_of(9), "watched")
        finally:
            if old_dir is None:
                os.environ.pop("BANGUMI_CACHE_DIR", None)
            else:
                os.environ["BANGUMI_CACHE_DIR"] = old_dir
            tmp.cleanup()

    def test_watched_toggle_and_grid(self) -> None:
        """详情页双段选择器 → 双状态存储 → 两个番剧库渲染/迁移/取消。"""
        import tempfile

        from bangumi_query.utils import watched as watched_store

        old_dir = os.environ.get("BANGUMI_CACHE_DIR")
        tmp = tempfile.TemporaryDirectory()
        os.environ["BANGUMI_CACHE_DIR"] = os.path.join(tmp.name, "cache")
        try:
            # 详情渲染：双段选择器随本地记录初始化（未标记）
            self.window._render_detail(_detail())
            self.assertFalse(self.window._pill_watching_btn.isChecked())
            self.assertFalse(self.window._pill_watched_btn.isChecked())
            self.assertTrue(self.window._detail_pill.isEnabled())
            self.assertTrue(self.window._pill_watching_btn.isEnabled())
            self.assertTrue(self.window._pill_watched_btn.isEnabled())
            # 标记“正在看”：入库 + 详情选择器状态
            self.window._set_detail_state("watching")
            self.assertEqual(watched_store.state_of(33346), "watching")
            self.assertTrue(self.window._pill_watching_btn.isChecked())
            # 番剧库：正在看子页 1 张卡片
            self.window._render_library()
            self.assertEqual(self.window.watching_grid.count(), 1)
            self.assertEqual(self.window.watched_grid.count(), 0)
            item = self.window.watching_grid.item(0)
            self.assertEqual(item.data(Qt.UserRole), 33346)
            self.assertEqual(item.text(), "刀剑神域")
            # 切到“已看完”：状态迁移
            self.window._set_detail_state("watched")
            self.assertEqual(watched_store.state_of(33346), "watched")
            self.window._render_library()
            self.assertEqual(self.window.watching_grid.count(), 0)
            self.assertEqual(self.window.watched_grid.count(), 1)
            # 取消标记 → 两个库都为空
            self.window._set_detail_state(None)
            self.assertIsNone(watched_store.state_of(33346))
            self.window._render_library()
            self.assertEqual(self.window.watching_grid.count(), 0)
            self.assertEqual(self.window.watched_grid.count(), 0)
        finally:
            if old_dir is None:
                os.environ.pop("BANGUMI_CACHE_DIR", None)
            else:
                os.environ["BANGUMI_CACHE_DIR"] = old_dir
            tmp.cleanup()

    def test_column_width_memory(self) -> None:
        """列宽记忆：拖动 → 关闭落盘 → 新窗口恢复上次状态。"""
        import tempfile

        old_dir = os.environ.get("BANGUMI_CACHE_DIR")
        tmp = tempfile.TemporaryDirectory()
        os.environ["BANGUMI_CACHE_DIR"] = os.path.join(tmp.name, "cache")
        try:
            w1 = gui.MainWindow()
            w1.search_table.horizontalHeader().resizeSection(1, 500)
            w1.timeline_tree.header().resizeSection(1, 400)
            w1.close()  # closeEvent → 列宽落盘
            w2 = gui.MainWindow()  # 新窗口应恢复记忆
            self.assertEqual(w2.search_table.columnWidth(1), 500)
            self.assertEqual(w2.timeline_tree.columnWidth(1), 400)
            # 记忆生效后，程序铺排不再覆盖用户列宽
            w2._apply_search_widths()
            self.assertEqual(w2.search_table.columnWidth(1), 500)
            w2.close()
        finally:
            if old_dir is None:
                os.environ.pop("BANGUMI_CACHE_DIR", None)
            else:
                os.environ["BANGUMI_CACHE_DIR"] = old_dir
            tmp.cleanup()

    def test_theme_switch_persists(self) -> None:
        """主题下拉切换即时持久化，新窗口恢复所选主题。"""
        import tempfile

        from bangumi_query.utils import settings as settings_store

        old_dir = os.environ.get("BANGUMI_CACHE_DIR")
        tmp = tempfile.TemporaryDirectory()
        os.environ["BANGUMI_CACHE_DIR"] = os.path.join(tmp.name, "cache")
        try:
            w1 = gui.MainWindow()
            self.assertEqual(w1._theme, "浅色")   # v3.2.0 起默认浅色
            w1.theme_combo.setCurrentText("深色")  # 触发即时保存
            self.assertEqual(settings_store.load()["theme"], "深色")
            w2 = gui.MainWindow()
            self.assertEqual(w2._theme, "深色")
            w1.close()
            w2.close()
        finally:
            if old_dir is None:
                os.environ.pop("BANGUMI_CACHE_DIR", None)
            else:
                os.environ["BANGUMI_CACHE_DIR"] = old_dir
            tmp.cleanup()

    def test_timeline_marquee_hover(self) -> None:
        """追番日历：悬停超宽名称行 → 跑马灯浮层显示并滚动；离开即隐藏。"""
        from PyQt5.QtCore import QEvent, QPoint
        from PyQt5.QtGui import QMouseEvent
        from bangumi_query.models.bangumi import TimelineDay, TimelineItem

        long_title = ("无职转生 ～到了异世界就拿出真本事～ 第三季 特别版"
                      "超长名称测试用例超长名称测试用例")
        day = TimelineDay(date="", weekday=1, weekday_cn="周一",
                          is_today=False, items=[
                              TimelineItem(season_id=1, title=long_title,
                                           cover="", pub_time="",
                                           ep_label="", category="动画")])
        self.window._timeline_days = [day]
        self.window._timeline_selected = 1
        self.window._render_timeline_day()
        self.window.show()
        self.window.resize(1100, 760)
        self.window.tabs.setCurrentIndex(3)  # 切到追番日历页签（否则视口尺寸为零）
        app = QApplication.instance()
        for _ in range(30):
            app.processEvents()
            time.sleep(0.005)
        # 收窄名称列，确保名称必然超宽
        self.window.timeline_tree.header().resizeSection(1, 120)
        row = self.window.timeline_tree.topLevelItem(0)
        rect = self.window.timeline_tree.visualItemRect(row)
        center = (rect.center() if rect.width() > 0 and rect.height() > 0
                  else QPoint(200, 80))
        ev = QMouseEvent(QEvent.MouseMove, center, Qt.NoButton,
                         Qt.NoButton, Qt.NoModifier)
        QApplication.sendEvent(self.window.timeline_tree.viewport(), ev)
        self.assertTrue(self.window._marquee_box.isVisible())
        self.assertTrue(self.window._marquee_timer.isActive())
        offset1 = self.window._marquee_offset
        for _ in range(10):
            app.processEvents()
            time.sleep(0.02)
        self.assertGreater(self.window._marquee_offset, offset1)  # 在滚动
        # 鼠标离开视口：浮层隐藏、滚动停止
        QApplication.sendEvent(self.window.timeline_tree.viewport(),
                               QEvent(QEvent.Leave))
        self.assertFalse(self.window._marquee_box.isVisible())
        self.assertFalse(self.window._marquee_timer.isActive())
        self.window.hide()


if __name__ == "__main__":
    unittest.main()
