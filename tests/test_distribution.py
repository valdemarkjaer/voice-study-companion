from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from tools.container_demo_smoke import SmokeError, _validate_runtime_distribution
from voice_study_companion.server import REQUIRED_WEB_ASSETS, resolve_web_root


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_web_tree(root: Path, *, omit: str | None = None) -> None:
    for relative in REQUIRED_WEB_ASSETS:
        if relative == omit:
            continue
        target = root.joinpath(*Path(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"fixture for {relative}\n", encoding="utf-8")


class DistributionTests(unittest.TestCase):
    def test_project_declares_console_command_and_recursive_web_include(self) -> None:
        document = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

        self.assertEqual(
            document["project"]["scripts"]["voice-study-companion"],
            "voice_study_companion.server:main",
        )
        self.assertEqual(
            document["tool"]["hatch"]["build"]["targets"]["wheel"][
                "force-include"
            ],
            {"web": "voice_study_companion/web"},
        )

    def test_source_layout_is_the_only_external_web_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            module = project / "src" / "voice_study_companion" / "server.py"
            module.parent.mkdir(parents=True)
            module.write_text("# fixture\n", encoding="utf-8")
            _write_web_tree(project / "web")

            self.assertEqual(resolve_web_root(module), project / "web")

            unrelated = Path(temporary) / "other" / "voice_study_companion" / "server.py"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("# fixture\n", encoding="utf-8")
            _write_web_tree(Path(temporary) / "web")
            with self.assertRaisesRegex(RuntimeError, "required_web_assets"):
                resolve_web_root(unrelated)

    def test_complete_package_local_web_tree_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "site-packages" / "voice_study_companion"
            module = package / "server.py"
            package.mkdir(parents=True)
            module.write_text("# fixture\n", encoding="utf-8")
            _write_web_tree(package / "web")

            self.assertEqual(resolve_web_root(module), package / "web")

    def test_partial_package_tree_fails_closed_without_source_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            package = project / "src" / "voice_study_companion"
            module = package / "server.py"
            package.mkdir(parents=True)
            module.write_text("# fixture\n", encoding="utf-8")
            _write_web_tree(project / "web")
            _write_web_tree(
                package / "web",
                omit="audio/playback-handshake.mjs",
            )

            with self.assertRaisesRegex(RuntimeError, "required_web_assets"):
                resolve_web_root(module)

    def test_installed_smoke_rejects_missing_nested_audio_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            purelib = Path(temporary) / "venv" / "site-packages"
            package = purelib / "voice_study_companion"
            module = package / "server.py"
            package.mkdir(parents=True)
            module.write_text("# fixture\n", encoding="utf-8")
            _write_web_tree(
                package / "web",
                omit="audio/pcm-ring-buffer.mjs",
            )

            with self.assertRaisesRegex(SmokeError, "required_web_assets"):
                _validate_runtime_distribution(
                    require_installed=True,
                    module_file=module,
                    purelib=purelib,
                )

    def test_installed_smoke_accepts_complete_package_and_proves_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            purelib = Path(temporary) / "venv" / "site-packages"
            package = purelib / "voice_study_companion"
            module = package / "server.py"
            package.mkdir(parents=True)
            module.write_text("# fixture\n", encoding="utf-8")
            _write_web_tree(package / "web")

            self.assertEqual(
                _validate_runtime_distribution(
                    require_installed=True,
                    module_file=module,
                    purelib=purelib,
                ),
                "installed-wheel",
            )

    def test_installed_smoke_rejects_source_checkout_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            module = project / "src" / "voice_study_companion" / "server.py"
            module.parent.mkdir(parents=True)
            module.write_text("# fixture\n", encoding="utf-8")
            _write_web_tree(project / "web")

            with self.assertRaisesRegex(SmokeError, "installed_environment"):
                _validate_runtime_distribution(
                    require_installed=True,
                    module_file=module,
                    purelib=root / "venv" / "site-packages",
                )

    def test_ci_installed_smoke_unsets_source_path_and_requires_install(self) -> None:
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "quality-gates.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("installed-wheel-venv", workflow)
        self.assertIn("--no-index --no-deps", workflow)
        self.assertIn("env -u PYTHONPATH", workflow)
        self.assertIn("--require-installed", workflow)
        self.assertIn("installed-wheel-smoke.json", workflow)

    def test_container_smoke_builds_and_installs_one_wheel_without_source_mount(
        self,
    ) -> None:
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "quality-gates.yml"
        ).read_text(encoding="utf-8")
        container_job = workflow.split("  container-smoke:\n", 1)[1].split(
            "  candidate-evidence:\n", 1
        )[0]

        self.assertEqual(container_job.count("python -m pip wheel"), 1)
        self.assertIn("--network none", container_job)
        self.assertIn("container-wheels:/wheels:ro", container_job)
        self.assertIn("container_demo_smoke.py:/runner.py:ro", container_job)
        self.assertIn("python -m venv /runtime/venv", container_job)
        self.assertIn("/runtime/venv/bin/python -I /runner.py", container_job)
        self.assertIn("--require-installed", container_job)
        self.assertIn("env -u PYTHONPATH", container_job)
        self.assertNotIn("--env PYTHONPATH=src", container_job)
        self.assertNotIn("$GITHUB_WORKSPACE:/project", container_job)


if __name__ == "__main__":
    unittest.main()
