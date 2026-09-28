import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mirofish_runtime


class MiroFishRuntimeTests(unittest.TestCase):
    def test_literal_config_only_loads_whitelist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.mirofish"
            path.write_text(
                "LLM_API_KEY=llm-secret\nZEP_API_KEY=zep-secret\n"
                "TELEGRAM_BOT_TOKEN=must-not-load\nOPENAI_API_KEY=must-not-load\n",
                encoding="utf-8",
            )
            values = mirofish_runtime.load_settings(path)
        self.assertEqual(values, {"LLM_API_KEY": "llm-secret", "ZEP_API_KEY": "zep-secret"})

    def test_clean_environment_does_not_inherit_telegram_or_unlisted_secrets(self):
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "telegram-secret",
            "OPENAI_API_KEY": "openai-secret",
            "RANDOM_SECRET": "random-secret",
        }, clear=False):
            environment = mirofish_runtime._clean_environment({
                "LLM_API_KEY": "allowed-secret", "ZEP_API_KEY": "zep-secret",
            })
        self.assertEqual(environment["LLM_API_KEY"], "allowed-secret")
        self.assertNotIn("TELEGRAM_BOT_TOKEN", environment)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("RANDOM_SECRET", environment)
        self.assertNotIn("HOME", environment)
        self.assertEqual(environment["FLASK_HOST"], "127.0.0.1")
        self.assertEqual(environment["FLASK_DEBUG"], "False")

    def test_status_never_contains_secret_values(self):
        values = {key: f"secret-{key}" for key in mirofish_runtime.SETTINGS}
        with patch("mirofish_runtime.load_settings", return_value=values), \
             patch("mirofish_runtime._service_running", return_value=(False, None)):
            status = mirofish_runtime.runtime_status()
        serialized = json.dumps(status)
        for value in values.values():
            self.assertNotIn(value, serialized)
        self.assertTrue(status["configured"])

    def test_start_refuses_missing_configuration_without_spawning(self):
        state = {"installed": True}
        with patch("mirofish_runtime.runtime_status", return_value=state), \
             patch("mirofish_runtime.load_settings", return_value={}), \
             patch("mirofish_runtime._spawn") as spawn:
            with self.assertRaisesRegex(RuntimeError, "configuration is incomplete"):
                mirofish_runtime.start()
        spawn.assert_not_called()

    def test_vendor_manifest_matches_pinned_source(self):
        manifest = json.loads((mirofish_runtime.INTEGRATION / "SOURCE.json").read_text())
        self.assertEqual(manifest["commit"], mirofish_runtime.COMMIT)
        self.assertTrue((mirofish_runtime.UPSTREAM / "LICENSE").is_file())
        self.assertTrue((mirofish_runtime.UPSTREAM / "backend" / "app").is_dir())
        self.assertTrue((mirofish_runtime.UPSTREAM / "frontend" / "src").is_dir())
        self.assertTrue((mirofish_runtime.UPSTREAM / "backend" / "scripts" /
                         "run_parallel_simulation.py").is_file())
        self.assertFalse((mirofish_runtime.UPSTREAM / ".git").exists())
        self.assertFalse(any(path.name in {"__pycache__", "logs", "uploads", "node_modules"}
                             for path in mirofish_runtime.UPSTREAM.rglob("*")))

        bundled = mirofish_runtime.source_files()
        self.assertIn((mirofish_runtime.UPSTREAM / "LICENSE").resolve(), bundled)
        self.assertTrue(all(path.is_file() and not path.is_symlink() for path in bundled))
        self.assertFalse(any(".env" in path.relative_to(mirofish_runtime.UPSTREAM).parts
                             for path in bundled))

    def test_legacy_upload_state_moves_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "worktree" / "backend" / "uploads"
            target = root / "persistent"
            old.mkdir(parents=True)
            (old / "projects").mkdir()
            (old / "projects" / "state.json").write_text("preserved")
            with patch("mirofish_runtime.UPLOADS", target):
                mirofish_runtime._preserve_existing_uploads(old)
            self.assertEqual((target / "projects" / "state.json").read_text(), "preserved")
            self.assertFalse(old.exists())

    def test_upload_migration_never_overwrites_existing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old"
            target = root / "persistent"
            old.mkdir()
            target.mkdir()
            (old / "projects").mkdir()
            (target / "projects").mkdir()
            with patch("mirofish_runtime.UPLOADS", target):
                with self.assertRaisesRegex(RuntimeError, "cannot migrate"):
                    mirofish_runtime._preserve_existing_uploads(old)
            self.assertTrue((old / "projects").is_dir())
            self.assertTrue((target / "projects").is_dir())

    def test_only_owned_runner_with_runtime_config_is_detected(self):
        valid = (
            f"101 101 {mirofish_runtime.VENV}/bin/python "
            f"{mirofish_runtime.WORKTREE}/backend/scripts/run_parallel_simulation.py "
            f"--config {mirofish_runtime.UPLOADS}/simulations/sim_1/config.json"
        )
        foreign = "202 202 /usr/bin/python /tmp/run_parallel_simulation.py --config /tmp/config.json"
        completed = subprocess.CompletedProcess([], 0, stdout=valid + "\n" + foreign + "\n")
        with patch("mirofish_runtime.subprocess.run", return_value=completed):
            found = mirofish_runtime._owned_simulation_processes()
        self.assertEqual(found, [{"pid": 101, "pgid": 101}])

    def test_stop_terminates_simulations_before_backend(self):
        order = []
        with patch("mirofish_runtime._request_simulation_stops",
                   side_effect=lambda: order.append("graceful")), \
             patch("mirofish_runtime._terminate_owned_simulations",
                   side_effect=lambda: order.append("runners")), \
             patch("mirofish_runtime._read_pid", return_value=None), \
             patch("mirofish_runtime.runtime_status", return_value={}):
            mirofish_runtime.stop()
        self.assertEqual(order, ["graceful", "runners"])


if __name__ == "__main__":
    unittest.main()
