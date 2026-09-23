from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.generate_dependency_report import (
    JAVASCRIPT_POLICY,
    PYTHON_POLICY,
    ReportError,
    _javascript_packages,
    _python_packages,
    _reconcile,
    build_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DependencyReportTests(unittest.TestCase):
    def test_checked_in_report_matches_locked_graph(self) -> None:
        expected = build_report(PROJECT_ROOT)
        actual = json.loads(
            (PROJECT_ROOT / "reports/dependency-licenses.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(actual, expected)
        self.assertEqual(expected["status"], "COMPLETE_FOR_LOCKED_GRAPH")
        self.assertEqual(expected["project_runtime_dependencies"], [])

    def test_unknown_python_dependency_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "requirements.lock"
            path.write_text(
                "unknown-tool==1.0 \\\n"
                "    --hash=sha256:" + ("a" * 64) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ReportError, "python_dependency_set_unreviewed"
            ):
                _reconcile(
                    _python_packages(path), PYTHON_POLICY, ecosystem="python"
                )

    def test_unknown_javascript_license_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "package-lock.json"
            document = {
                "lockfileVersion": 3,
                "packages": {
                    "": {},
                    "node_modules/playwright": {
                        "version": "1.63.0",
                        "integrity": "sha512-placeholder",
                        "license": "UNKNOWN",
                    },
                    "node_modules/playwright-core": {
                        "version": "1.63.0",
                        "integrity": "sha512-placeholder",
                        "license": "Apache-2.0",
                    },
                },
            }
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaisesRegex(
                ReportError, "javascript_dependency_license_unreviewed"
            ):
                _reconcile(
                    _javascript_packages(path),
                    JAVASCRIPT_POLICY,
                    ecosystem="javascript",
                )


if __name__ == "__main__":
    unittest.main()
