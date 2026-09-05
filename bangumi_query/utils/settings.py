"""界面偏好持久化（列宽等）—— ``settings.json``。

存放位置：缓存目录的**上一级**（与 watched.json 同级），因此“清除缓存”
不会影响用户的界面偏好。键值随功能演进向前兼容：update() 只合并已知键，
未认识的键原样保留。

所有写入均为“尽力而为”：磁盘不可写等异常被吞掉（只影响下次恢复）；
读取时文件缺失 / 损坏一律按空字典处理，绝不抛异常。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .cache import cache_root

__all__ = ["settings_path", "load", "update"]


def settings_path() -> Path:
    """settings.json 的存放路径（缓存目录的上一级）。"""
    return cache_root().parent / "settings.json"


def load() -> Dict[str, Any]:
    """读取全部界面偏好；文件缺失/损坏返回空字典。"""
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update(**kwargs: Any) -> None:
    """合并保存偏好（未知键原样保留；原子写入，尽力而为）。"""
    data = load()
    data.update(kwargs)
    try:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(path)
    except OSError:
        pass
