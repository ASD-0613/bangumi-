"""本地磁盘缓存：封面图等展示用资源的持久化缓存。

封面原图体积较大且重复观看率高：首次下载后写入本地缓存目录，再次显示时
直接读缓存，避免重复消耗带宽。缓存仅用于“展示用资源”（图片字节）；接口
JSON 数据不在此缓存，以保证评分 / 榜单等数据的时效性。

约定：
- 缓存根目录：环境变量 ``BANGUMI_CACHE_DIR`` 优先（每次调用时读取，便于
  测试隔离）；默认 Windows 为 ``%LOCALAPPDATA%\\BangumiQuery\\cache``，
  其它平台为 ``~/.cache/BangumiQuery``；
- 缓存文件名 = 资源 URL 的 SHA-1 摘要（无扩展名，QImage 按内容识别格式）；
- 写入为“尽力而为”：磁盘不可写等异常被吞掉（只影响下次命中，不影响
  功能）；``clear_cache()`` 的异常向上抛出，由调用方提示用户。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path
from typing import Optional, Tuple

__all__ = [
    "cache_root",
    "cache_path_for",
    "load_cached_bytes",
    "store_cached_bytes",
    "cache_size",
    "clear_cache",
]


def cache_root() -> Path:
    """返回缓存根目录（写入 / 清理前由调用方按需创建）。"""
    env = os.environ.get("BANGUMI_CACHE_DIR", "").strip()
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        return Path(base) / "BangumiQuery" / "cache"
    return Path.home() / ".cache" / "BangumiQuery"


def cache_path_for(url: str) -> Path:
    """资源 URL -> 缓存文件路径（SHA-1 命名，规避特殊字符 / 超长路径）。"""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return cache_root() / digest


def load_cached_bytes(url: str) -> Optional[bytes]:
    """读取缓存内容；未命中返回 None（空文件视为损坏并删除）。"""
    try:
        path = cache_path_for(url)
        if path.is_file():
            data = path.read_bytes()
            if data:
                return data
            path.unlink()  # 空文件按损坏处理
    except OSError:
        return None
    return None


def store_cached_bytes(url: str, data: bytes) -> None:
    """把数据写入缓存（尽力而为；失败静默，只影响下次命中）。

    先写临时文件再原子替换，避免并发下载同一封面时留下半个文件。
    """
    if not data:
        return
    try:
        root = cache_root()
        root.mkdir(parents=True, exist_ok=True)
        path = cache_path_for(url)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        pass


def cache_size() -> Tuple[int, int]:
    """缓存占用统计：(文件数, 总字节数)。目录不存在时为 (0, 0)。"""
    try:
        entries = list(cache_root().iterdir())
    except OSError:
        return 0, 0
    count = 0
    total = 0
    for entry in entries:
        try:
            if entry.is_file():
                count += 1
                total += entry.stat().st_size
        except OSError:
            continue
    return count, total


def clear_cache() -> Tuple[int, int]:
    """清空缓存目录，返回 (删除文件数, 释放字节数)。

    Raises:
        OSError: 目录 / 文件无法删除（由调用方决定如何提示）。
    """
    root = cache_root()
    if not root.exists():
        return 0, 0
    count = 0
    freed = 0
    for entry in root.iterdir():
        try:
            if entry.is_file():
                freed += entry.stat().st_size
                entry.unlink()
                count += 1
            elif entry.is_dir():
                shutil.rmtree(entry)
        except OSError as exc:
            raise OSError(f"无法删除缓存项 {entry}：{exc}") from exc
    return count, freed
