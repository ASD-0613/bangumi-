"""“已看完”本地持久化测试（离线，临时目录隔离）。

注意：watched.json 存放在缓存目录的**上一级**，因此测试环境把
``BANGUMI_CACHE_DIR`` 指到 ``<临时目录>/cache``，使 watched.json
落在 ``<临时目录>/watched.json``，两者都被隔离在临时目录内。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from bangumi_query.utils import watched as watched_store


class TestWatchedStore(unittest.TestCase):
    """add / remove / contains / load_items 与文件读写。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_dir = os.environ.get("BANGUMI_CACHE_DIR")
        os.environ["BANGUMI_CACHE_DIR"] = os.path.join(self._tmp.name, "cache")

    def tearDown(self) -> None:
        if self._old_dir is None:
            os.environ.pop("BANGUMI_CACHE_DIR", None)
        else:
            os.environ["BANGUMI_CACHE_DIR"] = self._old_dir
        self._tmp.cleanup()

    def test_empty_by_default(self) -> None:
        self.assertEqual(watched_store.load_items(), [])
        self.assertFalse(watched_store.contains(33346))

    def test_add_and_contains(self) -> None:
        watched_store.add(33346, "刀剑神域", "http://example.com/a.jpg")
        self.assertTrue(watched_store.contains(33346))
        items = watched_store.load_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], 33346)
        self.assertEqual(items[0]["title"], "刀剑神域")
        self.assertEqual(items[0]["cover"], "http://example.com/a.jpg")

    def test_add_newest_first_and_dedupe(self) -> None:
        watched_store.add(1, "最早", "")
        watched_store.add(2, "次之", "")
        watched_store.add(3, "最新", "")
        self.assertEqual([it["id"] for it in watched_store.load_items()],
                         [3, 2, 1])
        # 重复添加：置顶更新且不产生重复
        watched_store.add(1, "最早（更新）", "http://example.com/x.jpg")
        ids = [it["id"] for it in watched_store.load_items()]
        self.assertEqual(ids, [1, 3, 2])
        self.assertEqual(watched_store.load_items()[0]["cover"],
                         "http://example.com/x.jpg")

    def test_remove(self) -> None:
        watched_store.add(1, "a", "")
        watched_store.add(2, "b", "")
        watched_store.remove(1)
        self.assertFalse(watched_store.contains(1))
        self.assertTrue(watched_store.contains(2))
        watched_store.remove(999)  # 不存在：静默
        self.assertEqual(len(watched_store.load_items()), 1)

    def test_persistence_roundtrip(self) -> None:
        watched_store.add(42, "持久化", "")
        # 重新读取文件（模拟重启）
        data = json.loads(watched_store.watched_path().read_text("utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["items"][0]["id"], 42)

    def test_corrupt_file_tolerated(self) -> None:
        path = watched_store.watched_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json{{", encoding="utf-8")
        self.assertEqual(watched_store.load_items(), [])
        # 损坏文件上的 add 仍可恢复正常工作
        watched_store.add(7, "恢复", "")
        self.assertTrue(watched_store.contains(7))

    def test_bad_entries_filtered(self) -> None:
        path = watched_store.watched_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 1, "items": [
                {"id": 1, "title": "ok", "cover": ""},
                {"id": "not-int", "title": "坏条目"},
                {"title": "缺 id"},
                "不是字典",
            ]}),
            encoding="utf-8",
        )
        items = watched_store.load_items()
        self.assertEqual([it["id"] for it in items], [1])


if __name__ == "__main__":
    unittest.main()
