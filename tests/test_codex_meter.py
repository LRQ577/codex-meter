"""Core checks that never read the user's Codex account or todo data."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import codex_meter as meter


class MeterDataTests(unittest.TestCase):
    def test_gpu_csv_and_missing_sensor(self):
        csv = "0, NVIDIA Test GPU, 35, 1024, 8192, 48, 85.5\n"
        with patch.object(meter, "_run", return_value=csv):
            self.assertEqual(meter.read_gpus()[0]["memory_total"], 8192)
            self.assertEqual(meter.read_gpus()[0]["name"], "Test GPU")
        with patch.object(meter, "_run", side_effect=FileNotFoundError("nvidia-smi")):
            with self.assertRaises(FileNotFoundError):
                meter.read_gpus()

    def test_local_database_is_optional_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "codex-dev.db"
            with patch.object(meter, "CODEX_DATABASE", database):
                self.assertEqual(meter.read_automation_stats(None), {"total": 0, "active": 0})
                self.assertEqual(meter.read_codex_todo_sources(None), [])
                with sqlite3.connect(database) as connection:
                    connection.execute("CREATE TABLE automations (status TEXT, account_id TEXT)")
                    connection.execute("CREATE TABLE inbox_items (id TEXT, title TEXT, description TEXT, read_at TEXT)")
                    connection.executemany("INSERT INTO automations VALUES (?, ?)", [
                        ("ACTIVE", "current"), ("PAUSED", None), ("ACTIVE", "other")
                    ])
                    connection.execute("INSERT INTO inbox_items VALUES ('item1', 'Review', NULL, NULL)")
                self.assertEqual(meter.read_automation_stats("current"), {"total": 2, "active": 1})
                self.assertEqual(meter.read_codex_todo_sources("current")[0]["source_id"], "item1")

    def test_todos_stay_in_private_local_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "todos.json"
            with patch.object(meter, "TODO_FILE", path):
                meter.save_todos([{"text": "Private local item", "done": False}])
                self.assertEqual(meter.load_todos()[0]["text"], "Private local item")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
