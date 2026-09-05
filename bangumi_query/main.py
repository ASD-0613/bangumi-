"""程序入口 —— PyQt5 图形界面（GUI）。

主窗口结构：
    ┌────────────────────────────────────────────┐
    │  搜索栏: [输入框…………] [搜索]              │
    ├────────────────────────────────────────────┤
    │  QTabWidget                                │
    │   ├ 搜索结果(表格, 可分页)                 │
    │   ├ 番剧详情(封面图 + 基本信息 + 分集)     │
    │   ├ 热门排行榜(表格, 可切换类别)           │
    │   └ 追番日历(按星期分组的树形列表)         │
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
import sys
import time
import traceback
from typing import Any, Awaitable, Callable, List, Optional, Sequence, Set, Tuple

try:  # GUI 依赖检测：缺少时给出清晰的中文提示
    import requests  # noqa: F401 - 用于后台线程下载封面图
    from PyQt5.QtCore import QSize, QThread, Qt, pyqtSignal
    from PyQt5.QtGui import QIcon, QImage, QPixmap
    from PyQt5.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QButtonGroup,
        QComboBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
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
from .models.bangumi import (
    EpisodeInfo,
    RankItem,
    SearchItem,
    SearchPage,
    SeasonDetail,
    StaffItem,
    TimelineDay,
)

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

        self._build_ui()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)

        # 顶部搜索栏
        search_bar = QHBoxLayout()
        search_bar.addWidget(QLabel("搜索番剧："))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入番剧名称 / 关键词，如：刀剑神域")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.returnPressed.connect(self.on_search_clicked)
        search_bar.addWidget(self.search_input, stretch=1)
        self.search_button = QPushButton("搜索")
        self.search_button.clicked.connect(self.on_search_clicked)
        search_bar.addWidget(self.search_button)
        root.addLayout(search_bar)

        # 多标签页
        self.tabs = QTabWidget(central)
        root.addWidget(self.tabs, stretch=1)

        # 底部：本地缓存占用提示 + 清除缓存按钮
        bottom_bar = QHBoxLayout()
        self.cache_info_label = QLabel("")
        self.cache_info_label.setStyleSheet("color: gray;")
        bottom_bar.addWidget(self.cache_info_label)
        bottom_bar.addStretch(1)
        self.clear_cache_button = QPushButton("清除缓存")
        self.clear_cache_button.clicked.connect(self.on_clear_cache)
        bottom_bar.addWidget(self.clear_cache_button)
        root.addLayout(bottom_bar)
        self._refresh_cache_info()

        self._build_search_tab()
        self._build_detail_tab()
        self._build_rank_tab()
        self._build_timeline_tab()

        # 首次切到“排行榜 / 追番日历”标签页时自动加载一次数据
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.statusBar().showMessage("就绪")

    def _build_search_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.search_table = self._create_table(self.SEARCH_HEADERS)
        self.search_table.cellDoubleClicked.connect(self.on_search_row_open)
        # 键盘回车（激活行）同样打开详情
        self.search_table.cellActivated.connect(self.on_search_row_open)
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

        # 标题行
        self.detail_title = QLabel("暂无详情（请先搜索或从榜单选择番剧）")
        self.detail_title.setWordWrap(True)
        font = self.detail_title.font()
        font.setPointSize(16)
        font.setBold(True)
        self.detail_title.setFont(font)
        outer.addWidget(self.detail_title)

        self.detail_subtitle = QLabel("")
        self.detail_subtitle.setWordWrap(True)
        self.detail_subtitle.setStyleSheet("color: gray;")
        outer.addWidget(self.detail_subtitle)

        # 封面 + 基本信息
        head = QHBoxLayout()
        self.cover_label = QLabel("暂无封面")
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setMinimumSize(150, 200)
        self.cover_label.setMaximumSize(190, 280)
        self.cover_label.setStyleSheet(
            "border: 1px solid #ccc; background-color: #f5f5f5;"
        )
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
        self.detail_intro.setFixedHeight(110)
        self.detail_intro.setPlainText("")
        outer.addWidget(self.detail_intro)

        # 声优 / 制作团队
        outer.addWidget(self._section_label("主要声优 / 制作团队"))
        self.detail_staff = QTextBrowser()
        self.detail_staff.setFixedHeight(130)
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
        self.detail_episode_hint.setStyleSheet("color: gray;")
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
        layout.addWidget(self.timeline_tree, stretch=1)

        # 默认高亮“今天”，列宽在首次渲染与窗口缩放时自适应
        if 1 <= self._timeline_selected <= len(self._timeline_day_buttons):
            self._timeline_day_buttons[self._timeline_selected - 1].setChecked(True)
        self.tabs.addTab(page, "追番日历")

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
        """首次切到“排行榜 / 追番日历”标签页时自动加载一次数据。"""
        if index == 2 and not self._rank_busy and not self._rank_items:
            self.on_rank_refresh()
        elif index == 3 and not self._timeline_busy and not self._timeline_days:
            self.on_timeline_refresh()

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

    def on_clear_cache(self) -> None:
        """清除本地磁盘缓存；成功后弹窗提示并刷新占用显示。"""
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
        self._refresh_cache_info()
        if count:
            message = (
                f"缓存清除成功：共清理 {count} 个文件，"
                f"释放 {format_bytes(freed)}。"
            )
        else:
            message = "缓存目录为空，没有需要清除的内容。"
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
        """搜索表格：封面列按行高、番剧名称列加宽。"""
        header = self.search_table.horizontalHeader()
        header.resizeSection(0, _COVER_COLUMN_WIDTH)
        header.resizeSection(1, 320)

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

        # 基本信息表
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
        # 表格较高时允许容器滚动
        row_h = self.detail_episode_table.verticalHeader().defaultSectionSize()
        self.detail_episode_table.setMaximumHeight(
            min(len(shown) * (row_h + 4) + 30, 420)
        )
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
        self._rank_request_signature = (idx, sort_key)
        self.rank_refresh_button.setEnabled(False)
        self.rank_prev_button.setEnabled(False)
        self.rank_next_button.setEnabled(False)
        self._busy(
            f"正在获取「{self._rank_request_label}」· 按「{sort_key}」榜单…"
        )

        worker = ApiWorker(
            lambda cat=category, sk=sort_key: api.fetch_ranking(cat, sort_key=sk)
        )
        worker.succeeded.connect(self._on_rank_result)
        worker.failed.connect(self._on_rank_error)
        self._register(worker)

    def _on_rank_error(self, message: str) -> None:
        """榜单请求失败：先恢复交互状态再提示，避免标签页永久卡死。"""
        self._rank_busy = False
        self.rank_refresh_button.setEnabled(True)
        self.rank_prev_button.setEnabled(self._rank_page > 1)
        self.rank_next_button.setEnabled(self._rank_page < self._rank_pages)
        self._on_task_error(message)
        # 请求期间用户改过筛选条件：按最新选择自动补发
        if self._rank_request_signature != (
            self.rank_combo.currentIndex(), self._rank_sort_key()
        ):
            self.on_rank_refresh()

    def _on_rank_result(self, result: Any) -> None:
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
        self.tabs.setCurrentIndex(2)
        self.statusBar().showMessage(
            f"「{self._rank_request_label or self.rank_combo.currentText()}」"
            f"排行榜共 {len(self._rank_items)} 条"
        )
        # 请求期间用户改过类别/维度：按最新选择自动补发一次
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

        # 封面列宽贴合行高；番剧名称列宽更大
        header = self.rank_table.horizontalHeader()
        header.resizeSection(0, _COVER_COLUMN_WIDTH)
        header.resizeSection(1, 48)
        header.resizeSection(2, 320)

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
        """日历请求失败：先恢复交互状态再提示，避免标签页永久卡死。"""
        self._timeline_busy = False
        self.timeline_refresh_button.setEnabled(True)
        self._on_task_error(message)
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
        self.tabs.setCurrentIndex(3)
        total = sum(len(day.items) for day in self._timeline_days)
        self.statusBar().showMessage(f"放送日历共 {total} 部番剧")
        # 请求期间用户改过日历类型：按最新选择自动补发一次
        if self._timeline_request_signature != (self.timeline_combo.currentIndex(),):
            self.on_timeline_refresh()

    def _render_timeline_day(self) -> None:
        """渲染当前选中星期的条目：无折叠分组，直接平铺为数据行。"""
        self.timeline_tree.clear()
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
        """封面列固定宽；番剧名称列约占数据栏一半宽。"""
        view_width = self.timeline_tree.viewport().width()
        header = self.timeline_tree.header()
        name_width = max(240, int(view_width * 0.5))
        header.resizeSection(0, _COVER_COLUMN_WIDTH)  # 封面
        header.resizeSection(1, name_width)            # 番剧名称 ≈ 一半
        header.resizeSection(2, 150)                   # 开播时间

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

    def resizeEvent(self, event: Any) -> None:
        """窗口缩放时同步调整追番日历列宽（番剧名称列≈一半）。"""
        super().resizeEvent(event)
        if hasattr(self, "timeline_tree"):
            self._apply_timeline_widths()

    # ------------------------------------------------------------------
    # 关闭处理
    # ------------------------------------------------------------------

    def closeEvent(self, event: Any) -> None:
        """退出前等待 / 终止仍在运行的后台线程，避免 Qt 崩溃。

        所有线程共享约 2 秒的优雅收尾窗口；仍阻塞在网络请求上的线程
        只能 terminate（Qt 不推荐，但作为最后手段，好过退出挂死）。
        """
        deadline = time.monotonic() + 2.0
        for worker in list(self._workers):
            remaining_ms = int(max(0.0, deadline - time.monotonic()) * 1000)
            if remaining_ms > 0 and worker.wait(remaining_ms):
                continue
            worker.requestInterruption()
            worker.terminate()
            worker.wait(200)
        event.accept()


def main() -> int:
    """GUI 程序入口：初始化网络设置并启动 Qt 事件循环。"""
    api.apply_network_settings()  # 沿用原有代理/超时配置逻辑
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
