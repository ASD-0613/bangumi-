"""程序入口 —— PyQt5 图形界面（GUI）。

主窗口结构：
    ┌────────────────────────────────────────────┐
    │  搜索栏: [输入框…………] [搜索]              │
    ├────────────────────────────────────────────┤
    │  QTabWidget                                │
    │   ├ 搜索结果(表格, 可分页)                 │
    │   ├ 番剧详情(封面图 + 基本信息 + 分集)     │
    │   ├ 热门排行榜(表格, 可切换类别)           │
    │   ├ 追番日历(按星期分组的树形列表)         │
    │   └ 已看完(封面网格, 详情页打钩后收录)     │
    └────────────────────────────────────────────┘

所有网络请求均在后台线程(QThread + asyncio.run)中执行，不阻塞界面。
业务/网络层完全复用 ``api/bangumi.py`` 与 ``models/bangumi.py``（未改动），
错误处理沿用 ``api.describe_error``，通过 QMessageBox 弹出提示。
封面图片由 ``requests`` 在独立后台线程下载。

启动方式：
    普通用户：直接运行 Release 页发布的单文件 exe（双击即用，无需 Python）；
    开发调试：python -m bangumi_query.main（需 PyQt5 + requests）；
    打包 exe：python build_exe.py（见项目根目录脚本）。
"""

from __future__ import annotations

import asyncio
import faulthandler
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional, Sequence, Set, Tuple

try:  # GUI 依赖检测：缺少时给出清晰的中文提示
    import requests  # noqa: F401 - 用于后台线程下载封面图
    from PyQt5.QtCore import QEvent, QPoint, QSize, QThread, Qt, QTimer, pyqtSignal
    from PyQt5.QtGui import (
        QBrush,
        QColor,
        QFont,
        QIcon,
        QImage,
        QLinearGradient,
        QPalette,
        QPainter,
        QPen,
        QPixmap,
        QPolygonF,
    )
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QButtonGroup,
        QComboBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QListView,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextBrowser,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - 依赖缺失提示
    raise SystemExit(
        "缺少 GUI 依赖。请先执行：\n"
        "    pip install PyQt5 requests\n"
    ) from exc

from . import config
from .api import bangumi as api
from .utils import cache as disk_cache
from .utils import settings as settings_store
from .utils import watched as watched_store
from .models.bangumi import (
    EpisodeInfo,
    RankItem,
    SearchItem,
    SearchPage,
    SeasonDetail,
    StaffItem,
    TimelineDay,
)

# 主题强调色：星期选中、打钩框选中共用，保证观感一致
ACCENT_GREEN = "#3fa34d"

# ---------------------------------------------------------------------------
# 常量与纯文本格式化工具（不依赖 Qt，便于单测）
# ---------------------------------------------------------------------------

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# 数据列表固定行高：行高不可拖拽调节，封面显示高度与此一致
_ROW_HEIGHT: int = 84
# 数据字号（比系统默认适当放大）
_DATA_FONT_SIZE: int = 12
# 封面列宽度（容纳“纵向高度=行高”的竖版海报并留出居中空间）
_COVER_COLUMN_WIDTH: int = _ROW_HEIGHT + 26

# 全局深色主题（Steam 风格）：只声明颜色/边框/内边距，
# 字体族与字号仍由 app.setFont 及各控件的 setFont 控制，
# 避免覆盖表格 12pt、标题 16pt 等专用字号
STYLE_SHEET_DARK: str = """
QWidget { background-color: #1b2733; color: #cfd8e3; }
QLabel { background: transparent; }
QMainWindow, QDialog { background-color: #141d26; }

QTabWidget::pane { border: 1px solid #32465c; border-radius: 4px;
                   background: #1b2733; top: -1px; }
QTabBar::tab { background: #141d26; color: #7f9ab0; padding: 7px 18px;
               border-top-left-radius: 4px; border-top-right-radius: 4px;
               margin-right: 3px; }
QTabBar::tab:selected { background: #2a475e; color: #ffffff; }
QTabBar::tab:hover:!selected { background: #1d2c3a; color: #d8e6f2; }

QTableWidget, QTreeWidget, QListWidget {
    background: #17222d; alternate-background-color: #1b2836;
    border: 1px solid #32465c; border-radius: 4px;
    gridline-color: #243443; selection-background-color: #2a475e;
    selection-color: #ffffff; }
QHeaderView::section { background: #22313d; color: #9fb8cc; border: none;
    border-right: 1px solid #32465c; border-bottom: 2px solid #32465c;
    padding: 6px; }
QTableCornerButton::section { background: #22313d; border: none; }

QLineEdit { background: #141d26; border: 1px solid #32465c;
            border-radius: 4px; padding: 6px 10px; color: #eaf2f8;
            selection-background-color: #2a475e; }
QLineEdit:focus { border: 1px solid #66c0f4; }

QPushButton { background: #2a475e; color: #d8e6f2;
              border: 1px solid #3a556e; border-radius: 4px;
              padding: 6px 14px; }
QPushButton:hover { background: #33566f; border-color: #66c0f4; }
QPushButton:pressed { background: #1f3748; }
QPushButton:disabled { background: #202c37; color: #5a7183;
                       border-color: #2a3b4a; }

QComboBox { background: #141d26; border: 1px solid #32465c;
            border-radius: 4px; padding: 4px 10px; color: #eaf2f8; }
QComboBox:hover { border: 1px solid #66c0f4; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #17222d; color: #cfd8e3;
    selection-background-color: #2a475e; selection-color: #ffffff; }

QTextBrowser { background: #141d26; border: 1px solid #32465c;
               border-radius: 4px; color: #cfd8e3; }

QScrollBar:vertical { background: #141d26; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #3a556e; border-radius: 5px;
                              min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #66c0f4; }
QScrollBar:horizontal { background: #141d26; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: #3a556e; border-radius: 5px;
                                min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #66c0f4; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QStatusBar { background: #141d26; color: #8fa8bd; }
QToolTip { background: #2a475e; color: #eaf2f8;
           border: 1px solid #66c0f4; padding: 4px; }

QListWidget::item { padding: 4px; border-radius: 4px; }
QListWidget::item:selected { background: #2a475e; color: #ffffff; }
QListWidget::item:hover:!selected { background: #223444; }

QPushButton:checked { background: #3fa34d; color: #ffffff;
                      border: 1px solid #2c8a3e; }
QPushButton:checked:hover { background: #4bb45c; }
QLabel[hint="true"] { color: #8fa8bd; }
QLabel[coverBox="true"] { border: 1px solid #32465c;
    background-color: #141d26; color: #8fa8bd; border-radius: 4px; }
QWidget[marquee="true"] { background: #17222d; border: 1px solid #32465c; }
"""

# 全局浅色主题：结构与深色一致，配色为浅色变体
STYLE_SHEET_LIGHT: str = """
QWidget { background-color: #f4f6f8; color: #2a3a48; }
QLabel { background: transparent; }
QMainWindow, QDialog { background-color: #eaeef2; }

QTabWidget::pane { border: 1px solid #c9d4dd; border-radius: 4px;
                   background: #f4f6f8; top: -1px; }
QTabBar::tab { background: #dde5ec; color: #55677a; padding: 7px 18px;
               border-top-left-radius: 4px; border-top-right-radius: 4px;
               margin-right: 3px; }
QTabBar::tab:selected { background: #ffffff; color: #14314a; }
QTabBar::tab:hover:!selected { background: #e8eef4; color: #2a3a48; }

QTableWidget, QTreeWidget, QListWidget {
    background: #ffffff; alternate-background-color: #f2f6f9;
    border: 1px solid #c9d4dd; border-radius: 4px;
    gridline-color: #dfe7ee; selection-background-color: #cfe4f7;
    selection-color: #14314a; }
QHeaderView::section { background: #e6ecf1; color: #44586b; border: none;
    border-right: 1px solid #d5dee6; border-bottom: 2px solid #c9d4dd;
    padding: 6px; }
QTableCornerButton::section { background: #e6ecf1; border: none; }

QLineEdit { background: #ffffff; border: 1px solid #c9d4dd;
            border-radius: 4px; padding: 6px 10px; color: #1f2d3a;
            selection-background-color: #cfe4f7; }
QLineEdit:focus { border: 1px solid #3d84c6; }

QPushButton { background: #e3eaf0; color: #2a3a48;
              border: 1px solid #c4d0da; border-radius: 4px;
              padding: 6px 14px; }
QPushButton:hover { background: #d7e2eb; border-color: #3d84c6; }
QPushButton:pressed { background: #c9d6e2; }
QPushButton:disabled { background: #eef1f4; color: #9aa7b2;
                       border-color: #dbe2e8; }

QComboBox { background: #ffffff; border: 1px solid #c9d4dd;
            border-radius: 4px; padding: 4px 10px; color: #1f2d3a; }
QComboBox:hover { border: 1px solid #3d84c6; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #ffffff; color: #2a3a48;
    selection-background-color: #cfe4f7; selection-color: #14314a; }

QTextBrowser { background: #ffffff; border: 1px solid #c9d4dd;
               border-radius: 4px; color: #2a3a48; }

QScrollBar:vertical { background: #eef1f4; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #b9c6d1; border-radius: 5px;
                              min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #8fa8bd; }
QScrollBar:horizontal { background: #eef1f4; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: #b9c6d1; border-radius: 5px;
                                min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #8fa8bd; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QStatusBar { background: #eaeef2; color: #66727d; }
QToolTip { background: #ffffff; color: #1f2d3a;
           border: 1px solid #3d84c6; padding: 4px; }

QListWidget::item { padding: 4px; border-radius: 4px; }
QListWidget::item:selected { background: #cfe4f7; color: #14314a; }
QListWidget::item:hover:!selected { background: #e8f0f7; }

QPushButton:checked { background: #3fa34d; color: #ffffff;
                      border: 1px solid #2c7a3a; }
QPushButton:checked:hover { background: #4bb45c; }
QLabel[hint="true"] { color: #66727d; }
QLabel[coverBox="true"] { border: 1px solid #c9d4dd;
    background-color: #ffffff; color: #8a97a3; border-radius: 4px; }
QWidget[marquee="true"] { background: #ffffff; border: 1px solid #c9d4dd; }
"""

# 可选主题（键即界面下拉框里的显示名，存入 settings.json 的 "theme" 键）
THEMES: Dict[str, str] = {"深色": STYLE_SHEET_DARK, "浅色": STYLE_SHEET_LIGHT}


def _colorize_score_cells(table: Any, column: int,
                          gold: str, green: str) -> None:
    """给“评分”列上色：≥9 金色、≥8 绿色（颜色随主题提供）。"""
    for row in range(table.rowCount()):
        item = table.item(row, column)
        if item is None:
            continue
        try:
            score = float(item.text())
        except ValueError:
            continue
        if score >= 9.0:
            item.setForeground(QColor(gold))
        elif score >= 8.0:
            item.setForeground(QColor(green))


