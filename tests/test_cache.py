"""本地磁盘缓存模块测试（离线，使用临时目录隔离）。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bilibili_bangumi.utils import cache as disk_cache


class TestDiskCache(unittest.TestCase):
    """cache 模块的存取 / 统计 / 清理行为。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_dir = os.environ.get("BANGUMI_CACHE_DIR")
        os.environ["BANGUMI_CACHE_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        if self._old_dir is None:
            os.environ.pop("BANGUMI_CACHE_DIR", None)
        else:
            os.environ["BANGUMI_CACHE_DIR"] = self._old_dir
        self._tmp.cleanup()

    def test_store_and_load_roundtrip(self) -> None:
        url = "http://lain.bgm.tv/pic/cover/l/6e/e1/33346_l.jpg"
        self.assertIsNone(disk_cache.load_cached_bytes(url))
        disk_cache.store_cached_bytes(url, b"\x89PNG-fake-bytes")
        self.assertEqual(
            disk_cache.load_cached_bytes(url), b"\x89PNG-fake-bytes"
        )

    def test_store_empty_ignored(self) -> None:
        disk_cache.store_cached_bytes("u-empty", b"")
        self.assertIsNone(disk_cache.load_cached_bytes("u-empty"))
        self.assertEqual(disk_cache.cache_size(), (0, 0))

    def test_url_maps_to_stable_path(self) -> None:
        url = "http://example.com/a.jpg"
        path = disk_cache.cache_path_for(url)
        self.assertEqual(path, disk_cache.cache_path_for(url))
        self.assertTrue(str(path.parent) == str(disk_cache.cache_root()))

    def test_cache_size(self) -> None:
        self.assertEqual(disk_cache.cache_size(), (0, 0))
        disk_cache.store_cached_bytes("a", b"12345")
        disk_cache.store_cached_bytes("b", b"12")
        self.assertEqual(disk_cache.cache_size(), (2, 7))

    def test_clear_cache(self) -> None:
        for i in range(3):
            disk_cache.store_cached_bytes(f"u{i}", b"abcd")
        count, freed = disk_cache.clear_cache()
        self.assertEqual(count, 3)
        self.assertEqual(freed, 12)
        self.assertIsNone(disk_cache.load_cached_bytes("u0"))
        # 再次清空：目录仍在但已无文件
        self.assertEqual(disk_cache.clear_cache(), (0, 0))

    def test_clear_cache_missing_dir(self) -> None:
        os.environ["BANGUMI_CACHE_DIR"] = os.path.join(
            self._tmp.name, "not-created"
        )
        self.assertEqual(disk_cache.clear_cache(), (0, 0))

    def test_corrupt_empty_file_treated_as_miss(self) -> None:
        from pathlib import Path

        url = "u-corrupt"
        path = disk_cache.cache_path_for(url)
        disk_cache.cache_root().mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"")  # 模拟损坏 / 半截写入
        self.assertIsNone(disk_cache.load_cached_bytes(url))
        self.assertFalse(path.exists())  # 空文件应被删除


if __name__ == "__main__":
    unittest.main()
