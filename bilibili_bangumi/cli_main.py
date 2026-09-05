"""命令行交互界面（备份入口，原 main.py）。

自 GUI 版本（main.py）引入后，本文件作为命令行版保留备份。

启动方式（项目根目录下）：
    python -m bilibili_bangumi.cli_main

界面提供五个功能：
    1. 搜索番剧
    2. 查看番剧详情
    3. 热门排行榜
    4. 本周更新日历
    5. 退出
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from typing import Any, Dict, Optional

from . import config
from .api import bangumi as api
from .models.bangumi import SeasonDetail
from .utils import display as view


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _ask(prompt: str) -> str:
    """从命令行读取一行输入并去掉首尾空白。"""
    return input(prompt).strip()


def _pause() -> None:
    """等待用户按回车返回。"""
    input("按回车键返回…")


def _is_back(cmd: str) -> bool:
    """判断输入是否为“返回/退出”。"""
    return cmd.lower() in ("q", "quit", "exit", "返回", "退出", "0")


# ---------------------------------------------------------------------------
# 详情展示（供搜索 / 排行榜复用）
# ---------------------------------------------------------------------------


async def _show_detail(season_id: Optional[int]) -> None:
    """按 season_id 拉取并展示番剧详情。

    Args:
        season_id: 番剧季度 ID；为 None 时仅提示。
    """
    if not season_id:
        view.print_warning("该条目缺少 season_id，无法查看详情")
        return
    try:
        view.print_info(f"正在获取番剧详情（season_id={season_id}）…")
        detail: SeasonDetail = await api.get_season_detail(season_id)
    except Exception as exc:  # noqa: BLE001 - 界面层统一兜底
        if config.DEBUG:
            traceback.print_exc()
        view.print_error(api.describe_error(exc))
        return
    view.print_season_detail(detail)


def _pick_index(prompt: str, maximum: int) -> Optional[int]:
    """读取用户输入的 1..maximum 序号，返回 None 表示放弃选择。

    Args:
        prompt: 输入提示语。
        maximum: 可选序号上限。

    Returns:
        合法序号；输入 q 返回 None。
    """
    cmd = _ask(prompt)
    if _is_back(cmd):
        return None
    if cmd.isdigit():
        value = int(cmd)
        if 1 <= value <= maximum:
            return value
        view.print_warning(f"请输入 1~{maximum} 之间的序号")
        return None
    view.print_warning("输入无效")
    return None


# ---------------------------------------------------------------------------
# 功能 1：搜索番剧（分页 + 可直接进详情）
# ---------------------------------------------------------------------------


async def search_flow() -> None:
    """搜索番剧交互流程：关键词 -> 分页浏览 -> 可选查看详情。"""
    while True:
        keyword = _ask("请输入搜索关键词（输入 q 返回主菜单）：")
        if _is_back(keyword):
            return
        if not keyword:
            view.print_warning("关键词不能为空")
            continue

        page = 1
        while True:
            try:
                result = await api.search_bangumi(keyword, page=page)
            except Exception as exc:  # noqa: BLE001
                if config.DEBUG:
                    traceback.print_exc()
                view.print_error(api.describe_error(exc))
                break
            view.print_search_page(
                result.items, page=result.page, total_pages=result.total_pages,
                keyword=keyword,
            )
            if result.empty:
                break

            cmd = _ask(
                "[回车]下一页  [p]上一页  [序号]查看详情  [q]退出搜索："
            ).strip().lower()
            if _is_back(cmd):
                return  # 浏览结束，直接回到主菜单
            if cmd == "p":
                page = max(1, page - 1)
                continue
            if cmd == "":
                if page < result.total_pages:
                    page += 1
                else:
                    view.print_warning("已经是最后一页")
                continue
            if cmd.isdigit():
                index = int(cmd)
                if 1 <= index <= len(result.items):
                    await _show_detail(result.items[index - 1].season_id)
                    continue
                view.print_warning(f"请输入 1~{len(result.items)} 之间的序号")
                continue
            view.print_warning("输入无效，请输入数字、p 或 q")


# ---------------------------------------------------------------------------
# 功能 2：查看番剧详情（支持 ID 或名称）
# ---------------------------------------------------------------------------


async def detail_flow() -> None:
    """番剧详情交互流程：输入 ID 或名称（名称走搜索后选择）。"""
    while True:
        raw = _ask("请输入番剧 ID（season_id，纯数字）或名称（输入 q 返回主菜单）：")
        if _is_back(raw):
            return
        if not raw:
            view.print_warning("输入不能为空")
            continue

        # 直接是 ID
        if raw.isdigit():
            await _show_detail(int(raw))
            _pause()
            continue

        # 名称 -> 先搜索第一页让用户选择
        try:
            result = await api.search_bangumi(raw, page=1)
        except Exception as exc:  # noqa: BLE001
            if config.DEBUG:
                traceback.print_exc()
            view.print_error(api.describe_error(exc))
            continue
        if result.empty:
            view.print_warning(f"没有搜索到与「{raw}」相关的番剧")
            continue
        view.print_info(f"为您找到以下番剧（第 1 页），请输入序号查看详情：")
        view.print_search_page(result.items, page=1, total_pages=result.total_pages)
        index = _pick_index("请输入序号（q 返回）：", len(result.items))
        if index is None:
            continue
        await _show_detail(result.items[index - 1].season_id)
        _pause()


# ---------------------------------------------------------------------------
# 功能 3：排行榜（按类别筛选，默认按热度排序）
# ---------------------------------------------------------------------------


async def rank_flow() -> None:
    """排行榜交互流程：选择类别 -> 查看榜单 -> 可选看详情。"""
    while True:
        view.print_info("请选择排行榜类别：")
        for idx, cat in enumerate(config.RANK_CATEGORIES, start=1):
            print(f"  {idx}. {cat['label']}")
        print("  0. 返回主菜单")
        raw = _ask("请输入类别序号：")
        if _is_back(raw):
            return
        if not raw.isdigit() or not (1 <= int(raw) <= len(config.RANK_CATEGORIES)):
            view.print_warning("请输入有效序号")
            continue
        category: Dict[str, Any] = config.RANK_CATEGORIES[int(raw) - 1]
        short: str = str(category.get("short", ""))
        try:
            view.print_info(f"正在获取「{category['label']}」排行榜（按热度）…")
            items = await api.fetch_ranking(category)
        except Exception as exc:  # noqa: BLE001
            if config.DEBUG:
                traceback.print_exc()
            view.print_error(api.describe_error(exc))
            continue
        view.print_ranking(items, label=short)
        if not items:
            continue
        # 排行榜也可以直接看详情
        index = _pick_index("输入序号查看番剧详情，回车重新选择类别：", len(items))
        if index is not None:
            await _show_detail(items[index - 1].season_id)


# ---------------------------------------------------------------------------
# 功能 4：本周更新日历（按星期分组）
# ---------------------------------------------------------------------------


async def timeline_flow() -> None:
    """本周更新日历交互流程：选择类型 -> 按星期分组展示。"""
    while True:
        view.print_info("请选择日历类型：")
        for idx, item in enumerate(config.TIMELINE_TYPES, start=1):
            print(f"  {idx}. {item['label']}")
        print("  0. 返回主菜单")
        raw = _ask("请输入类型序号：")
        if _is_back(raw):
            return
        if not raw.isdigit() or not (1 <= int(raw) <= len(config.TIMELINE_TYPES)):
            view.print_warning("请输入有效序号")
            continue
        type_item: Dict[str, Any] = config.TIMELINE_TYPES[int(raw) - 1]
        label: str = str(type_item["label"])
        type_value: int = int(type_item["value"])
        try:
            view.print_info(f"正在获取「{label}」本周更新日历…")
            days = await api.fetch_timeline(type_value)
        except Exception as exc:  # noqa: BLE001
            if config.DEBUG:
                traceback.print_exc()
            view.print_error(api.describe_error(exc))
            continue
        view.print_timeline(days)
        if days:
            _pause()


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------


async def main_loop() -> None:
    """主循环：横幅 -> 菜单 -> 分发功能。"""
    api.apply_network_settings()
    view.show_banner()
    while True:
        view.show_menu()
        choice = _ask("请输入功能序号（1-5）：")
        if choice in ("5",) or _is_back(choice):
            break
        if choice == "1":
            await search_flow()
        elif choice == "2":
            await detail_flow()
        elif choice == "3":
            await rank_flow()
        elif choice == "4":
            await timeline_flow()
        else:
            view.print_warning("无效输入，请输入 1~5 之间的数字")


def main() -> None:
    """命令行入口：负责环境初始化与异常兜底。"""
    _setup_stdout_encoding()
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print()
        view.print_info("已退出（Ctrl+C）")
    print()
    view.print_ok(f"感谢使用 {config.APP_NAME}，再见！")


def _setup_stdout_encoding() -> None:
    """输出被重定向（管道/文件）时以 UTF-8 输出，避免 Windows 下乱码。

    交互式终端保持系统编码（rich 通过 Windows 控制台 Unicode API 输出，
    无需也不应强制 UTF-8，否则可能让 GBK 控制台的中文乱码）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    main()