def format_score(score: Optional[float]) -> str:
    """评分文本：9.8 / 8.7，缺失返回 “—”。"""
    if score is None:
        return "—"
    return f"{score:.1f}"


def format_number(value: Optional[float]) -> str:
    """大数字人性化：1.2 万 / 1.23 亿 / 1234。"""
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(number) >= 100_000_000:
        return f"{number / 100_000_000:.2f} 亿"
    if abs(number) >= 10_000:
        return f"{number / 10_000:.1f} 万"
    return f"{number:g}"


def format_bytes(value: Optional[float]) -> str:
    """字节数人性化：512 B / 128.0 KB / 3.4 MB / 1.2 GB。"""
    if value is None or value <= 0:
        return "0 B"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _is_valid_image(data: bytes) -> bool:
    """用 QImage 校验字节能否解析为图片（线程安全，避免缓存坏文件）。"""
    image = QImage()
    return bool(image.loadFromData(data))


def join_names(names: Sequence[str], empty: str = "—") -> str:
    """把地区/风格等列表拼成字符串。"""
    return "、".join(names) if names else empty


def search_row_values(item: SearchItem) -> List[str]:
    """把搜索结果条目转换成一行的单元格文本。"""
    return [
        item.title or "—",
        item.category or "—",
        join_names(item.areas),
        item.status or "—",
        format_score(item.score),
        item.update_desc or "—",
        item.pub_time or "—",
    ]


def detail_info_rows(detail: SeasonDetail) -> List[Tuple[str, str]]:
    """把详情对象转换成 (字段名, 值) 列表用于表格展示。"""
    rows: List[Tuple[str, str]] = [
        ("类别", detail.category or "—"),
        ("状态", detail.status or "—"),
        ("地区", join_names(detail.areas)),
        ("风格", join_names(detail.styles)),
        ("开播时间", detail.pub_time or "—"),
    ]
    if detail.episode_total is not None:
        rows.append(("总集数", str(detail.episode_total)))
    if detail.newest_ep_desc:
        rows.append(("最新一话", detail.newest_ep_desc))
    score_desc = ""
    if detail.score is not None:
        score_desc = f"{detail.score:.1f} 分"
        if detail.score_count:
            score_desc += f"（{format_number(float(detail.score_count))} 人评分）"
    rows.append(("评分", score_desc or "—"))
    if detail.views is not None:
        rows.append(("播放量", format_number(float(detail.views))))
    if detail.follows is not None:
        rows.append(("追番人数", format_number(float(detail.follows))))
    if detail.share_url:
        rows.append(("链接", detail.share_url))
    return rows


def staff_lines(staff: Sequence[StaffItem], default: str = "暂无") -> str:
    """声优 / 制作团队 -> 多行文本（“角色 → 姓名”）。"""
    if not staff:
        return default
    return "\n".join(f"{item.role or '—'}　→　{item.name or '—'}" for item in staff)


def episode_row_values(ep: EpisodeInfo, index: int) -> List[str]:
    """把分集对象转换成一行单元格文本。"""
    return [str(index), ep.display_title or "—", ep.pub_time or "—"]


def rank_row_values(item: RankItem, heat_label: str) -> List[str]:
    """把排行榜条目转换成一行单元格文本。"""
    return [
        str(item.rank),
        item.title or "—",
        item.category or "—",
        format_score(item.score),
        format_number(item.heat_value) if item.heat_value is not None else "—",
        item.pub_time or "—",
    ]


# ---------------------------------------------------------------------------
# 后台任务线程
# ---------------------------------------------------------------------------


class ApiWorker(QThread):
    """在后台线程中运行一个返回协程的任务，结果经信号回传主线程。

    每次 ``run()`` 内部使用独立的 ``asyncio.run``（即独立事件循环）。
    """

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self, job: Callable[[], Awaitable[Any]], parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self._job = job

    def run(self) -> None:  # noqa: D102 - 线程入口
        try:
            result: Any = asyncio.run(self._job())
        except Exception as exc:  # noqa: BLE001 - 统一交给界面层展示
            if config.DEBUG:
                traceback.print_exc()
            self.failed.emit(api.describe_error(exc))
        else:
            self.succeeded.emit(result)


class CoverWorker(QThread):
    """后台线程获取封面图字节：优先读本地磁盘缓存，未命中才联网下载。

    下载成功且可解析为图片时写入磁盘缓存（下次不再消耗带宽）；
    失败时回传 None（不弹错误框，界面显示占位）。
    """

    image_ready = pyqtSignal(str, object)  # (url, bytes | None)

    def __init__(self, url: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._url = url

    def run(self) -> None:  # noqa: D102 - 线程入口
        cached = disk_cache.load_cached_bytes(self._url)
        if cached:
            self.image_ready.emit(self._url, cached)
            return
        try:
            resp = requests.get(
                self._url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Referer": "https://bgm.tv/",
                },
                timeout=10,
            )
            data: Optional[bytes] = resp.content if resp.status_code == 200 else None
        except Exception:  # noqa: BLE001 - 封面失败仅显示占位
            data = None
        # 只缓存可解析的图片字节，避免坏文件长期占据缓存
        if data and _is_valid_image(data):
            disk_cache.store_cached_bytes(self._url, data)
        self.image_ready.emit(self._url, data)


