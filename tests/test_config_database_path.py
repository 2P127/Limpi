from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.core.config import resolve_database_path, runtime_base_dir


class ResolveDatabasePathTests(unittest.TestCase):
    def test_default_relative_path_uses_runtime_base_dir(self) -> None:
        base = runtime_base_dir()
        resolved = resolve_database_path(None)
        self.assertEqual(resolved, (base / "limpi.sqlite3").resolve())

    def test_relative_env_path_ignores_process_cwd(self) -> None:
        base = runtime_base_dir()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {}, clear=False):
                previous = Path.cwd()
                try:
                    os.chdir(tmp)
                    resolved = resolve_database_path("data/limpi.sqlite3")
                finally:
                    os.chdir(previous)
        self.assertEqual(resolved, (base / "data" / "limpi.sqlite3").resolve())
        self.assertFalse(str(resolved).startswith(str(Path(tmp).resolve())))

    def test_absolute_path_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            absolute = Path(tmp) / "custom.sqlite3"
            resolved = resolve_database_path(str(absolute))
        self.assertEqual(resolved, absolute.resolve())


if __name__ == "__main__":
    unittest.main()
