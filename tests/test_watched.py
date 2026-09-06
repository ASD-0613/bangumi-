"""“正在看 / 已看完”持久化测试（离线，临时目录隔离）。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from bangumi_query.utils import watched as watched_store


class TestWatchedStore(unittest.TestCase):
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
        self.assertIsNone(watched_store.state_of(33346))

    def test_set_state_and_filters(self) -> None:
        watched_store.set_state(1, "正在看A", "", "watching")
        watched_store.set_state(2, "正在看B", "", "watching")
        watched_store.set_state(3, "已看完C", "", "watched")
        watching = watched_store.load_items("watching")
        watched = watched_store.load_items("watched")
        self.assertEqual([i["id"] for i in watching], [2, 1])
        self.assertEqual([i["id"] for i in watched], [3])
        self.assertEqual(watched_store.state_of(1), "watching")
        self.assertEqual(watched_store.state_of(3), "watched")

    def test_state_change_moves_and_updates(self) -> None:
        watched_store.set_state(1, "旧标题", "", "watching")
        watched_store.set_state(2, "B", "", "watched")
        # 1 从“正在看”改为“已看完”：元数据更新、移到 watched 最前
        watched_store.set_state(1, "新标题", "http://example.com/x.jpg",
                                "watched")
        self.assertEqual(watched_store.state_of(1), "watched")
        watched = watched_store.load_items("watched")
        self.assertEqual([i["id"] for i in watched], [1, 2])
        self.assertEqual(watched[0]["title"], "新标题")
        self.assertEqual(watched[0]["cover"], "http://example.com/x.jpg")
        self.assertEqual(watched_store.load_items("watching"), [])

    def test_invalid_state_defaults_to_watched(self) -> None:
        watched_store.set_state(5, "x", "", "bogus-state")
        self.assertEqual(watched_store.state_of(5), "watched")

    def test_remove(self) -> None:
        watched_store.set_state(1, "a", "", "watching")
        watched_store.set_state(2, "b", "", "watched")
        watched_store.remove(1)
        self.assertIsNone(watched_store.state_of(1))
        self.assertEqual(watched_store.state_of(2), "watched")
        watched_store.remove(999)  # 不存在：静默
        self.assertEqual(len(watched_store.load_items()), 1)

    def test_persistence_roundtrip_v2(self) -> None:
        watched_store.set_state(42, "持久化", "", "watching")
        data = json.loads(watched_store.watched_path().read_text("utf-8"))
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["items"][0]["id"], 42)
        self.assertEqual(data["items"][0]["state"], "watching")

    def test_legacy_v1_items_migrate_to_watched(self) -> None:
        path = watched_store.watched_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 1, "items": [
                {"id": 100, "title": "旧已看完", "cover": ""},
            ]}),
            encoding="utf-8",
        )
        items = watched_store.load_items()
        self.assertEqual(items[0]["state"], "watched")
        # 迁移后的条目可正常改状态
        watched_store.set_state(100, "旧已看完", "", "watching")
        self.assertEqual(watched_store.state_of(100), "watching")

    def test_corrupt_file_tolerated(self) -> None:
        path = watched_store.watched_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json{{", encoding="utf-8")
        self.assertEqual(watched_store.load_items(), [])
        watched_store.set_state(7, "恢复", "", "watched")
        self.assertTrue(watched_store.state_of(7))

    def test_bad_entries_filtered(self) -> None:
        path = watched_store.watched_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 2, "items": [
                {"id": 1, "title": "ok", "cover": "", "state": "watching"},
                {"id": "not-int", "title": "坏条目"},
                {"title": "缺 id"},
                "不是字典",
            ]}),
            encoding="utf-8",
        )
        items = watched_store.load_items()
        self.assertEqual([i["id"] for i in items], [1])

    def test_clear(self) -> None:
        watched_store.set_state(1, "a", "", "watching")
        watched_store.clear()
        self.assertEqual(watched_store.load_items(), [])


if __name__ == "__main__":
    unittest.main()
