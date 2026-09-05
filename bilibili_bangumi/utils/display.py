"""终端美化输出（基于 rich）。

负责把数据模型渲染成清晰美观的终端界面：菜单面板、表格、
分组展示与颜色状态提示等。展示层只依赖 models 数据模型与 rich，
不发起任何网络请求。
"""

from __future__ import annotations

import sys
from typing import Iterable, Optional, Sequence

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .. import config
from ..models.bangumi import (
    EpisodeInfo,
    RankItem,
    SearchItem,
    SeasonDetail,
    StaffItem,
    TimelineDay,
)


def _prepare_stdout_for_redirect() -> None:
    """输出被重定向（管道/文件）时把 stdout 切换到 UTF-8。

    B 站番剧标题常含日文假名（如“・”），若按 Windows GBK 编码写入
    重定向流会触发 UnicodeEncodeError；交互式终端保持系统编码即可
    （rich 走 Windows 控制台 Unicode API，无此问题）。
    """
    try:
        if sys.stdout is not None and not sys.stdout.isatty():
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass


_prepare_stdout_for_redirect()

# 全局控制台（单测中可整体替换为 Console(record=True, file=io.StringIO())）
CONSOLE: Console = Console()

# 主题色
C_ACCENT: str = "cyan"
C_OK: str = "green"
C_WARN: str = "yellow"
C_ERR: str = "red"
C_DIM: str = "dim"
C_RANK: str = "bold magenta"
C_SCORE_GOLD: str = "bold yellow"
C_SCORE_GREEN: str = "green"
C_TODAY: str = "bold yellow"


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def human_number(value: Optional[float]) -> str:
    """把较大的数值格式化为“1.2 万”等更易读的形式。

    Args:
        value: 数值。

    Returns:
        格式化字符串；无法识别时返回 "—"。
    """
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


def _score_text(score: Optional[float]) -> Text:
    """评分单元格：9 分以上金色、8 分以上绿色、其余普通。"""
    if score is None:
        return Text("—", style=C_DIM)
    style = C_SCORE_GOLD if score >= 9.0 else C_SCORE_GREEN if score >= 8.0 else ""
    return Text(f"{score:.1f}", style=style)


def _status_text(status: Optional[str]) -> Text:
    """状态单元格着色：连载中/已完结/未开播。"""
    if status is None:
        return Text("—", style=C_DIM)
    if status == "连载中":
        return Text("连载中", style=C_OK)
    if status == "已完结":
        return Text("已完结", style=C_DIM)
    if status == "未开播":
        return Text("未开播", style=C_WARN)
    return Text(status)


def _mark_cell(text: str) -> Text:
    """普通文本单元格（自动转义富文本标记）。"""
    return Text(escape(str(text)))


# ---------------------------------------------------------------------------
# 基础提示
# ---------------------------------------------------------------------------


def show_banner() -> None:
    """打印程序启动横幅。"""
    panel = Panel(
        Text(
            f"{config.APP_NAME}  v{config.VERSION}",
            style=C_ACCENT,
            justify="center",
        ),
        subtitle="基于 bilibili-api-python + rich",
        subtitle_align="center",
        border_style="cyan",
        padding=(1, 4),
    )
    CONSOLE.print(panel)


def show_menu() -> None:
    """打印主菜单。"""
    menu = Table(show_header=False, box=None, pad_edge=False, expand=False)
    menu.add_column(no_wrap=True)
    items = [
        ("1", "搜索番剧"),
        ("2", "查看番剧详情"),
        ("3", "排行榜"),
        ("4", "本周更新日历"),
        ("5", "退出"),
    ]
    for num, label in items:
        menu.add_row(
            Text(f"  {num}. ", style=f"bold {C_ACCENT}"),
            Text(label),
        )
    CONSOLE.print(
        Panel(
            Group(Text("请选择功能：", style="bold"), menu),
            title="主菜单",
            border_style="cyan",
        )
    )


def print_error(message: str) -> None:
    """打印错误信息（红色）。"""
    CONSOLE.print(f"[{C_ERR}][错误][/{C_ERR}] {escape(str(message))}")


def print_warning(message: str) -> None:
    """打印警告信息（黄色）。"""
    CONSOLE.print(f"[{C_WARN}][警告][/{C_WARN}] {escape(str(message))}")


def print_info(message: str) -> None:
    """打印提示信息（青色）。"""
    CONSOLE.print(f"[{C_ACCENT}][提示][/{C_ACCENT}] {escape(str(message))}")


def print_ok(message: str) -> None:
    """打印成功信息（绿色）。"""
    CONSOLE.print(f"[{C_OK}][完成][/{C_OK}] {escape(str(message))}")


# ---------------------------------------------------------------------------
# 1. 搜索结果显示
# ---------------------------------------------------------------------------


