"""“已看完”标记的本地持久化（JSON 文件）。

存储位置：缓存根目录的**上一级**（``watched.json``，与 cache/ 文件夹同级）。
这样“清除缓存”按钮只清空缓存目录，不会误删用户的观看记录。

文件结构（顺序即“已看完”页的展示顺序，最新打钩的在前）::

    {"version": 1, "items": [{"id": 33346, "title": "刀剑神域",
                              "cover": "https://..."}]}

所有写入均为“尽力而为”：磁盘不可写等异常被吞掉（只影响本次保存）；
读取时文件缺失 / 损坏一律按空列表处理，绝不抛异常。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .cache import cache_root

__all__ = ["watched_path", "load_items", "contains", "add", "remove", "clear"]


def watched_path() -> Path:
    """watched.json 的存放路径（缓存目录的上一级）。"""
    return cache_root().parent / "watched.json"


def load_items() -> List[Dict[str, Any]]:
    """读取全部已看完条目（最新在前）；文件缺失/损坏返回空列表。"""
    try:
        data = json.loads(watched_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    result: List[Dict[str, Any]] = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("id"), int):
            result.append({
                "id": it["id"],
                "title": str(it.get("title") or ""),
                "cover": str(it.get("cover") or ""),
            })
    return result


def _save(items: List[Dict[str, Any]]) -> None:
    try:
        path = watched_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "items": items},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass


def contains(subject_id: int) -> bool:
    """该条目是否已被标记为已看完。"""
    return any(it["id"] == subject_id for it in load_items())


def add(subject_id: int, title: str = "", cover: str = "") -> None:
    """打钩：加入（或置顶更新）条目。"""
    items = [it for it in load_items() if it["id"] != subject_id]
    items.insert(0, {"id": subject_id, "title": title, "cover": cover})
    _save(items)


def remove(subject_id: int) -> None:
    """取消打钩：移出条目（不存在时静默）。"""
    items = load_items()
    remaining = [it for it in items if it["id"] != subject_id]
    if len(remaining) != len(items):
        _save(remaining)


def clear() -> None:
    """删除整个 watched.json（不存在时静默）。"""
    try:
        watched_path().unlink()
    except OSError:
        pass
