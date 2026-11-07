import os
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from src.backend.codex_config import sync_codex_config
from src.backend.main import ROOT, app, lifespan


class ConfigSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / "source"
        self.home = Path(temporary.name) / "home"
        shutil.copytree(ROOT / "codex", self.source)

    def test_installs_into_a_new_private_home(self):
        sync_codex_config(self.source, self.home)
        for name in ("config.toml", "AGENTS.md"):
            self.assertEqual((self.home / name).read_bytes(), (self.source / name).read_bytes())
        self.assertTrue((self.home / "skills").is_dir())
        self.assertEqual(self.home.stat().st_mode & 0o777, 0o700)

    def test_restart_updates_files_and_skills_without_touching_runtime_data(self):
        old_skill = self.source / "skills" / "old"
        old_skill.mkdir()
        (old_skill / "SKILL.md").write_text("Old skill")
        sync_codex_config(self.source, self.home)

        runtime = {
            "auth.json": b'{"test": "fake credential"}',
            "sessions/2026/rollout.jsonl": b'{"message": "saved conversation"}\n',
            "history.jsonl": b'{"message": "history"}\n',
            "state.sqlite-wal": b"existing WAL data",
            "state.sqlite-shm": b"existing shared memory",
        }
        with sqlite3.connect(self.home / "state.sqlite") as database:
            database.execute("CREATE TABLE threads (id TEXT)")
            database.execute("INSERT INTO threads VALUES ('saved-thread')")
        for name, contents in runtime.items():
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        runtime["state.sqlite"] = (self.home / "state.sqlite").read_bytes()

        (self.source / "config.toml").write_text('sandbox_mode = "read-only"\n')
        (self.source / "AGENTS.md").write_text("Updated agent instructions\n")
        shutil.rmtree(old_skill)
        new_skill = self.source / "skills" / "new"
        new_skill.mkdir()
        (new_skill / "SKILL.md").write_text("New skill")

        for _ in range(2):
            sync_codex_config(self.source, self.home)
        self.assertEqual((self.home / "config.toml").read_text(), 'sandbox_mode = "read-only"\n')
        self.assertEqual((self.home / "AGENTS.md").read_text(), "Updated agent instructions\n")
        self.assertFalse((self.home / "skills" / "old").exists())
        self.assertEqual((self.home / "skills" / "new" / "SKILL.md").read_text(), "New skill")
        for name, contents in runtime.items():
            self.assertEqual((self.home / name).read_bytes(), contents, name)


class StartupConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_syncs_configured_home_before_starting_codex(self):
        with TemporaryDirectory() as temporary:
            home = Path(temporary).resolve() / "app-home"

            async def check_installed(api_key, configured_home, workspace):
                self.assertEqual(configured_home, home)
                for name in ("config.toml", "AGENTS.md"):
                    self.assertEqual((home / name).read_bytes(), (ROOT / "codex" / name).read_bytes())
                self.assertTrue((home / "skills").is_dir())

            with patch.dict(os.environ, {
                "OPENAI_API_KEY": "test-api-key",
                "PODCAST_CODEX_HOME": str(home),
                "CODEX_HOME": str(Path(temporary) / "personal-home"),
            }), patch("src.backend.main.load_dotenv"), patch(
                "src.backend.main.ChatHarness"
            ) as harness_type:
                client = harness_type.return_value.client
                client.start = AsyncMock(side_effect=check_installed)
                client.close = AsyncMock()
                async with lifespan(app):
                    client.start.assert_awaited_once()
                client.close.assert_awaited_once()
                self.assertFalse((Path(temporary) / "personal-home").exists())


if __name__ == "__main__":
    unittest.main()