def print_search_page(
    items: Sequence[SearchItem],
    page: int,
    total_pages: int,
    keyword: str = "",
) -> None:
    """打印一页搜索结果表格。

    Args:
        items: 本页番剧条目。
        page: 当前页码。
        total_pages: 总页数。
        keyword: 搜索关键词（用于标题显示）。
    """
    title = f"搜索结果 - 第 {page}/{total_pages} 页"
    if keyword:
        title += f"  ·  关键词「{escape(keyword)}」"
    if not items:
        CONSOLE.print(Panel(Text("没有找到相关番剧，换个关键词试试吧", style=C_DIM), title=title))
        return

    table = Table(
        title=title,
        title_style="bold",
        header_style=C_ACCENT,
        border_style="cyan",
        expand=False,
    )
    table.add_column("序号", justify="right", style=C_RANK, width=5)
    table.add_column("番剧名称", min_width=16, overflow="fold")
    table.add_column("类型", style=C_DIM, no_wrap=True)
    table.add_column("地区", style=C_DIM, no_wrap=True)
    table.add_column("状态", no_wrap=True)
    table.add_column("评分", justify="right", no_wrap=True)
    table.add_column("更新情况", no_wrap=True)
    table.add_column("开播/更新时间", no_wrap=True)

    for idx, item in enumerate(items, start=1):
        styles = "、".join(item.styles[:3]) or "—"
        table.add_row(
            str(idx),
            Text(item.title, overflow="fold"),
            Text(item.category or "番剧", style=C_DIM),
            Text("、".join(item.areas) or "—", style=C_DIM),
            _status_text(item.status),
            _score_text(item.score),
            Text(item.update_desc or "—"),
            Text(item.pub_time or "—"),
        )
    CONSOLE.print(table)


# ---------------------------------------------------------------------------
# 2. 番剧详情显示
# ---------------------------------------------------------------------------


def _kv_table(rows: Iterable[tuple]) -> Table:
    """构建“键-值”两列信息表。"""
    table = Table(show_header=False, box=None, pad_edge=False, expand=False)
    table.add_column(style=f"bold {C_ACCENT}", width=10, no_wrap=True)
    table.add_column(min_width=30, overflow="fold")
    for key, value in rows:
        table.add_row(key, value)
    return table


def print_season_detail(detail: SeasonDetail) -> None:
    """打印番剧完整详情。

    Args:
        detail: 番剧详情数据。
    """
    # ---------- 头部：标题 + 基本信息 ----------
    header = Text()
    header.append(detail.title, style="bold")
    if detail.original_title and detail.original_title != detail.title:
        header.append(f"  {detail.original_title}", style=C_DIM)

    basic_rows = []
    basic_rows.append(("类别", detail.category or "番剧"))
    basic_rows.append(("状态", detail.status or "—"))
    basic_rows.append(("地区", "、".join(detail.areas) or "—"))
    basic_rows.append(("风格", "、".join(detail.styles) or "—"))
    basic_rows.append(("开播时间", detail.pub_time or "—"))
    if detail.episode_total is not None:
        basic_rows.append(("总集数", str(detail.episode_total)))
    if detail.newest_ep_desc:
        basic_rows.append(("最新一话", detail.newest_ep_desc))

    # 评分
    if detail.score is not None:
        stars = "★" * round(detail.score) + "☆" * (10 - round(detail.score))
        rating_text = Text(f"{detail.score:.1f} 分", style=C_SCORE_GOLD)
        rating_text.append(f"  {stars}", style=C_WARN)
        if detail.score_count:
            rating_text.append(
                f"（{human_number(float(detail.score_count))} 人评分）", style=C_DIM
            )
        basic_rows.append(("评分", rating_text))
    if detail.views is not None:
        basic_rows.append(("播放量", human_number(float(detail.views))))
    if detail.follows is not None:
        basic_rows.append(("追番人数", human_number(float(detail.follows))))
    if detail.share_url:
        basic_rows.append(("链接", detail.share_url))

    CONSOLE.print(
        Panel(
            Group(header, _kv_table(basic_rows)),
            title=f"番剧详情 · season_id={detail.season_id}",
            border_style="cyan",
        )
    )

    # ---------- 简介 ----------
    CONSOLE.print(Rule("剧情简介", style=C_ACCENT))
    if detail.evaluate:
        CONSOLE.print(Text(detail.evaluate))
    else:
        CONSOLE.print(Text("暂无简介", style=C_DIM))

    # ---------- 声优 ----------
    CONSOLE.print(Rule("主要声优", style=C_ACCENT))
    if detail.casts:
        _print_staff_table(detail.casts, header=("角色", "声优"))
    else:
        CONSOLE.print(Text("未获取到声优信息", style=C_DIM))

    # ---------- 制作团队 ----------
    CONSOLE.print(Rule("制作团队", style=C_ACCENT))
    if detail.staff:
        _print_staff_table(detail.staff, header=("职位", "姓名"))
    else:
        CONSOLE.print(Text("未获取到制作团队信息", style=C_DIM))

    # ---------- 分集 ----------
    CONSOLE.print(Rule("分集列表", style=C_ACCENT))
    _print_episodes(detail.episodes)