class WatchedCheckBox(QPushButton):
    """详情页“已看完”打钩框：粗黑边圆角正方形方框。

    - 未选中：白底 + 浅灰色钩；
    - 选中：粗黑边 + 绿色底，钩变纯黑并稍微放大。
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(44, 44)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("标记为已看完")

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名约定
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)

        # 圆角正方形框：粗黑边；选中时绿底（ACCENT_GREEN，与星期选中一致，
        # 叠加轻微纵向渐变提升质感）
        painter.setPen(QPen(QColor("#000000"), 3))
        if self.isChecked():
            base = QColor(ACCENT_GREEN)
            grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
            grad.setColorAt(0.0, base.lighter(115))
            grad.setColorAt(1.0, base.darker(115))
            painter.setBrush(QBrush(grad))
        else:
            painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(rect, 9, 9)

        # 钩形折线：选中时纯黑且放大 1.18 倍（围绕中心缩放）
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        scale = 1.18 if self.isChecked() else 1.0
        raw = [(0.26 * w, 0.53 * h), (0.43 * w, 0.69 * h), (0.75 * w, 0.33 * h)]
        pts = [QPointF(cx + (x - cx) * scale, cy + (y - cy) * scale)
               for x, y in raw]
        check_pen = QPen(
            QColor("#000000" if self.isChecked() else "#c9c9c9"),
            4.5 if self.isChecked() else 3.5,
        )
        check_pen.setCapStyle(Qt.RoundCap)
        check_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(check_pen)
        painter.drawPolyline(QPolygonF(pts))


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    """主窗口（应用名见 config.APP_NAME）。"""

    SEARCH_HEADERS = ["封面", "番剧名称", "类型", "地区", "状态", "评分", "更新情况", "更新时间"]
    # 指标列（热度/收藏数）按所选排序维度动态命名；选择“评分”时复用评分列
    RANK_HEADERS = ["排名", "番剧名称", "类型", "评分", "开播时间"]
    EPISODE_HEADERS = ["集数", "标题", "发布时间"]
    # 追番日历：封面 | 番剧名称 | 开播时间（首话日期）
    TIMELINE_HEADERS = ["封面", "番剧名称", "开播时间"]

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{config.APP_NAME} v{config.VERSION}")
        self.resize(1080, 760)
        self.setMinimumSize(880, 600)

        # ---- 状态 ----
        self._workers: Set[QThread] = set()
        self._search_items: List[SearchItem] = []
        self._search_covers: Dict[int, QPixmap] = {}
        self._search_icon_fetching: Set[int] = set()
        self._rank_items: List[RankItem] = []
        self._rank_page: int = 1
        self._rank_pages: int = 1
        self._rank_metric_kind: str = ""          # 当前排序维度对应指标列表头
        self._rank_covers: Dict[int, QPixmap] = {}
        self._rank_icon_fetching: Set[int] = set()
        # 追番日历：全部星期数据 + 当前选中星期 + 封面/首话日期缓存
        self._timeline_days: List[TimelineDay] = []
        self._timeline_selected: int = self._current_weekday()
        self._timeline_covers: Dict[int, QPixmap] = {}
        self._timeline_icon_fetching: Set[int] = set()
        self._first_date_cache: Dict[int, Optional[str]] = {}
        self._first_date_fetching: Set[int] = set()
        self._search_keyword: str = ""
        self._search_page: int = 1
        self._search_total_pages: int = 1
        self._search_busy: bool = False
        self._rank_busy: bool = False
        self._rank_request_label: str = ""        # 本次请求的榜单类别名（状态栏用）
        self._rank_request_signature: tuple = ()  # 发起请求时的 (类别, 维度)
        self._timeline_busy: bool = False
        self._timeline_request_signature: tuple = ()  # 发起请求时的日历类型
        self._detail_token: int = 0
        self._detail_pending_id: Optional[int] = None  # 正在加载的条目（防连击重复请求）
        self._cover_url: str = ""
        # “已看完”：网格封面缓存 + 抓取中集合 + 当前详情条目（打钩用）
        self._watched_icons: Dict[int, QPixmap] = {}
        self._watched_fetching: Set[int] = set()
        self._detail_current: Optional[Dict[str, Any]] = None
        # 列宽记忆：用户手动调过的视图不再被程序自动铺排；
        # _widths_programmatic 用于区分“程序铺排”与“用户拖动”
        self._saved_settings: Dict[str, Any] = settings_store.load()
        self._col_widths_user: Set[str] = set()
        self._widths_programmatic: bool = False
        # 当前主题（深色/浅色），与底部下拉框联动并持久化
        self._theme: str = str(self._saved_settings.get("theme", "浅色"))
        # 排行榜渐进式加载：首屏一个请求，后台逐页补全候选池
        self._rank_pool_raw: List[Dict[str, Any]] = []
        self._rank_pool_next_offset: int = 0
        self._rank_pool_token: int = 0      # 新刷新使后台补全链失效
        self._rank_pool_done: bool = True   # 候选池是否已取尽/中止
        self._rank_pool_page_size: int = 25
        self._rank_request_category: Dict[str, Any] = {}
        self._rank_request_sort: str = "热度"

        self._build_ui()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)

        # 顶部搜索栏（文字加粗放大，纵向加高）
        search_bar = QHBoxLayout()
        search_bar.addWidget(QLabel("搜索番剧："))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入番剧名称 / 关键词，如：刀剑神域")
        self.search_input.setClearButtonEnabled(True)
        search_font = self.search_input.font()
        search_font.setPointSize(11)
        search_font.setBold(True)
        self.search_input.setFont(search_font)
        self.search_input.setMinimumHeight(36)
        self.search_input.returnPressed.connect(self.on_search_clicked)
        search_bar.addWidget(self.search_input, stretch=1)
        self.search_button = QPushButton("搜索")
        self.search_button.setMinimumHeight(36)
        self.search_button.clicked.connect(self.on_search_clicked)
        search_bar.addWidget(self.search_button)
        root.addLayout(search_bar)

        # 多标签页（页签名加粗放大）
        self.tabs = QTabWidget(central)
        tab_bar = self.tabs.tabBar()
        tab_font = tab_bar.font()
        tab_font.setPointSize(11)
        tab_font.setBold(True)
        tab_bar.setFont(tab_font)
        root.addWidget(self.tabs, stretch=1)

        # 底部：主题切换 + 本地缓存占用提示 + 清除缓存按钮
        bottom_bar = QHBoxLayout()
        bottom_bar.addWidget(QLabel("主题："))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES.keys()))
        if self._theme not in THEMES:
            self._theme = "浅色"
        self.theme_combo.setCurrentText(self._theme)
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        bottom_bar.addWidget(self.theme_combo)
        bottom_bar.addStretch(1)
        self.cache_info_label = QLabel("")
        self.cache_info_label.setProperty("hint", True)
        bottom_bar.addWidget(self.cache_info_label)
        self.clear_cache_button = QPushButton("清除缓存")
        self.clear_cache_button.clicked.connect(self.on_clear_cache)
        bottom_bar.addWidget(self.clear_cache_button)
        root.addLayout(bottom_bar)
        self._refresh_cache_info()

        self._build_search_tab()
        self._build_detail_tab()
        self._build_rank_tab()
        self._build_timeline_tab()
        self._build_watched_tab()

        # 恢复上次记忆的列宽（存在记忆时优先于程序的自动铺排）
        if self._saved_settings.get("search_widths"):
            self._apply_saved_widths("search", self.search_table)
        if self._saved_settings.get("rank_widths"):
            self._apply_saved_widths("rank", self.rank_table)
        if self._saved_settings.get("timeline_widths"):
            self._apply_saved_widths("timeline", self.timeline_tree)

        # 首次切到“排行榜 / 追番日历”标签页时自动加载一次数据；
        # “已看完”为本地数据，每次切入都重新渲染
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.statusBar().showMessage("就绪")

    def _build_search_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.search_table = self._create_table(self.SEARCH_HEADERS)
        self.search_table.cellDoubleClicked.connect(self.on_search_row_open)
        # 键盘回车（激活行）同样打开详情
        self.search_table.cellActivated.connect(self.on_search_row_open)
        # 用户拖动列宽 → 记入“用户已调整”，此后不再被自动铺排覆盖
        self.search_table.horizontalHeader().sectionResized.connect(
            lambda i, o, n: self._on_section_resized("search", i, o, n)
        )
        layout.addWidget(self.search_table, stretch=1)

        pager = QHBoxLayout()
        self.page_label = QLabel("第 0 页 / 共 0 页")
        self.prev_button = QPushButton("上一页")
        self.prev_button.clicked.connect(lambda: self._goto_search_page(-1))
        self.next_button = QPushButton("下一页")
        self.next_button.clicked.connect(lambda: self._goto_search_page(1))
        # 尚未搜索过：分页按钮禁用，避免点了弹“没有更多页了”
        self.prev_button.setEnabled(False)
        self.next_button.setEnabled(False)
        open_btn = QPushButton("查看选中番剧详情")
        open_btn.clicked.connect(self.on_search_row_open_button)
        pager.addWidget(self.prev_button)
        pager.addWidget(self.page_label)
        pager.addStretch(1)
        pager.addWidget(open_btn)
        pager.addWidget(self.next_button)
        layout.addLayout(pager)
        self.tabs.addTab(page, "搜索结果")

    def _build_detail_tab(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(8, 8, 8, 8)

        # 标题行：番剧名（左）+ “已看完”打钩框（最右侧）
        title_row = QHBoxLayout()
        self.detail_title = QLabel("暂无详情（请先搜索或从榜单选择番剧）")
        self.detail_title.setWordWrap(True)
        font = self.detail_title.font()
        font.setPointSize(16)
        font.setBold(True)
        self.detail_title.setFont(font)
        title_row.addWidget(self.detail_title, stretch=1)
        self.watched_check = WatchedCheckBox()
        self.watched_check.setEnabled(False)
        self.watched_check.toggled.connect(self._on_watched_toggled)
        title_row.addWidget(self.watched_check, 0, Qt.AlignTop)
        outer.addLayout(title_row)

        self.detail_subtitle = QLabel("")
        self.detail_subtitle.setWordWrap(True)
        self.detail_subtitle.setProperty("hint", True)
        outer.addWidget(self.detail_subtitle)

        # 封面 + 基本信息
        head = QHBoxLayout()
        self.cover_label = QLabel("暂无封面")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setMinimumSize(150, 200)
        self.cover_label.setMaximumSize(190, 280)
        self.cover_label.setProperty("coverBox", True)
        head.addWidget(self.cover_label, alignment=Qt.AlignTop)

        self.detail_info_table = QTableWidget(0, 2)
        self.detail_info_table.setHorizontalHeaderLabels(["项目", "内容"])
        self.detail_info_table.verticalHeader().setVisible(False)
        self.detail_info_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.detail_info_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.detail_info_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.detail_info_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        head.addWidget(self.detail_info_table, stretch=1)
        outer.addLayout(head)

        # 简介
        outer.addWidget(self._section_label("剧情简介"))
        self.detail_intro = QTextBrowser()
        self.detail_intro.setFixedHeight(150)
        self.detail_intro.setPlainText("")
        outer.addWidget(self.detail_intro)

        # 声优 / 制作团队
        outer.addWidget(self._section_label("主要声优 / 制作团队"))
        self.detail_staff = QTextBrowser()
        self.detail_staff.setFixedHeight(200)
        self.detail_staff.setPlainText("")
        outer.addWidget(self.detail_staff)

        # 分集列表
        outer.addWidget(self._section_label("分集列表"))
        self.detail_episode_table = QTableWidget(0, 3)
        self.detail_episode_table.setHorizontalHeaderLabels(self.EPISODE_HEADERS)
        self.detail_episode_table.verticalHeader().setVisible(False)
        self.detail_episode_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.detail_episode_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.detail_episode_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        outer.addWidget(self.detail_episode_table)
        # 分集超出展示上限时的截断提示（CLI 版有，GUI 版此前缺失）
        self.detail_episode_hint = QLabel("")
        self.detail_episode_hint.setProperty("hint", True)
        self.detail_episode_hint.setWordWrap(True)
        outer.addWidget(self.detail_episode_hint)

        scroll.setWidget(content)
        self.tabs.addTab(scroll, "番剧详情")

    def _build_rank_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)

        # 第一重筛选：榜单类别；第二重筛选：排序维度
        bar = QHBoxLayout()
        bar.addWidget(QLabel("榜单类别："))
        self.rank_combo = QComboBox()
        for cat in config.RANK_CATEGORIES:
            self.rank_combo.addItem(str(cat.get("label", "")))
        # 与“排序维度”一致：切换类别立即刷新榜单
        self.rank_combo.currentIndexChanged.connect(
            lambda _i: self.on_rank_refresh()
        )
        bar.addWidget(self.rank_combo)
        bar.addWidget(QLabel("排序维度："))
        self.rank_sort_combo = QComboBox()
        for key in config.RANK_SORT_KEYS:
            self.rank_sort_combo.addItem(key)
        self.rank_sort_combo.currentIndexChanged.connect(
            lambda _i: self.on_rank_refresh()
        )
        bar.addWidget(self.rank_sort_combo)
        self.rank_refresh_button = QPushButton("刷新榜单")
        self.rank_refresh_button.clicked.connect(self.on_rank_refresh)
        bar.addWidget(self.rank_refresh_button)
        self.rank_detail_button = QPushButton("查看选中番剧详情")
        self.rank_detail_button.clicked.connect(self.on_rank_row_open_button)
        bar.addWidget(self.rank_detail_button)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.rank_table = self._create_table(self.RANK_HEADERS)
        self.rank_table.cellDoubleClicked.connect(self.on_rank_row_open)
        # 键盘回车（激活行）同样打开详情
        self.rank_table.cellActivated.connect(self.on_rank_row_open)
        # 用户拖动列宽 → 记入“用户已调整”，此后不再被自动铺排覆盖
        self.rank_table.horizontalHeader().sectionResized.connect(
            lambda i, o, n: self._on_section_resized("rank", i, o, n)
        )
        layout.addWidget(self.rank_table, stretch=1)

        # 排行榜分页（与搜索结果一致：每页 config.RANK_PAGE_SIZE 条）
        pager = QHBoxLayout()
        self.rank_prev_button = QPushButton("上一页")
        self.rank_prev_button.clicked.connect(lambda: self._goto_rank_page(-1))
        self.rank_page_label = QLabel("第 0 页 / 共 0 页")
        self.rank_next_button = QPushButton("下一页")
        self.rank_next_button.clicked.connect(lambda: self._goto_rank_page(1))
        # 尚未加载过榜单：分页按钮禁用
        self.rank_prev_button.setEnabled(False)
        self.rank_next_button.setEnabled(False)
        pager.addWidget(self.rank_prev_button)
        pager.addWidget(self.rank_page_label)
        pager.addStretch(1)
        pager.addWidget(self.rank_next_button)
        layout.addLayout(pager)
        self.tabs.addTab(page, "排行榜")

    def _build_timeline_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("日历类型："))
        self.timeline_combo = QComboBox()
        for item in config.TIMELINE_TYPES:
            self.timeline_combo.addItem(str(item.get("label", "")))
        # 切换日历类型立即刷新（与排行榜筛选行为一致）
        self.timeline_combo.currentIndexChanged.connect(
            lambda _i: self.on_timeline_refresh()
        )
        bar.addWidget(self.timeline_combo)
        self.timeline_refresh_button = QPushButton("刷新日历")
        self.timeline_refresh_button.clicked.connect(self.on_timeline_refresh)
        bar.addWidget(self.timeline_refresh_button)
        self.timeline_hint = QLabel("双击番剧查看详情；开播时间=首话播出日期，异步查询")
        bar.addWidget(self.timeline_hint)
        bar.addStretch(1)
        layout.addLayout(bar)

        # 星期选择行（默认选中今天，一次只查看一天的更新内容）
        day_row = QHBoxLayout()
        day_row.addWidget(QLabel("选择星期："))
        self._timeline_day_buttons: List[QPushButton] = []
        self._timeline_day_group = QButtonGroup(self)
        self._timeline_day_group.setExclusive(True)
        for idx, name in enumerate(config.WEEKDAY_NAMES, start=1):
            btn = QPushButton(name)
            btn.setCheckable(True)
            self._timeline_day_group.addButton(btn)
            btn.clicked.connect(
                lambda _checked=False, wd=idx: self._on_timeline_day_selected(wd)
            )
            self._timeline_day_buttons.append(btn)
            day_row.addWidget(btn)
        day_row.addStretch(1)
        layout.addLayout(day_row)

        self.timeline_tree = QTreeWidget()
        self.timeline_tree.setColumnCount(3)
        self.timeline_tree.setHeaderLabels(self.TIMELINE_HEADERS)
        self.timeline_tree.setAlternatingRowColors(True)
        header = self.timeline_tree.header()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        data_font = self.timeline_tree.font()
        data_font.setPointSize(_DATA_FONT_SIZE)
        self.timeline_tree.setFont(data_font)
        header_font = header.font()
        header_font.setPointSize(_DATA_FONT_SIZE)
        header.setFont(header_font)
        # 关键：设置图标显示区为行高大小，否则封面会按默认小图标绘制
        self.timeline_tree.setIconSize(QSize(_ROW_HEIGHT, _ROW_HEIGHT))
        self.timeline_tree.itemDoubleClicked.connect(self.on_timeline_item_open)
        # 键盘回车（激活行）同样打开详情
        self.timeline_tree.itemActivated.connect(self.on_timeline_item_open)
        # 用户拖动列宽 → 记入“用户已调整”，此后不再被自动铺排覆盖
        self.timeline_tree.header().sectionResized.connect(
            lambda i, o, n: self._on_section_resized("timeline", i, o, n)
        )
        # 番剧名称列：悬停且名称超宽时显示跑马灯浮层（滚动展示全名）。
        # 注意：不使用自定义绘制委托——真实环境下委托绘制路径曾触发
        # Qt 内部 qFatal 闪退；浮层是标准控件，无此风险。
        self._marquee_box = QWidget(self.timeline_tree.viewport())
        self._marquee_box.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._marquee_box.setProperty("marquee", True)
        self._marquee_label = QLabel(self._marquee_box)
        self._marquee_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._marquee_box.hide()
        self._marquee_title = ""
        self._marquee_offset = 0
        self._marquee_timer = QTimer(self)
        self._marquee_timer.setInterval(40)
        self._marquee_timer.timeout.connect(self._marquee_tick)
        # 悬停判定需要鼠标追踪：否则视图不派发移动事件，跑马灯无法触发
        self.timeline_tree.setMouseTracking(True)
        self.timeline_tree.viewport().setMouseTracking(True)
        self.timeline_tree.viewport().installEventFilter(self)
        # 滚动内容时行位置变化，直接隐藏浮层（下次悬停再显示）
        self.timeline_tree.verticalScrollBar().valueChanged.connect(
            lambda _v: self._hide_marquee())
        self.timeline_tree.horizontalScrollBar().valueChanged.connect(
            lambda _v: self._hide_marquee())
        layout.addWidget(self.timeline_tree, stretch=1)

        # 默认高亮“今天”，列宽在首次渲染与窗口缩放时自适应
        if 1 <= self._timeline_selected <= len(self._timeline_day_buttons):
            self._timeline_day_buttons[self._timeline_selected - 1].setChecked(True)
        self.tabs.addTab(page, "追番日历")

    def _build_watched_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)

        bar = QHBoxLayout()
        self.watched_hint = QLabel("在番剧详情页标题右侧的方框打钩，即可加入本页")
        self.watched_hint.setProperty("hint", True)
        bar.addWidget(self.watched_hint)
        bar.addStretch(1)
        layout.addLayout(bar)

        # Steam 仓库式网格：封面在上、简中名在下；单击选中，双击进详情
        # （条目名称用加粗字体，更醒目）
        self.watched_grid = QListWidget()
        watched_font = self.watched_grid.font()
        watched_font.setPointSize(10)
        watched_font.setBold(True)
        self.watched_grid.setFont(watched_font)
        self.watched_grid.setViewMode(QListView.IconMode)
        self.watched_grid.setResizeMode(QListView.Adjust)
        self.watched_grid.setMovement(QListView.Static)
        self.watched_grid.setSpacing(14)
        self.watched_grid.setIconSize(QSize(176, 248))
        self.watched_grid.setWordWrap(True)
        self.watched_grid.setUniformItemSizes(True)
        self.watched_grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.watched_grid.itemDoubleClicked.connect(self.on_watched_item_open)
        layout.addWidget(self.watched_grid, stretch=1)

        self.tabs.addTab(page, "已看完")

    # ------------------------------------------------------------------
    # 通用小工具
    # ------------------------------------------------------------------

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        font = label.font()
        font.setPointSize(11)
        font.setBold(True)
        label.setFont(font)
        label.setContentsMargins(0, 6, 0, 2)
        return label

    @staticmethod
    def _current_weekday() -> int:
        """今天对应星期：1=周一 … 7=周日。"""
        import time as _time

        return _time.localtime().tm_wday + 1

    @staticmethod
    def _create_table(headers: Sequence[str]) -> QTableWidget:
        """创建“可拖拽调列宽”的表格：大行高 + 放大字号 + Interactive 表头。"""
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(list(headers))
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        # Interactive：允许用户拖拽分隔线调整每列宽度（仅水平方向）
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        # 行高固定 = _ROW_HEIGHT（禁止拖拽行高，保证封面纵向与行高一致）
        vheader = table.verticalHeader()
        vheader.setSectionResizeMode(QHeaderView.Fixed)
        vheader.setDefaultSectionSize(_ROW_HEIGHT)
        data_font = table.font()
        data_font.setPointSize(_DATA_FONT_SIZE)
        table.setFont(data_font)
        header_font = header.font()
        header_font.setPointSize(_DATA_FONT_SIZE)
        header.setFont(header_font)
        # 关键：限制表格内图标的显示区，否则 84px 封面会被 Qt 按默认小图标
        # 尺寸缩小绘制（这是此前“图片依然很小”的原因）
        table.setIconSize(QSize(_ROW_HEIGHT, _ROW_HEIGHT))
        return table

    @staticmethod
    def _fill_table(table: QTableWidget, rows: Sequence[Sequence[str]]) -> None:
        """用字符串行填充表格（自动清空旧内容）。"""
        table.setRowCount(0)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(str(value)))

    def _register(self, worker: QThread) -> None:
        """登记并启动后台线程，负责其结束后的清理。

        调用方必须先连接好 succeeded / failed / image_ready 等信号，
        本方法随即调用 ``worker.start()`` 真正启动线程（此前遗漏 start
        会导致界面一直停留在“正在搜索/加载中”）。
        """
        self._workers.add(worker)

        def _on_finished() -> None:
            self._workers.discard(worker)
            worker.deleteLater()

        worker.finished.connect(_on_finished)
        worker.start()  # 关键修复：启动后台线程

    def _on_task_error(self, message: str) -> None:
        """所有 API 请求失败的统一处理：QMessageBox 弹出提示。"""
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "请求失败", message)

    def _busy(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def _on_tab_changed(self, index: int) -> None:
        """首次切到“排行榜 / 追番日历”标签页时自动加载一次数据；
        “已看完”为本地数据，每次切入都重新渲染。"""
        if index == 2 and not self._rank_busy and not self._rank_items:
            self.on_rank_refresh()
        elif index == 3 and not self._timeline_busy and not self._timeline_days:
            self.on_timeline_refresh()
        elif index == 4:
            self._render_watched()

    # ------------------------------------------------------------------
    # 本地磁盘缓存
    # ------------------------------------------------------------------

    def _refresh_cache_info(self) -> None:
        """刷新底部缓存占用提示，并在按钮 tooltip 中显示缓存目录。"""
        count, total = disk_cache.cache_size()
        if count:
            self.cache_info_label.setText(
                f"本地缓存：{count} 个文件 · {format_bytes(total)}"
            )
        else:
            self.cache_info_label.setText("本地缓存：空")
        self.clear_cache_button.setToolTip(
            f"清空本地缓存目录（封面图片等）：\n{disk_cache.cache_root()}"
        )

    def _ask_clear_cache(self) -> Optional[bool]:
        """清除前的确认弹窗。

        返回值：True=连带清除“已看完”记录；False=仅清缓存（默认）；
        None=用户取消（点击“取消”按钮、Esc 或关闭弹窗）——什么都不清。
        """
        box = QMessageBox(self)
        box.setWindowTitle("清除缓存")
        box.setText(
            "即将清空封面图片缓存。\n"
            "是否一并清除「已看完」的本地记录？"
        )
        clear_btn = box.addButton("一并清除", QMessageBox.YesRole)
        keep_btn = box.addButton("仅清缓存", QMessageBox.NoRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(keep_btn)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is clear_btn:
            return True
        if clicked is keep_btn:
            return False
        return None  # 取消按钮 / Esc / 关闭弹窗

    def on_clear_cache(self) -> None:
        """清除本地磁盘缓存；“已看完”记录默认保留；可随时取消。"""
        choice = self._ask_clear_cache()
        if choice is None:
            self.statusBar().showMessage("已取消清除缓存")
            return
        also_clear_watched = choice
        try:
            count, freed = disk_cache.clear_cache()
        except OSError as exc:
            QMessageBox.critical(self, "清除缓存", f"缓存清除失败：{exc}")
            return
        # 内存中的封面缩略图一并丢弃，下次显示时重新加载（并重建缓存）
        self._search_covers.clear()
        self._search_icon_fetching.clear()
        self._rank_covers.clear()
        self._rank_icon_fetching.clear()
        self._timeline_covers.clear()
        self._timeline_icon_fetching.clear()
        if also_clear_watched:
            watched_store.clear()
            self._watched_icons.clear()
        self._refresh_cache_info()
        if count:
            message = (
                f"缓存清除成功：共清理 {count} 个文件，"
                f"释放 {format_bytes(freed)}。"
            )
        else:
            message = "缓存目录为空，没有需要清除的内容。"
        message += (
            "\n「已看完」的本地记录已一并清除。"
            if also_clear_watched
            else "\n「已看完」的本地记录已保留。"
        )
        QMessageBox.information(self, "清除缓存", message)

    # ------------------------------------------------------------------
    # 功能 1：搜索
    # ------------------------------------------------------------------

    def on_search_clicked(self) -> None:
        """搜索栏点击 / 回车：从第 1 页开始搜索。"""
        keyword = self.search_input.text().strip()
        if not keyword:
            QMessageBox.information(self, "提示", "请输入搜索关键词")
            return
        if self._search_busy:
            self.statusBar().showMessage("正在搜索中，请稍候…")
            return
        self._search_keyword = keyword
        self._search_page = 1
        self._start_search()

    def _goto_search_page(self, delta: int) -> None:
        """上一页 / 下一页。"""
        if self._search_busy:
            self.statusBar().showMessage("正在搜索中，请稍候…")
            return
        target = self._search_page + delta
        if target < 1 or target > self._search_total_pages:
            QMessageBox.information(self, "提示", "没有更多页了")
            return
        if not self._search_keyword:
            return
        self._search_page = target
        self._start_search()

    def _update_search_pager_state(self) -> None:
        """按当前页码与忙状态同步上一页/下一页按钮可用性。"""
        self.prev_button.setEnabled(
            not self._search_busy and self._search_page > 1
        )
        self.next_button.setEnabled(
            not self._search_busy and self._search_page < self._search_total_pages
        )

    def _start_search(self) -> None:
        if self._search_busy:
            return
        self._search_busy = True
        self.search_button.setEnabled(False)
        self._update_search_pager_state()
        keyword = self._search_keyword
        page = self._search_page
        self._busy(f"正在搜索「{keyword}」第 {page} 页…")

        worker = ApiWorker(lambda: api.search_bangumi(keyword, page=page))
        worker.succeeded.connect(self._on_search_result)
        worker.failed.connect(self._on_search_error)
        self._register(worker)

    def _on_search_error(self, message: str) -> None:
        """搜索请求失败：先恢复交互状态，再弹出提示（避免界面卡死）。"""
        self._search_busy = False
        self.search_button.setEnabled(True)
        self._update_search_pager_state()
        self._on_task_error(message)

    def _on_search_result(self, result: Any) -> None:
        self._search_busy = False
        self.search_button.setEnabled(True)

        if not isinstance(result, SearchPage):
            self._update_search_pager_state()
            self._on_task_error("搜索返回数据格式异常")
            return
        self._search_total_pages = max(1, result.total_pages)
        self._update_search_pager_state()
        self._search_items = list(result.items)
        # 首列为“封面”，其后为原有信息列
        rows = [
            [""] + search_row_values(item) for item in self._search_items
        ]
        self._fill_table(self.search_table, rows)
        _colorize_score_cells(self.search_table, 5, *self._score_colors())
        for row_idx, _item in enumerate(self._search_items):
            cover_cell = self.search_table.item(row_idx, 0)
            if cover_cell is not None:
                cover_cell.setTextAlignment(Qt.AlignCenter)
        for row_idx, item in enumerate(self._search_items):
            pix = self._search_covers.get(item.season_id)
            if pix is not None:
                cell = self.search_table.item(row_idx, 0)
                if cell is not None:
                    cell.setIcon(QIcon(pix))
        self._apply_search_widths()
        self._ensure_search_covers(self._search_items)
        self.page_label.setText(
            f"第 {result.page} 页 / 共 {self._search_total_pages} 页"
            f"（共 {result.total_results} 条）"
        )
        self.tabs.setCurrentIndex(0)
        if result.empty:
            self.statusBar().showMessage("未找到相关番剧")
            QMessageBox.information(
                self, "搜索结果", "没有找到相关番剧，请更换关键词试试"
            )
        else:
            self.statusBar().showMessage(f"搜索完成，共 {len(result.items)} 条")

    def _apply_search_widths(self) -> None:
        """搜索表格：封面列按行高、番剧名称列加宽（用户调过列宽则不覆盖）。"""
        if "search" in self._col_widths_user:
            return
        header = self.search_table.horizontalHeader()
        self._widths_programmatic = True
        try:
            header.resizeSection(0, _COVER_COLUMN_WIDTH)
            header.resizeSection(1, 320)
        finally:
            self._widths_programmatic = False

    def _ensure_search_covers(self, items: Sequence[SearchItem]) -> None:
        """为当前页搜索结果异步加载封面（独立首列）。"""
        for item in items:
            sid = item.season_id
            if not sid or not item.cover:
                continue
            if sid in self._search_covers or sid in self._search_icon_fetching:
                continue
            self._search_icon_fetching.add(sid)
            worker = CoverWorker(item.cover)

            def on_image(_url: str, data: Any, subject_id: int = sid) -> None:
                self._search_icon_fetching.discard(subject_id)
                if not data:
                    return
                pixmap = QPixmap()
                if not pixmap.loadFromData(data):
                    return
                # 纵向高度 = 行高（等比缩放，保留横向比例）
                scaled = pixmap.scaledToHeight(
                    _ROW_HEIGHT, Qt.SmoothTransformation
                )
                self._search_covers[subject_id] = scaled
                for row_idx, it in enumerate(self._search_items):
                    if it.season_id == subject_id:
                        cell = self.search_table.item(row_idx, 0)
                        if cell is not None:
                            cell.setIcon(QIcon(scaled))
                        break

            worker.image_ready.connect(on_image)
            self._register(worker)

    # 查看详情：双击 / 按钮
    def on_search_row_open(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._search_items):
            self._open_detail(self._search_items[row].season_id)

    def on_search_row_open_button(self) -> None:
        row = self.search_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先在表格中选择一部番剧")
            return
        self.on_search_row_open(row, 0)

    # ------------------------------------------------------------------
    # 功能 2：番剧详情
    # ------------------------------------------------------------------

    def _open_detail(self, season_id: Optional[int]) -> None:
        """后台获取 season_id 对应详情并切换到详情标签页。

        加载期间再选其他条目会立即开始新请求（token 保证只渲染最新一次）；
        同一条目加载中的重复触发（双击/回车连击）会被忽略。
        """
        if not season_id:
            QMessageBox.information(self, "提示", "该条目缺少 season_id，无法查看详情")
            return
        if season_id == self._detail_pending_id:
            return  # 同一条目正在加载
        self._detail_token += 1
        token = self._detail_token
        self._detail_pending_id = season_id
        self._cover_url = ""

        self.detail_title.setText("正在加载详情…")
        self.detail_subtitle.setText(f"season_id = {season_id}")
        self.cover_label.setText("封面加载中…")
        self.cover_label.setPixmap(QPixmap())
        self.detail_info_table.setRowCount(0)
        self.detail_intro.setPlainText("")
        self.detail_staff.setPlainText("")
        self.detail_episode_table.setRowCount(0)
        self.detail_episode_hint.setText("")
        self._detail_current = None
        self.watched_check.blockSignals(True)
        self.watched_check.setChecked(False)
        self.watched_check.blockSignals(False)
        self.watched_check.setEnabled(False)
        self.tabs.setCurrentIndex(1)
        self._busy(f"正在获取番剧详情（season_id={season_id}）…")

        worker = ApiWorker(lambda sid=season_id: api.get_season_detail(sid))

        def on_ok(result: Any) -> None:
            if token != self._detail_token:  # 过期结果丢弃
                return
            self._detail_pending_id = None
            if isinstance(result, SeasonDetail):
                self._render_detail(result)
            else:
                self._on_task_error("详情返回数据格式异常")

        def on_err(message: str) -> None:
            if token == self._detail_token:
                self._detail_pending_id = None
                self._on_task_error(message)

        worker.succeeded.connect(on_ok)
        worker.failed.connect(on_err)
        self._register(worker)

    def _render_detail(self, detail: SeasonDetail) -> None:
        """在主线程用详情数据刷新界面。"""
        title = detail.title or "（未知标题）"
        if detail.original_title and detail.original_title != detail.title:
            self.detail_title.setText(f"{title}　{detail.original_title}")
            self.detail_subtitle.setText("")
        else:
            self.detail_title.setText(title)
            self.detail_subtitle.setText("")

        # 基本信息/打钩状态：标题下方渲染完成后同步“已看完”勾选状态
        self._detail_current = {
            "id": detail.season_id,
            "title": detail.title,
            "cover": detail.cover,
        }
        if detail.season_id:
            self.watched_check.blockSignals(True)
            self.watched_check.setChecked(
                watched_store.contains(detail.season_id)
            )
            self.watched_check.blockSignals(False)
            self.watched_check.setEnabled(True)
        else:
            self.watched_check.setEnabled(False)

        rows = detail_info_rows(detail)
        self.detail_info_table.setRowCount(0)
        self.detail_info_table.setRowCount(len(rows))
        for r, (k, v) in enumerate(rows):
            self.detail_info_table.setItem(r, 0, QTableWidgetItem(k))
            self.detail_info_table.setItem(r, 1, QTableWidgetItem(v))

        # 简介 / 声优制作 / 分集
        self.detail_intro.setPlainText(detail.evaluate or "暂无简介")
        cast_text = staff_lines(detail.casts, "未获取到声优信息")
        staff_text = staff_lines(detail.staff, "未获取到制作团队信息")
        self.detail_staff.setPlainText(
            "【主要声优】\n" + cast_text + "\n\n【制作团队】\n" + staff_text
        )

        episodes = detail.episodes
        shown = episodes[: config.EPISODE_DISPLAY_LIMIT]
        self.detail_episode_table.setRowCount(0)
        self.detail_episode_table.setRowCount(len(shown))
        for idx, ep in enumerate(shown, start=1):
            values = episode_row_values(ep, idx)
            for c, value in enumerate(values):
                self.detail_episode_table.setItem(idx - 1, c, QTableWidgetItem(value))
        # 表格高度：内容多时最高 600px（约可见 13~15 行），少时也保底 240px，
        # 整页仍可在滚动区里滚动
        row_h = self.detail_episode_table.verticalHeader().defaultSectionSize()
        content_height = len(shown) * (row_h + 4) + 30
        self.detail_episode_table.setMinimumHeight(min(content_height, 240))
        self.detail_episode_table.setMaximumHeight(min(content_height, 600))
        # 分集被截断时给出提示（与 CLI 版行为对齐）
        self.detail_episode_hint.setText(
            f"共 {len(episodes)} 集，仅展示前 {config.EPISODE_DISPLAY_LIMIT} 集"
            if len(episodes) > config.EPISODE_DISPLAY_LIMIT
            else ""
        )

        self.statusBar().showMessage(f"详情加载完成：{detail.title}")

        # 封面图后台下载
        self._load_cover(detail.cover)

    def _load_cover(self, url: str) -> None:
        self._cover_url = url or ""
        if not url:
            self.cover_label.setText("无封面")
            return
        self.cover_label.setText("封面加载中…")
        worker = CoverWorker(url)

        def on_image(image_url: str, data: Any) -> None:
            if not image_url or image_url != self._cover_url:
                return  # 过期封面丢弃
            if not data:
                self.cover_label.setText("封面加载失败")
                return
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                scaled = pixmap.scaled(
                    170, 230, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self.cover_label.setPixmap(scaled)
            else:
                self.cover_label.setText("封面加载失败")

        worker.image_ready.connect(on_image)
        self._register(worker)

    # ------------------------------------------------------------------
    # 功能 3：排行榜（类别 + 排序维度双重筛选，50 条本地分页，封面缩略图）
    # ------------------------------------------------------------------

    def _rank_sort_key(self) -> str:
        """当前排序维度。"""
        return str(self.rank_sort_combo.currentText())

    def on_rank_refresh(self) -> None:
        if self._rank_busy:
            # 请求进行中不重复发起；期间下拉的改动会在结果返回后
            # 按 (类别, 维度) 签名比对自动补发（见 _on_rank_result 尾部）
            return
        idx = self.rank_combo.currentIndex()
        if not (0 <= idx < len(config.RANK_CATEGORIES)):
            return
        category = config.RANK_CATEGORIES[idx]
        sort_key = self._rank_sort_key()
        self._rank_busy = True
        self._rank_request_label = str(category.get("label", ""))
        self._rank_request_category = category
        self._rank_request_sort = sort_key
        self._rank_request_signature = (idx, sort_key)
        # 渐进式加载状态复位：首屏只取一个服务器页
        self._rank_pool_token += 1
        self._rank_pool_raw = []
        self._rank_pool_next_offset = 0
        self._rank_pool_done = False
        self.rank_refresh_button.setEnabled(False)
        self.rank_prev_button.setEnabled(False)
        self.rank_next_button.setEnabled(False)
        self._busy(f"正在获取「{self._rank_request_label}」首屏榜单…")

        worker = ApiWorker(
            lambda cat=category: api.fetch_rank_pool_page(cat, 0,
                                                          self._rank_pool_page_size)
        )
        worker.succeeded.connect(self._on_rank_first_page)
        worker.failed.connect(self._on_rank_error)
        self._register(worker)

    def _on_rank_first_page(self, result: Any) -> None:
        """首屏：第一个服务器页（25 条）到达即渲染，后续页后台补全。"""
        self._rank_busy = False
        self.rank_refresh_button.setEnabled(True)
        if not isinstance(result, list):
            self._on_task_error("排行榜返回数据格式异常")
            return
        self._rank_pool_raw = list(result)
        self._rank_pool_next_offset = len(result)
        self._rank_pool_done = len(result) < self._rank_pool_page_size
        self._rebuild_rank_view()
        if not self._rank_pool_done:
            self._fetch_next_rank_pool_page()

    def _fetch_next_rank_pool_page(self) -> None:
        """后台逐页补全候选池；每页到达即重排序渲染，直到取尽或出错。"""
        token = self._rank_pool_token
        offset = self._rank_pool_next_offset
        category = self._rank_request_category
        worker = ApiWorker(
            lambda cat=category, off=offset:
                api.fetch_rank_pool_page(cat, off, self._rank_pool_page_size)
        )

        def on_done(result: Any) -> None:
            if token != self._rank_pool_token:
                return  # 已发起新的刷新，丢弃过期页
            if isinstance(result, list) and result:
                self._rank_pool_raw.extend(result)
                self._rank_pool_next_offset += len(result)
                if len(result) < self._rank_pool_page_size:
                    self._rank_pool_done = True
                self._rebuild_rank_view()
                if not self._rank_pool_done:
                    self._fetch_next_rank_pool_page()
            else:
                self._rank_pool_done = True  # 服务器已取尽
                self._rebuild_rank_view()

        def on_fail(_message: str) -> None:
            if token != self._rank_pool_token:
                return
            self._rank_pool_done = True
            self.statusBar().showMessage(
                "候选池后台补全中断（网络异常），当前榜单基于已获取数据"
            )

        worker.succeeded.connect(on_done)
        worker.failed.connect(on_fail)
        self._register(worker)

    def _rebuild_rank_view(self) -> None:
        """用当前候选池构建榜单并渲染（保留用户所在页码）。"""
        try:
            items = api.build_ranking_from_pool(
                self._rank_pool_raw,
                self._rank_request_category,
                self._rank_request_sort,
            )
        except api.BangumiQueryError as exc:
            self._rank_items = []
            self._rank_page = 1
            self._rank_pages = 1
            self._rank_metric_kind = ""
            self._render_rank_page()
            if self._rank_pool_done:
                self._on_task_error(str(exc))
            else:
                self.statusBar().showMessage(f"{exc}（后台继续获取中…）")
            return
        self._rank_items = items
        page_size = max(1, int(config.RANK_PAGE_SIZE))
        self._rank_pages = max(1, (len(items) + page_size - 1) // page_size)
        if self._rank_page > self._rank_pages:
            self._rank_page = self._rank_pages
        kind = next((i.heat_kind for i in items if i.heat_kind), "")
        self._rank_metric_kind = "" if kind == "评分" else kind
        self._render_rank_page()
        pool_note = (
            ""
            if self._rank_pool_done
            else f"（候选池 {len(self._rank_pool_raw)}/{config.RANK_POOL_SIZE}，"
                 "后台完善中）"
        )
        self.statusBar().showMessage(
            f"「{self._rank_request_label}」排行榜 {len(items)} 条{pool_note}"
        )

    def _on_rank_result(self, result: Any) -> None:
        """（测试/兼容路径）直接用一组 RankItem 渲染榜单，不发请求。"""
        self._rank_busy = False
        self.rank_refresh_button.setEnabled(True)
        if not isinstance(result, list):
            self._on_task_error("排行榜返回数据格式异常")
            return
        self._rank_items = list(result)
        self._rank_page = 1
        page_size = max(1, int(config.RANK_PAGE_SIZE))
        self._rank_pages = max(1, (len(self._rank_items) + page_size - 1) // page_size)
        # 指标列：热度/收藏数 单独成列；“评分”复用评分列不重复
        kind = next((i.heat_kind for i in self._rank_items if i.heat_kind), "")
        if kind == "评分":
            kind = ""
        self._rank_metric_kind = kind
        self._render_rank_page()
        self.statusBar().showMessage(
            f"「{self._rank_request_label or self.rank_combo.currentText()}」"
            f"排行榜共 {len(self._rank_items)} 条"
        )
        # 请求期间用户改过类别/维度：按最新选择自动补发一次
        if self._rank_request_signature != (
            self.rank_combo.currentIndex(), self._rank_sort_key()
        ):
            self.on_rank_refresh()

    def _on_rank_error(self, message: str) -> None:
        """榜单请求失败：先恢复交互状态再提示，避免标签页永久卡死。

        用户已切离排行榜页签时改用状态栏提示，不弹模态框打断。
        """
        self._rank_busy = False
        self.rank_refresh_button.setEnabled(True)
        self.rank_prev_button.setEnabled(self._rank_page > 1)
        self.rank_next_button.setEnabled(self._rank_page < self._rank_pages)
        if self.tabs.currentIndex() == 2:
            self._on_task_error(message)
        else:
            self.statusBar().showMessage(f"排行榜获取失败：{message}")
        # 请求期间用户改过筛选条件：按最新选择自动补发
        if self._rank_request_signature != (
            self.rank_combo.currentIndex(), self._rank_sort_key()
        ):
            self.on_rank_refresh()

    def _rank_page_items(self) -> List[RankItem]:
        """当前页对应的条目。"""
        page_size = max(1, int(config.RANK_PAGE_SIZE))
        start = (self._rank_page - 1) * page_size
        return self._rank_items[start: start + page_size]

    def _render_rank_page(self) -> None:
        """把当前页条目渲染到排行榜表格（封面独立首列，随行高自适应）。"""
        items = self._rank_page_items()
        kind = self._rank_metric_kind
        show_metric = bool(kind)
        headers = ["封面", "排名", "番剧名称", "类型", "评分"]
        if show_metric:
            headers.append(kind)
        headers.append("开播时间")
        count_changed = self.rank_table.columnCount() != len(headers)
        self.rank_table.setColumnCount(len(headers))
        self.rank_table.setHorizontalHeaderLabels(headers)

        self.rank_table.setRowCount(0)
        self.rank_table.setRowCount(len(items))
        for row_idx, item in enumerate(items):
            values = [
                "",
                str(item.rank),
                item.title or "—",
                item.category or "—",
                format_score(item.score),
            ]
            if show_metric:
                values.append(format_number(item.heat_value))
            values.append(item.pub_time or "—")
            for col_idx, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if col_idx == 0:  # 封面独立列
                    pix = self._rank_covers.get(item.season_id)
                    if pix is not None:
                        cell.setIcon(QIcon(pix))
                    cell.setTextAlignment(Qt.AlignCenter)
                self.rank_table.setItem(row_idx, col_idx, cell)
        _colorize_score_cells(self.rank_table, 4, *self._score_colors())

        # 列宽：用户已手动调过且列数未变 → 保留用户的列宽；
        # 列数变化（切换排序维度）→ 回到默认铺排，待关闭时重新记忆
        header = self.rank_table.horizontalHeader()
        if count_changed:
            self._col_widths_user.discard("rank")
        if "rank" not in self._col_widths_user:
            self._widths_programmatic = True
            try:
                header.resizeSection(0, _COVER_COLUMN_WIDTH)
                header.resizeSection(1, 48)
                header.resizeSection(2, 320)
            finally:
                self._widths_programmatic = False

        self.rank_page_label.setText(
            f"第 {self._rank_page} 页 / 共 {self._rank_pages} 页"
            f"（共 {len(self._rank_items)} 条）"
        )
        self.rank_prev_button.setEnabled(self._rank_page > 1)
        self.rank_next_button.setEnabled(self._rank_page < self._rank_pages)
        self._ensure_rank_covers(items)

    def _goto_rank_page(self, delta: int) -> None:
        """排行榜上一页 / 下一页。"""
        target = self._rank_page + delta
        if target < 1 or target > self._rank_pages:
            QMessageBox.information(self, "提示", "没有更多页了")
            return
        self._rank_page = target
        self._render_rank_page()

    def _ensure_rank_covers(self, items: Sequence[RankItem]) -> None:
        """为当前页条目异步加载封面缩略图（独立首列，尺寸贴合行高）。"""
        for item in items:
            sid = item.season_id
            if not sid or not item.cover:
                continue
            if sid in self._rank_covers or sid in self._rank_icon_fetching:
                continue
            self._rank_icon_fetching.add(sid)
            worker = CoverWorker(item.cover)

            def on_image(_url: str, data: Any, subject_id: int = sid) -> None:
                self._rank_icon_fetching.discard(subject_id)
                if not data:
                    return
                pixmap = QPixmap()
                if not pixmap.loadFromData(data):
                    return
                # 纵向高度 = 行高（等比缩放，保留横向比例）
                scaled = pixmap.scaledToHeight(
                    _ROW_HEIGHT, Qt.SmoothTransformation
                )
                self._rank_covers[subject_id] = scaled
                self._refresh_rank_icon_row(subject_id)

            worker.image_ready.connect(on_image)
            self._register(worker)

    def _refresh_rank_icon_row(self, subject_id: int) -> None:
        """封面下载完成后刷新当前页对应行的封面列。"""
        for row_idx, item in enumerate(self._rank_page_items()):
            if item.season_id == subject_id:
                cell = self.rank_table.item(row_idx, 0)
                if cell is not None:
                    cell.setIcon(QIcon(self._rank_covers[subject_id]))
                break

    def _rank_absolute_index(self, row: int) -> Optional[int]:
        """把表格行号映射为榜单完整列表下标（考虑当前分页偏移）。"""
        page_size = max(1, int(config.RANK_PAGE_SIZE))
        index = (self._rank_page - 1) * page_size + row
        if 0 <= index < len(self._rank_items):
            return index
        return None

    def on_rank_row_open(self, row: int, _col: int) -> None:
        index = self._rank_absolute_index(row)
        if index is not None:
            self._open_detail(self._rank_items[index].season_id)

    def on_rank_row_open_button(self) -> None:
        row = self.rank_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先在表格中选择一部番剧")
            return
        self.on_rank_row_open(row, 0)

    # ------------------------------------------------------------------
    # 功能 4：追番日历（选择星期查看单日更新 + 封面 + 更新集数）
    # ------------------------------------------------------------------

    def _selected_timeline_day(self) -> Optional[TimelineDay]:
        """返回当前选中星期对应的数据（未刷新/该日无数据时为 None）。"""
        for day in self._timeline_days:
            if day.weekday == self._timeline_selected:
                return day
        return None

    def _on_timeline_day_selected(self, weekday: int) -> None:
        """点击“周一~周日”按钮：只展示该天的更新内容。"""
        self._timeline_selected = weekday
        for idx, btn in enumerate(self._timeline_day_buttons, start=1):
            btn.setChecked(idx == weekday)
        self._render_timeline_day()

    def on_timeline_refresh(self) -> None:
        if self._timeline_busy:
            self.statusBar().showMessage("日历正在刷新中，请稍候…")
            return
        idx = self.timeline_combo.currentIndex()
        if not (0 <= idx < len(config.TIMELINE_TYPES)):
            return
        type_item = config.TIMELINE_TYPES[idx]
        type_value = int(type_item.get("value", config.SEASON_TYPE_ANIME))
        label = str(type_item.get("label", ""))
        self._timeline_busy = True
        self._timeline_request_signature = (idx,)
        self.timeline_refresh_button.setEnabled(False)
        self._busy(f"正在获取「{label}」放送日历…")

        worker = ApiWorker(lambda tv=type_value: api.fetch_timeline(tv))
        worker.succeeded.connect(self._on_timeline_result)
        worker.failed.connect(self._on_timeline_error)
        self._register(worker)

    def _on_timeline_error(self, message: str) -> None:
        """日历请求失败：先恢复交互状态再提示，避免标签页永久卡死。

        用户已切离追番日历页签时改用状态栏提示，不弹模态框打断。
        """
        self._timeline_busy = False
        self.timeline_refresh_button.setEnabled(True)
        if self.tabs.currentIndex() == 3:
            self._on_task_error(message)
        else:
            self.statusBar().showMessage(f"放送日历获取失败：{message}")
        # 请求期间用户改过日历类型：按最新选择自动补发
        if self._timeline_request_signature != (self.timeline_combo.currentIndex(),):
            self.on_timeline_refresh()

    def _on_timeline_result(self, result: Any) -> None:
        self._timeline_busy = False
        self.timeline_refresh_button.setEnabled(True)
        if not isinstance(result, list):
            self._on_task_error("日历返回数据格式异常")
            return
        self._timeline_days = [
            day for day in result if isinstance(day, TimelineDay)
        ]
        self._render_timeline_day()
        # 注意：数据到达时不强行切换页签——用户可能已在自动加载期间
        # 切去别的页签，强行 setCurrentIndex 会造成“页签自动跳转”
        total = sum(len(day.items) for day in self._timeline_days)
        self.statusBar().showMessage(f"放送日历共 {total} 部番剧")
        # 请求期间用户改过日历类型：按最新选择自动补发一次
        if self._timeline_request_signature != (self.timeline_combo.currentIndex(),):
            self.on_timeline_refresh()

    def _render_timeline_day(self) -> None:
        """渲染当前选中星期的条目：无折叠分组，直接平铺为数据行。"""
        self.timeline_tree.clear()
        self._hide_marquee()  # 列表重建，隐藏跑马灯浮层
        self._apply_timeline_widths()
        day = self._selected_timeline_day()
        if day is None:
            # 无数据 / 当日无更新：显示灰色占位行，而不是一片空白
            text = (
                "暂无数据——请点击上方「刷新日历」加载数据"
                if not self._timeline_days
                else f"{config.WEEKDAY_NAMES[self._timeline_selected - 1]}暂无更新条目"
            )
            placeholder = QTreeWidgetItem(["", text, ""])
            placeholder.setFlags(Qt.NoItemFlags)  # 占位行不可选中/不可交互
            self.timeline_tree.addTopLevelItem(placeholder)
            self.statusBar().showMessage(text)
            return

        # 每部番剧一行：封面 | 番剧名称 | 开播时间（首话日期，异步填充）
        for item in day.items:
            row = QTreeWidgetItem(["", item.title or "—", "…"])
            row.setSizeHint(0, QSize(0, _ROW_HEIGHT))
            row.setData(1, Qt.UserRole, item.season_id)
            row.setTextAlignment(0, Qt.AlignCenter)  # 封面图标居中
            if item.season_id in self._first_date_cache:
                cached = self._first_date_cache.get(item.season_id)
                row.setText(2, cached or "—")
            if item.season_id in self._timeline_covers:
                row.setIcon(0, QIcon(self._timeline_covers[item.season_id]))
            self.timeline_tree.addTopLevelItem(row)

        self.timeline_tree.expandAll()
        self.statusBar().showMessage(
            f"{day.weekday_cn} 更新 {len(day.items)} 部 · 开播时间/封面异步查询中"
        )
        self._ensure_timeline_covers(day.items)
        self._ensure_timeline_first_dates(day.items)

    def _apply_timeline_widths(self) -> None:
        """封面列固定宽；番剧名称列约占数据栏一半宽（用户调过列宽则不覆盖）。"""
        if "timeline" in self._col_widths_user:
            return
        view_width = self.timeline_tree.viewport().width()
        header = self.timeline_tree.header()
        name_width = max(240, int(view_width * 0.5))
        self._widths_programmatic = True
        try:
            header.resizeSection(0, _COVER_COLUMN_WIDTH)  # 封面
            header.resizeSection(1, name_width)            # 番剧名称 ≈ 一半
            header.resizeSection(2, 150)                   # 开播时间
        finally:
            self._widths_programmatic = False

    def _find_timeline_row(self, season_id: int) -> Optional[QTreeWidgetItem]:
        """按条目 ID 在当前平铺行中定位（用于异步回填）。"""
        for i in range(self.timeline_tree.topLevelItemCount()):
            row = self.timeline_tree.topLevelItem(i)
            if row.data(1, Qt.UserRole) == season_id:
                return row
        return None

    def _ensure_timeline_covers(self, items: Sequence[TimelineItem]) -> None:
        """为当前列表条目异步加载封面（首列）。"""
        for item in items:
            sid = item.season_id
            if not sid or not item.cover:
                continue
            if sid in self._timeline_covers or sid in self._timeline_icon_fetching:
                continue
            self._timeline_icon_fetching.add(sid)
            worker = CoverWorker(item.cover)

            def on_image(_url: str, data: Any, subject_id: int = sid) -> None:
                self._timeline_icon_fetching.discard(subject_id)
                if not data:
                    return
                pixmap = QPixmap()
                if pixmap.loadFromData(data):
                    # 纵向高度 = 行高（等比缩放，保留横向比例）
                    scaled = pixmap.scaledToHeight(
                        _ROW_HEIGHT, Qt.SmoothTransformation
                    )
                    self._timeline_covers[subject_id] = scaled
                    row = self._find_timeline_row(subject_id)
                    if row is not None:
                        row.setIcon(0, QIcon(scaled))

            worker.image_ready.connect(on_image)
            self._register(worker)

    def _ensure_timeline_first_dates(self, items: Sequence[TimelineItem]) -> None:
        """逐条查询“首话播出日期”，成功后回填“开播时间”列。"""
        for item in items:
            sid = item.season_id
            if not sid:
                continue
            if sid in self._first_date_cache or sid in self._first_date_fetching:
                continue
            self._first_date_fetching.add(sid)
            worker = ApiWorker(
                lambda subject_id=sid: api.fetch_first_episode_date(subject_id)
            )

            def on_done(value: Any, subject_id: int = sid) -> None:
                self._first_date_fetching.discard(subject_id)
                text = value if isinstance(value, str) and value else None
                self._first_date_cache[subject_id] = text
                row = self._find_timeline_row(subject_id)
                if row is not None:
                    row.setText(2, text or "—")

            def on_fail(_message: str, subject_id: int = sid) -> None:
                # 查询失败也要清理状态并把单元格落到 “—”，
                # 否则该条目永远停留在 “…”（且缓存集合只增不减）
                self._first_date_fetching.discard(subject_id)
                self._first_date_cache[subject_id] = None
                row = self._find_timeline_row(subject_id)
                if row is not None:
                    row.setText(2, "—")

            worker.succeeded.connect(on_done)
            worker.failed.connect(on_fail)
            self._register(worker)

    def on_timeline_item_open(self, item: QTreeWidgetItem, _col: int) -> None:
        if not (item.flags() & Qt.ItemIsEnabled):
            return  # 占位行，忽略双击/回车
        season_id = item.data(1, Qt.UserRole)
        if season_id is not None:
            self._open_detail(int(season_id))
        else:
            self.statusBar().showMessage("该条目缺少条目 ID，无法查看详情")

    # ------------------------------------------------------------------
    # 功能 5：已看完（详情页打钩 + Steam 仓库式网格）
    # ------------------------------------------------------------------

    def _on_watched_toggled(self, checked: bool) -> None:
        """详情页打钩：把当前番剧加入/移出“已看完”并立即持久化。"""
        info = self._detail_current
        if not info or not info.get("id"):
            return
        sid = int(info["id"])
        title = str(info.get("title") or "")
        if checked:
            watched_store.add(sid, title, str(info.get("cover") or ""))
            self.statusBar().showMessage(f"已把「{title}」标记为已看完")
        else:
            watched_store.remove(sid)
            self.statusBar().showMessage(f"已取消「{title}」的已看完标记")

    def _render_watched(self) -> None:
        """渲染“已看完”网格：封面 + 底部简中名（最新打钩在前）。"""
        self.watched_grid.clear()
        items = watched_store.load_items()
        if not items:
            self.watched_hint.setText(
                "暂无已看完的番剧——在番剧详情页标题右侧的方框打钩即可加入"
            )
            self.statusBar().showMessage("已看完列表为空")
            return
        self.watched_hint.setText(f"共 {len(items)} 部 · 单击选中，双击查看详情")
        self.statusBar().showMessage(f"已看完共 {len(items)} 部")
        for it in items:
            sid = it["id"]
            item = QListWidgetItem(it["title"] or f"番剧 {sid}")
            item.setData(Qt.UserRole, sid)
            item.setSizeHint(QSize(196, 316))
            item.setTextAlignment(Qt.AlignHCenter)
            if sid in self._watched_icons:
                item.setIcon(QIcon(self._watched_icons[sid]))
            else:
                item.setIcon(QIcon(self._placeholder_pixmap()))
            self.watched_grid.addItem(item)
        self._ensure_watched_covers(items)

    def _score_colors(self) -> Tuple[str, str]:
        """评分档位配色（金/绿），随主题切换。"""
        if self._theme == "浅色":
            return "#b8860b", "#3f8f3f"
        return "#ffd45e", "#8fd08f"

    def _on_theme_changed(self, theme: str) -> None:
        """底部主题下拉：即时切换并记忆（评分列同步重新着色）。"""
        self._theme = theme
        settings_store.update(theme=theme)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(THEMES.get(theme, STYLE_SHEET_DARK))
        gold, green = self._score_colors()
        _colorize_score_cells(self.search_table, 5, gold, green)
        _colorize_score_cells(self.rank_table, 4, gold, green)

    def _placeholder_pixmap(self) -> QPixmap:
        """封面未就绪时的占位图（配色随主题）。"""
        pm = QPixmap(176, 248)
        pm.fill(QColor("#dfe6ec") if self._theme == "浅色" else QColor("#243443"))
        return pm

    def _ensure_watched_covers(self, items: Sequence[Dict[str, Any]]) -> None:
        """为网格条目异步加载封面（优先命中磁盘缓存，与其它页共用）。"""
        for it in items:
            sid = it["id"]
            if (not it.get("cover") or sid in self._watched_icons
                    or sid in self._watched_fetching):
                continue
            self._watched_fetching.add(sid)
            worker = CoverWorker(it["cover"])

            def on_image(_url: str, data: Any, subject_id: int = sid) -> None:
                self._watched_fetching.discard(subject_id)
                if not data:
                    return
                pixmap = QPixmap()
                if not pixmap.loadFromData(data):
                    return
                scaled = pixmap.scaled(
                    176, 248, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self._watched_icons[subject_id] = scaled
                for row in range(self.watched_grid.count()):
                    item = self.watched_grid.item(row)
                    if item.data(Qt.UserRole) == subject_id:
                        item.setIcon(QIcon(scaled))
                        break

            worker.image_ready.connect(on_image)
            self._register(worker)

    def on_watched_item_open(self, item: QListWidgetItem) -> None:
        season_id = item.data(Qt.UserRole)
        if season_id is not None:
            self._open_detail(int(season_id))

    # ------------------------------------------------------------------
    # 列宽记忆（搜索/排行榜/追番日历）
    # ------------------------------------------------------------------

    def _on_section_resized(self, key: str, _index: int,
                            _old: int, _new: int) -> None:
        """列被拖动：记入“用户已调整”（程序铺排期间的事件忽略）。"""
        if self._widths_programmatic:
            return
        self._col_widths_user.add(key)

    def _apply_saved_widths(self, key: str, view: Any) -> None:
        """把上次记忆的列宽套用到视图，并标记为“用户已调整”。"""
        saved = self._saved_settings.get(key + "_widths")
        header = (view.horizontalHeader() if hasattr(view, "horizontalHeader")
                  else view.header())
        if isinstance(saved, list):
            self._widths_programmatic = True
            try:
                for i, width in enumerate(saved[: view.columnCount()]):
                    if isinstance(width, int) and width >= 20:
                        header.resizeSection(i, width)
            finally:
                self._widths_programmatic = False
        self._col_widths_user.add(key)

    def _persist_column_widths(self) -> None:
        """把三个数据视图的当前列宽写入 settings.json（尽力而为）。"""
        try:
            settings_store.update(
                search_widths=[
                    self.search_table.columnWidth(i)
                    for i in range(self.search_table.columnCount())
                ],
                rank_widths=[
                    self.rank_table.columnWidth(i)
                    for i in range(self.rank_table.columnCount())
                ],
                timeline_widths=[
                    self.timeline_tree.columnWidth(i)
                    for i in range(self.timeline_tree.columnCount())
                ],
            )
        except Exception:  # noqa: BLE001 - 偏好保存失败不影响退出
            pass

    def eventFilter(self, obj: Any, event: Any) -> bool:
        """追番日历视口：移动=更新悬停跑马灯浮层，离开=隐藏浮层。"""
        if hasattr(self, "timeline_tree") and obj is self.timeline_tree.viewport():
            if event.type() == QEvent.MouseMove:
                self._update_marquee(self.timeline_tree.itemAt(event.pos()))
            elif event.type() == QEvent.Leave:
                self._hide_marquee()
        return super().eventFilter(obj, event)

    def _update_marquee(self, item: Optional[QTreeWidgetItem]) -> None:
        """悬停行：名称超宽时在名称单元格上方显示滚动浮层。"""
        if item is None:
            self._hide_marquee()
            return
        row_rect = self.timeline_tree.visualItemRect(item)
        pos_ok = row_rect.height() > 0 and row_rect.width() > 0
        if not pos_ok:
            self._hide_marquee()
            return
        title = item.text(1) or ""
        header = self.timeline_tree.header()
        cell_w = header.sectionSize(1) - 10
        text_w = self.timeline_tree.fontMetrics().horizontalAdvance(title)
        if not title or text_w <= cell_w:
            self._hide_marquee()  # 名称能完整显示，无需跑马灯
            return
        x = header.sectionViewportPosition(1) + 5
        y = row_rect.top() + 2
        w = header.sectionSize(1) - 10
        h = row_rect.height() - 4
        self._marquee_box.setGeometry(x, y, w, h)
        if self._marquee_title != title:
            self._marquee_title = title
            self._marquee_offset = 0
            self._marquee_label.setText(f"{title}　　{title}")
            self._marquee_label.adjustSize()
            self._marquee_label.move(
                0, max(0, (h - self._marquee_label.height()) // 2))
        self._marquee_box.show()
        self._marquee_label.show()
        if not self._marquee_timer.isActive():
            self._marquee_timer.start()

    def _marquee_tick(self) -> None:
        """跑马灯心跳：文字整体左移，滚出后从右侧重新进入（无缝循环）。"""
        if not self._marquee_title:
            self._marquee_timer.stop()
            return
        label_w = self._marquee_label.width()
        loop = label_w + 24
        self._marquee_offset = (self._marquee_offset + 2) % loop
        self._marquee_label.move(-self._marquee_offset, self._marquee_label.y())

    def _hide_marquee(self) -> None:
        """隐藏跑马灯浮层并停止滚动。"""
        self._marquee_box.hide()
        self._marquee_timer.stop()

    def resizeEvent(self, event: Any) -> None:
        """窗口缩放时同步调整追番日历列宽（番剧名称列≈一半）。"""
        super().resizeEvent(event)
        if hasattr(self, "timeline_tree"):
            self._apply_timeline_widths()

    # ------------------------------------------------------------------
    # 关闭处理
    # ------------------------------------------------------------------

    def closeEvent(self, event: Any) -> None:
        """退出处理：先隐藏窗口（对用户“秒退”），再给后台线程约 2 秒收尾。

        若线程仍阻塞在网络请求上（网络差时一次请求可挂 10 秒以上），
        terminate() 对卡在 SSL/socket 原生代码里的线程有死锁风险——这正是
        “关闭时偶尔卡死”的原因。所有数据（打钩记录、缓存）均为即时落盘，
        因此此时直接 os._exit 立即结束进程，保证退出永远干脆利落。
        """
        self.hide()  # 立即从屏幕消失，收尾过程对用户不可见
        self._persist_column_widths()  # 列宽记忆落盘（业务数据均为即时保存）
        deadline = time.monotonic() + 2.0
        for worker in list(self._workers):
            remaining_ms = int(max(0.0, deadline - time.monotonic()) * 1000)
            if remaining_ms > 0 and worker.wait(remaining_ms):
                continue  # 线程正常收尾
            # 线程卡在网络请求上：立即结束进程（数据已落盘，无丢失风险）
            event.accept()
            os._exit(0)
        event.accept()


def _app_icon() -> Optional[QIcon]:
    """加载程序图标（icon.ico）。

    打包态：从 PyInstaller 自解压目录（sys._MEIPASS，build_exe.py 已把
    icon.ico 打进包内）取；开发态：从项目根目录取。找不到返回 None。
    """
    candidates: List[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "icon.ico")
        candidates.append(Path(sys.executable).parent / "icon.ico")
    else:
        candidates.append(Path(__file__).resolve().parent.parent / "icon.ico")
    for path in candidates:
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return None


# 崩溃日志文件句柄（保持引用，避免被关闭）
_CRASH_LOG_FILE: Any = None
# Qt 日志文件句柄（qFatal 的消息文本是定位闪退的关键）
_QT_LOG_FILE: Any = None


def _qt_message_handler(mode: Any, _context: Any, message: str) -> None:
    """捕获 Qt 的警告/致命消息写入 qt.log（qFatal 文本是闪退定位关键）。"""
    names = {0: "debug", 1: "warning", 2: "critical", 3: "FATAL", 4: "info"}
    try:
        if _QT_LOG_FILE is not None and mode >= 1:  # 跳过 debug 级
            from datetime import datetime
            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            _QT_LOG_FILE.write(
                f"[qt-{names.get(mode, mode)} {stamp}] {message}\n"
            )
            _QT_LOG_FILE.flush()
    except Exception:  # noqa: BLE001 - 日志失败不影响运行
        pass


def _enable_crash_log() -> None:
    """把原生崩溃（段错误等）发生时的 Python 调用栈写入 crash.log。

    同时打开 qt.log 供 Qt 消息处理器写入 qFatal/qWarning 文本
    （qFatal 的消息文本是定位“闪退”的关键）。
    文件位于 %LOCALAPPDATA%\\BangumiQuery\\（每次启动重写）。
    """
    global _CRASH_LOG_FILE, _QT_LOG_FILE
    try:
        log_dir = disk_cache.cache_root().parent
        log_dir.mkdir(parents=True, exist_ok=True)
        _CRASH_LOG_FILE = open(log_dir / "crash.log", "w", encoding="utf-8")
        faulthandler.enable(file=_CRASH_LOG_FILE, all_threads=True)
        _QT_LOG_FILE = open(log_dir / "qt.log", "w", encoding="utf-8")
    except Exception:  # noqa: BLE001 - 日志失败不影响运行
        _CRASH_LOG_FILE = None


def main() -> int:
    """GUI 程序入口：初始化网络设置并启动 Qt 事件循环。"""
    api.apply_network_settings()  # 沿用原有代理/超时配置逻辑
    _enable_crash_log()
    from PyQt5.QtCore import qInstallMessageHandler
    qInstallMessageHandler(_qt_message_handler)
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    # 全局字体用微软雅黑：Windows 默认字体渲染中文发虚，雅黑明显更清晰；
    # 各控件的专用字体（表格 12pt、标题 16pt 等）在此基础之上覆盖
    app.setFont(QFont("Microsoft YaHei UI", 10))
    # 全局主题（深色/浅色，记忆于 settings.json）
    theme = str(settings_store.load().get("theme", "浅色"))
    app.setStyleSheet(THEMES.get(theme, STYLE_SHEET_DARK))
    icon = _app_icon()
    if icon is not None:
        app.setWindowIcon(icon)  # 窗口/任务栏图标
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
