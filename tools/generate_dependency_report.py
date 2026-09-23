#!/usr/bin/env python3
"""Generate a deterministic, fail-closed dependency/license report."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


REPORT_VERSION = 1
EVIDENCE_DATE = "2026-09-22"
PYTHON_POLICY = {
    "hatchling": {
        "version": "1.27.0",
        "license": "MIT",
        "use": "build-only",
        "notice": "THIRD_PARTY_NOTICES.md#hatchling-1270",
    },
    "packaging": {
        "version": "26.3",
        "license": "Apache-2.0 OR BSD-2-Clause",
        "selected_license": "Apache-2.0",
        "use": "build-only",
        "notice": "THIRD_PARTY_NOTICES.md#packaging-263",
    },
    "pathspec": {
        "version": "1.1.1",
        "license": "MPL-2.0",
        "use": "build-only",
        "notice": "licenses/MPL-2.0.txt",
    },
    "pluggy": {
        "version": "1.6.0",
        "license": "MIT",
        "use": "build-only",
        "notice": "THIRD_PARTY_NOTICES.md#pluggy-160",
    },
    "trove-classifiers": {
        "version": "2026.9.21.13",
        "license": "Apache-2.0",
        "use": "build-only",
        "notice": "LICENSE",
    },
}
JAVASCRIPT_POLICY = {
    "playwright": {
        "version": "1.63.0",
        "license": "Apache-2.0",
        "use": "development-only",
        "notice": "THIRD_PARTY_NOTICES.md#playwright-1630-and-playwright-core-1630",
    },
    "playwright-core": {
        "version": "1.63.0",
        "license": "Apache-2.0",
        "use": "development-only",
        "notice": "THIRD_PARTY_NOTICES.md#playwright-1630-and-playwright-core-1630",
    },
}
_PYTHON_PACKAGE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s\\]+)\s*\\$"
)
_PYTHON_HASH = re.compile(r"^--hash=sha256:(?P<digest>[0-9a-f]{64})$")


class ReportError(RuntimeError):
    """Raised when a lock cannot be reconciled with the reviewed policy."""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _python_packages(lock_path: Path) -> list[dict[str, str]]:
    meaningful = [
        line.strip()
        for line in lock_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(meaningful) % 2:
        raise ReportError("python_lock_shape_invalid")
    packages: list[dict[str, str]] = []
    for index in range(0, len(meaningful), 2):
        package_match = _PYTHON_PACKAGE.fullmatch(meaningful[index])
        hash_match = _PYTHON_HASH.fullmatch(meaningful[index + 1])
        if package_match is None or hash_match is None:
            raise ReportError("python_lock_shape_invalid")
        name = _normalize_name(package_match.group("name"))
        if any(item["name"] == name for item in packages):
            raise ReportError("python_lock_duplicate")
        packages.append(
            {
                "name": name,
                "version": package_match.group("version"),
                "sha256": hash_match.group("digest"),
            }
        )
    return sorted(packages, key=lambda item: item["name"])


def _javascript_packages(lock_path: Path) -> list[dict[str, str]]:
    document = json.loads(lock_path.read_text(encoding="utf-8"))
    if document.get("lockfileVersion") != 3 or not isinstance(
        document.get("packages"), dict
    ):
        raise ReportError("javascript_lock_shape_invalid")
    packages: list[dict[str, str]] = []
    for key, value in document["packages"].items():
        if key == "":
            continue
        if not key.startswith("node_modules/") or not isinstance(value, dict):
            raise ReportError("javascript_lock_shape_invalid")
        name = key.removeprefix("node_modules/")
        version = value.get("version")
        integrity = value.get("integrity")
        license_id = value.get("license")
        if not all(isinstance(item, str) and item for item in (version, integrity, license_id)):
            raise ReportError("javascript_lock_metadata_incomplete")
        packages.append(
            {
                "name": name,
                "version": version,
                "integrity": integrity,
                "lock_license": license_id,
            }
        )
    return sorted(packages, key=lambda item: item["name"])


def _reconcile(
    packages: list[dict[str, str]],
    policy: dict[str, dict[str, str]],
    *,
    ecosystem: str,
) -> list[dict[str, str]]:
    observed = {item["name"] for item in packages}
    if observed != set(policy):
        raise ReportError(f"{ecosystem}_dependency_set_unreviewed")
    reconciled: list[dict[str, str]] = []
    for package in packages:
        approved = policy[package["name"]]
        if package["version"] != approved["version"]:
            raise ReportError(f"{ecosystem}_dependency_version_unreviewed")
        if (
            ecosystem == "javascript"
            and package["lock_license"] != approved["license"]
        ):
            raise ReportError("javascript_dependency_license_unreviewed")
        reconciled.append({**package, **approved, "review_status": "APPROVED"})
    return reconciled


def build_report(project_root: Path) -> dict[str, Any]:
    python_lock = project_root / "requirements-build.lock"
    javascript_lock = project_root / "package-lock.json"
    python = _reconcile(
        _python_packages(python_lock), PYTHON_POLICY, ecosystem="python"
    )
    javascript = _reconcile(
        _javascript_packages(javascript_lock),
        JAVASCRIPT_POLICY,
        ecosystem="javascript",
    )
    return {
        "schema": "voice-study-companion.dependency-license-report",
        "schema_version": REPORT_VERSION,
        "evidence_date": EVIDENCE_DATE,
        "generator": "tools/generate_dependency_report.py@1",
        "status": "COMPLETE_FOR_LOCKED_GRAPH",
        "project_runtime_dependencies": [],
        "locks": {
            "javascript": {
                "path": "package-lock.json",
                "sha256": _digest(javascript_lock),
            },
            "python_build": {
                "path": "requirements-build.lock",
                "sha256": _digest(python_lock),
            },
        },
        "dependencies": {
            "javascript": javascript,
            "python_build": python,
        },
        "assets": {
            "fonts": "NONE_DISTRIBUTED",
            "icons": "CSS_AND_TEXT_ONLY_NO_ICON_FILES",
            "models": "NONE_DISTRIBUTED",
            "recorded_audio": "NONE_DISTRIBUTED",
            "opaque_images": "NONE_DISTRIBUTED",
            "synthetic_media": "GENERATED_FROM_PROJECT_SOURCE_RECIPES",
        },
        "excluded_artifacts": [
            "browser binaries",
            "node_modules",
            "Python wheels",
            "virtual environments",
        ],
    }


def _canonical_json(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the locked dependency/license report"
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    output = args.output or project_root / "reports/dependency-licenses.json"
    content = _canonical_json(build_report(project_root))
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != content:
            raise ReportError("generated_report_is_stale")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, ReportError) as exc:
        raise SystemExit(f"dependency report failed: {exc}") from exc
