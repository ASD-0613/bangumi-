"""界面偏好持久化（settings.json）测试（离线，临时目录隔离）。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from bangumi_query.utils import settings as settings_store


class TestSettingsStore(unittest.TestCase):
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
        self.assertEqual(settings_store.load(), {})

    def test_update_and_load_roundtrip(self) -> None:
        settings_store.update(search_widths=[110, 320, 60, 60, 60, 60, 60, 60])
        data = settings_store.load()
        self.assertEqual(data["search_widths"][1], 320)
        # 文件结构可读
        raw = json.loads(settings_store.settings_path().read_text("utf-8"))
        self.assertEqual(raw["search_widths"][0], 110)

    def test_update_merges_and_keeps_unknown_keys(self) -> None:
        settings_store.update(a=1)
        settings_store.update(b=2)
        data = settings_store.load()
        self.assertEqual(data, {"a": 1, "b": 2})

    def test_corrupt_file_tolerated(self) -> None:
        path = settings_store.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{{{{", encoding="utf-8")
        self.assertEqual(settings_store.load(), {})
        settings_store.update(x=1)  # 损坏文件上仍可正常写入
        self.assertEqual(settings_store.load()["x"], 1)


if __name__ == "__main__":
    unittest.main()
