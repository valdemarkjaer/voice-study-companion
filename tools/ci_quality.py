#!/usr/bin/env python3
"""Credential-free quality gates and sanitized CI evidence.

The commands in this module deliberately retain only aggregate results. Test
output remains ephemeral in the job log; reports contain no environment,
command line, source excerpt, or repository path.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
import unicodedata
import unittest
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = "voice-study-companion.ci-gate-report"
REPORT_VERSION = 1
TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".html",
        ".js",
        ".json",
        ".md",
        ".mjs",
        ".py",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
STATIC_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)
GATE_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
CANDIDATE_DIGEST_ALGORITHM = "sha256-length-prefixed-tree-record-fields-v1"
_READ_CHUNK_SIZE = 1024 * 1024


class GateError(RuntimeError):
    """A fail-closed quality-gate error safe to print in public CI."""


@dataclass(frozen=True, slots=True)
class CandidateDigest:
    sha256: str
    regular_file_count: int
    byte_count: int


def _canonical_json(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_report(path: Path, gate: str, status: str, **evidence: object) -> None:
    if GATE_NAME.fullmatch(gate) is None:
        raise GateError("invalid_gate_name")
    document = {
        "schema": REPORT_SCHEMA,
        "schema_version": REPORT_VERSION,
        "gate": gate,
        "status": status,
        "evidence": evidence,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical_json(document), encoding="utf-8", newline="\n")


def _iter_tree_files(
    root: Path,
    *,
    ignored_directories: frozenset[str] = frozenset({".git"}),
) -> Iterable[tuple[Path, PurePosixPath, os.stat_result]]:
    """Yield ordinary files without following links or special nodes."""

    if not root.is_absolute():
        raise GateError("candidate_root_must_be_absolute")
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise GateError("candidate_root_unreadable") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise GateError("candidate_root_not_directory")

    seen_names: set[str] = set()

    def walk(directory: Path, prefix: PurePosixPath) -> Iterable[
        tuple[Path, PurePosixPath, os.stat_result]
    ]:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise GateError("candidate_tree_unreadable") from exc
        for entry in entries:
            relative = prefix / entry.name
            if entry.name in ignored_directories and entry.is_dir(follow_symlinks=False):
                continue
            normalized = unicodedata.normalize("NFC", relative.as_posix()).casefold()
            if normalized in seen_names:
                raise GateError("candidate_path_collision")
            seen_names.add(normalized)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise GateError("candidate_entry_unreadable") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise GateError("candidate_link_rejected")
            if stat.S_ISDIR(metadata.st_mode):
                yield from walk(Path(entry.path), relative)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise GateError("candidate_special_file_rejected")
            if metadata.st_nlink != 1:
                raise GateError("candidate_hardlink_rejected")
            yield Path(entry.path), relative, metadata

    yield from walk(root, PurePosixPath())


def _file_state(metadata: os.stat_result) -> tuple[int, ...]:
    """Return the identity and mutable state covered by one file snapshot."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_file_snapshot(
    path: Path,
    discovered: os.stat_result,
) -> tuple[str, int, str, tuple[int, ...]]:
    """Hash one regular file while rejecting replacement or in-flight mutation."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise GateError("candidate_file_changed") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _file_state(opened) != _file_state(discovered)
        ):
            raise GateError("candidate_file_changed")

        content_digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_SIZE)
            if not chunk:
                break
            content_digest.update(chunk)
            size += len(chunk)

        finished = os.fstat(descriptor)
        if _file_state(finished) != _file_state(opened) or size != opened.st_size:
            raise GateError("candidate_file_changed")
    except OSError as exc:
        raise GateError("candidate_file_changed") from exc
    finally:
        os.close(descriptor)

    try:
        current = path.lstat()
    except OSError as exc:
        raise GateError("candidate_file_changed") from exc
    if _file_state(current) != _file_state(finished):
        raise GateError("candidate_file_changed")
    mode = f"{stat.S_IMODE(finished.st_mode):04o}"
    return content_digest.hexdigest(), size, mode, _file_state(finished)


def candidate_digest(root: Path) -> CandidateDigest:
    """Digest globally path-sorted records using length-prefixed record fields.

    Each regular file contributes, in order, its UTF-8 path, ASCII SHA-256,
    decimal byte size, and ASCII mode. Every individual field is prefixed by
    its unsigned eight-byte big-endian length. This is the same canonical
    algorithm used by the private export manifest generator.
    """

    digest = hashlib.sha256()
    records: list[tuple[str, str, int, str]] = []
    snapshots: dict[str, tuple[int, ...]] = {}
    total = 0
    for path, relative, metadata in _iter_tree_files(root):
        relative_path = relative.as_posix()
        content_digest, size, mode, snapshot = _read_file_snapshot(path, metadata)
        records.append((relative_path, content_digest, size, mode))
        snapshots[relative_path] = snapshot
        total += size
    if not records:
        raise GateError("candidate_tree_empty")

    current_snapshots = {
        relative.as_posix(): _file_state(metadata)
        for _path, relative, metadata in _iter_tree_files(root)
    }
    if current_snapshots != snapshots:
        raise GateError("candidate_tree_changed")

    for relative, content_digest, size, mode in sorted(
        records,
        key=lambda record: record[0],
    ):
        for field in (
            relative.encode("utf-8"),
            content_digest.encode("ascii"),
            str(size).encode("ascii"),
            mode.encode("ascii"),
        ):
            digest.update(len(field).to_bytes(8, "big"))
            digest.update(field)
    return CandidateDigest(digest.hexdigest(), len(records), total)


def _source_files(root: Path) -> list[Path]:
    return [
        path
        for path, _relative, _metadata in _iter_tree_files(
            root,
            ignored_directories=STATIC_IGNORED_DIRECTORY_NAMES,
        )
        if path.suffix.lower() in TEXT_SUFFIXES
    ]


def _check_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GateError("source_text_unreadable") from exc
    if not text.endswith("\n"):
        raise GateError("source_missing_final_newline")
    if "\r" in text:
        raise GateError("source_non_lf_newline")
    if path.suffix.lower() not in {".md", ".txt"} and any(
        line.rstrip(" \t") != line for line in text.splitlines()
    ):
        raise GateError("source_trailing_whitespace")
    return text


def run_static_gate(root: Path, report: Path) -> int:
    files = _source_files(root)
    parsed_python = 0
    parsed_json = 0
    parsed_toml = 0
    checked_javascript = 0
    try:
        for path in files:
            text = _check_text(path)
            suffix = path.suffix.lower()
            if suffix == ".py":
                ast.parse(text, filename=path.name)
                parsed_python += 1
            elif suffix == ".json":
                json.loads(text)
                parsed_json += 1
            elif suffix == ".toml":
                tomllib.loads(text)
                parsed_toml += 1
            elif suffix in {".js", ".mjs"}:
                result = subprocess.run(
                    ["node", "--check", str(path)],
                    cwd=root,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                )
                if result.returncode != 0:
                    raise GateError("javascript_syntax_invalid")
                checked_javascript += 1
    except (GateError, SyntaxError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        _write_report(report, "static-sanity", "FAIL", reason=type(exc).__name__)
        print(f"static sanity failed: {exc}", file=sys.stderr)
        return 1
    _write_report(
        report,
        "static-sanity",
        "PASS",
        text_files=len(files),
        python_files=parsed_python,
        json_files=parsed_json,
        toml_files=parsed_toml,
        javascript_files=checked_javascript,
    )
    return 0


def discover_unit_suite(root: Path) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    original_path = list(sys.path)
    sys.path[:0] = [str(root), str(root / "src")]
    try:
        return loader.discover(str(root / "tests"), pattern="test_*.py")
    finally:
        sys.path[:] = original_path


def run_unit_gate(root: Path, report: Path, minimum: int) -> int:
    suite = discover_unit_suite(root)
    discovered = suite.countTestCases()
    if discovered < minimum:
        _write_report(
            report,
            "python-unit",
            "FAIL",
            tests_discovered=discovered,
            required_minimum=minimum,
            failures=0,
            errors=0,
            skipped=0,
        )
        print(
            f"unit gate failed: discovered {discovered}; minimum is {minimum}",
            file=sys.stderr,
        )
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    status = "PASS" if result.wasSuccessful() else "FAIL"
    _write_report(
        report,
        "python-unit",
        status,
        tests_discovered=discovered,
        tests_run=result.testsRun,
        required_minimum=minimum,
        failures=len(result.failures),
        errors=len(result.errors),
        skipped=len(result.skipped),
    )
    return 0 if result.wasSuccessful() else 1


def run_command_gate(name: str, report: Path, command: Sequence[str]) -> int:
    if GATE_NAME.fullmatch(name) is None:
        raise GateError("invalid_gate_name")
    if not command:
        raise GateError("missing_gate_command")
    try:
        result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        return_code = result.returncode
    except OSError:
        return_code = 127
    status = "PASS" if return_code == 0 else "FAIL"
    _write_report(report, name, status, exit_code=return_code)
    return return_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run public-safe CI quality gates")
    subparsers = parser.add_subparsers(dest="command", required=True)

    static_parser = subparsers.add_parser("static")
    static_parser.add_argument("--report", type=Path, required=True)

    unit_parser = subparsers.add_parser("unit")
    unit_parser.add_argument("--report", type=Path, required=True)
    unit_parser.add_argument("--minimum", type=int, default=92)

    digest_parser = subparsers.add_parser("digest")
    digest_parser.add_argument("--report", type=Path, required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--name", required=True)
    run_parser.add_argument("--report", type=Path, required=True)
    run_parser.add_argument("gate_command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "static":
        return run_static_gate(PROJECT_ROOT, args.report)
    if args.command == "unit":
        if args.minimum < 1:
            raise GateError("minimum_test_count_invalid")
        return run_unit_gate(PROJECT_ROOT, args.report, args.minimum)
    if args.command == "digest":
        result = candidate_digest(PROJECT_ROOT)
        _write_report(
            args.report,
            "candidate-digest",
            "PASS",
            algorithm=CANDIDATE_DIGEST_ALGORITHM,
            candidate_tree_sha256=result.sha256,
            regular_file_count=result.regular_file_count,
            byte_count=result.byte_count,
        )
        return 0
    if args.command == "run":
        command = list(args.gate_command)
        if command and command[0] == "--":
            command.pop(0)
        return run_command_gate(args.name, args.report, command)
    raise GateError("unknown_gate_command")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as exc:
        raise SystemExit(f"quality gate failed: {exc}") from exc
