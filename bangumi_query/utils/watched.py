"""“正在看 / 已看完”标记的本地持久化（JSON 文件，v2 结构）。

存储位置：缓存根目录的**上一级**（``watched.json``，与 cache/ 文件夹同级）。
这样“清除缓存”按钮只清空缓存目录，不会误删用户的观看记录。

文件结构（单一列表，条目带状态；每个状态内的展示顺序 = 最新标记在前）::

    {"version": 2,
     "items": [{"id": 33346, "title": "刀剑神域",
                "cover": "https://...", "state": "watching"}]}

- ``state`` 仅两种：``watching``（正在看）/ ``watched``（已看完）；
- v1 结构（条目无 state）自动按“已看完”迁移；
- 同一条目改变状态时**原地更新状态并移到对应序列最前**。

所有写入均为“尽力而为”：磁盘不可写等异常被吞掉；读取时文件缺失 /
损坏一律按空列表处理，绝不抛异常。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cache import cache_root

__all__ = [
    "watched_path",
    "load_items",
    "state_of",
    "set_state",
    "remove",
    "clear",
]

STATE_WATCHING = "watching"
STATE_WATCHED = "watched"
_VALID_STATES = (STATE_WATCHING, STATE_WATCHED)


def watched_path() -> Path:
    """watched.json 的存放路径（缓存目录的上一级）。"""
    return cache_root().parent / "watched.json"


def load_items(state: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取条目；指定 state 时只返回该状态的条目（最新在前）。"""
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
            entry = {
                "id": it["id"],
                "title": str(it.get("title") or ""),
                "cover": str(it.get("cover") or ""),
                # v1 无 state：一律按“已看完”迁移
                "state": (it.get("state")
                          if it.get("state") in _VALID_STATES
                          else STATE_WATCHED),
            }
            if state is None or entry["state"] == state:
                result.append(entry)
    return result


def _save(items: List[Dict[str, Any]]) -> None:
    try:
        path = watched_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"version": 2, "items": items},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass


def state_of(subject_id: int) -> Optional[str]:
    """该条目的当前状态；未标记返回 None。"""
    for it in load_items():
        if it["id"] == subject_id:
            return it["state"]
    return None


def set_state(subject_id: int, title: str = "", cover: str = "",
              state: str = STATE_WATCHED) -> None:
    """标记/改变状态：原地更新并移到该状态序列最前（去重）。"""
    if state not in _VALID_STATES:
        state = STATE_WATCHED
    items = load_items()
    items = [it for it in items if it["id"] != subject_id]
    items.insert(0, {"id": subject_id, "title": title,
                     "cover": cover, "state": state})
    _save(items)


def remove(subject_id: int) -> None:
    """取消标记：移出条目（不存在时静默）。"""
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