def _print_staff_table(staff: Sequence[StaffItem], header: tuple) -> None:
    """打印声优/制作团队的“角色-姓名”表格。"""
    table = Table(header_style=C_ACCENT, border_style="dim", show_lines=False)
    table.add_column(header[0], min_width=8, overflow="fold", style="bold")
    table.add_column(header[1], min_width=10, overflow="fold")
    for item in staff:
        table.add_row(Text(item.role or "—"), Text(item.name or "—"))
    CONSOLE.print(table)


def _print_episodes(episodes: Sequence[EpisodeInfo]) -> None:
    """打印分集表格（超出上限时截断并提示）。"""
    if not episodes:
        CONSOLE.print(Text("暂无分集信息", style=C_DIM))
        return
    shown = episodes[: config.EPISODE_DISPLAY_LIMIT]
    table = Table(
        header_style=C_ACCENT,
        border_style="cyan",
        show_lines=False,
        expand=False,
    )
    table.add_column("集数", justify="right", style=C_RANK, width=6, no_wrap=True)
    table.add_column("标题", min_width=12, overflow="fold")
    table.add_column("发布时间", no_wrap=True, style=C_DIM)
    for idx, ep in enumerate(shown, start=1):
        table.add_row(
            str(idx),
            Text(ep.display_title or "—", overflow="fold"),
            Text(ep.pub_time or "—"),
        )
    CONSOLE.print(table)
    if len(episodes) > config.EPISODE_DISPLAY_LIMIT:
        CONSOLE.print(
            Text(
                f"… 共 {len(episodes)} 集，仅展示前 {config.EPISODE_DISPLAY_LIMIT} 集",
                style=C_DIM,
            )
        )


# ---------------------------------------------------------------------------
# 3. 排行榜显示
# ---------------------------------------------------------------------------


def print_ranking(items: Sequence[RankItem], label: str = "") -> None:
    """打印排行榜。

    Args:
        items: 排行数据（已带名次）。
        label: 排行榜类别名（用于标题）。
    """
    if not items:
        CONSOLE.print(Panel(Text("排行榜暂无数据", style=C_DIM)))
        return
    # 指标列含义：取第一个有条目含义的类型（热度/收藏数等）
    heat_kind = next((i.heat_kind for i in items if i.heat_kind), "热度")
    table = Table(
        title=f"{escape(label)} 排行榜  TOP {len(items)}",
        title_style="bold",
        header_style=C_ACCENT,
        border_style="cyan",
        expand=False,
    )
    table.add_column("排名", justify="center", style=C_RANK, width=6, no_wrap=True)
    table.add_column("番剧名称", min_width=16, overflow="fold")
    table.add_column("类型", style=C_DIM, no_wrap=True)
    table.add_column("评分", justify="right", no_wrap=True)
    table.add_column(heat_kind, justify="right", no_wrap=True)
    table.add_column("开播时间", no_wrap=True, style=C_DIM)
    for item in items:
        table.add_row(
            str(item.rank),
            Text(item.title or "—", overflow="fold"),
            Text(item.category or "—", style=C_DIM),
            _score_text(item.score),
            Text(human_number(item.heat_value), justify="right"),
            Text(item.pub_time or "—"),
        )
    CONSOLE.print(table)


# ---------------------------------------------------------------------------
# 4. 追番日历（按星期分组）
# ---------------------------------------------------------------------------


def print_timeline(days: Sequence[TimelineDay]) -> None:
    """按星期分组打印本周更新日历。

    Args:
        days: 按日期排序的更新时间线。
    """
    if not days:
        CONSOLE.print(Panel(Text("本周暂无更新数据（或接口返回为空）", style=C_DIM)))
        return
    total = sum(len(day.items) for day in days)
    CONSOLE.print(
        Panel(
            Text(f"本周更新日历 · 覆盖 {len(days)} 天 · 共 {total} 部番剧", style=C_ACCENT),
            border_style="cyan",
        )
    )
    for day in days:
        _print_timeline_day(day)


def _print_timeline_day(day: TimelineDay) -> None:
    """打印某一天的更新列表（一张小表格）。"""
    title_parts = [day.weekday_cn]
    if day.date:
        title_parts.append(day.date)
    title = " ".join(title_parts)
    if day.is_today:
        title = f"{title}  ★ 今天"
    table = Table(
        title=title,
        title_style=C_TODAY if day.is_today else "bold",
        header_style=C_ACCENT,
        border_style="dim",
        expand=False,
    )
    table.add_column("番剧名称", min_width=16, overflow="fold")
    table.add_column("更新时间", no_wrap=True, style=C_DIM)
    table.add_column("更新内容", no_wrap=True, style=C_OK)
    for item in day.items:
        table.add_row(
            Text(item.title or "—", overflow="fold"),
            Text(item.pub_time or "—"),
            Text(item.ep_label or "更新"),
        )
    CONSOLE.print(table)
