#!/usr/bin/env python3
"""
File registry checker.

Static-only repo lifecycle enforcement:
- scans .py files only
- does not import repo modules
- does not execute repo code
- does not touch databases
- compares discovered repo-relative .py paths against file_registry.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


VALID_STATES = {"ACTIVE", "PENDING", "ARCHIVE", "DOCS"}

REQUIRED_COLUMNS = [
    "File name",
    "File path",
    "State",
    "Purpose",
    "Public callables",
    "Wired to",
    "Last reviewed date",
    "Notes",
]

IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}


def normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").lstrip("./")


def find_repo_root(start: Path) -> Path:
    current = start.resolve()

    for candidate in [current, *current.parents]:
        if (candidate / "file_registry.csv").exists():
            return candidate
        if (candidate / ".git").exists():
            return candidate

    return current


def is_ignored(path: Path, repo_root: Path) -> bool:
    try:
        rel_parts = path.resolve().relative_to(repo_root.resolve()).parts
    except ValueError:
        rel_parts = path.parts

    return any(part in IGNORE_DIRS for part in rel_parts)


def discover_python_files(repo_root: Path) -> list[str]:
    discovered: list[str] = []

    for path in repo_root.rglob("*.py"):
        if not path.is_file():
            continue
        if is_ignored(path, repo_root):
            continue

        rel_path = path.resolve().relative_to(repo_root.resolve()).as_posix()
        discovered.append(rel_path)

    return sorted(discovered, key=str.lower)


def load_registry(registry_path: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    errors: list[str] = []

    if not registry_path.exists():
        return {}, [f"Missing registry file: {registry_path}"]

    rows_by_path: dict[str, dict[str, str]] = {}

    with registry_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        fieldnames = reader.fieldnames or []
        missing_columns = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        if missing_columns:
            errors.append(f"file_registry.csv is missing required columns: {', '.join(missing_columns)}")
            return {}, errors

        for line_number, row in enumerate(reader, start=2):
            raw_path = row.get("File path", "")
            file_path = normalize_path(raw_path)

            if not file_path:
                errors.append(f"Line {line_number}: missing File path")
                continue

            if file_path in rows_by_path:
                errors.append(f"Line {line_number}: duplicate File path: {file_path}")
                continue

            state = (row.get("State") or "").strip().upper()
            row["State"] = state

            if state not in VALID_STATES:
                errors.append(
                    f"Line {line_number}: invalid State '{state}' for {file_path}. "
                    f"Valid states: {', '.join(sorted(VALID_STATES))}"
                )

            rows_by_path[file_path] = row

    return rows_by_path, errors


def print_results(
    discovered_paths: list[str],
    registry_rows: dict[str, dict[str, str]],
    registry_errors: list[str],
) -> int:
    missing = [path for path in discovered_paths if path not in registry_rows]

    print("FILE REGISTRY CHECK")
    print("-------------------")
    print(f"Python files discovered: {len(discovered_paths)}")
    print(f"Registry rows loaded: {len(registry_rows)}")
    print(f"Missing registry rows: {len(missing)}")
    print(f"Registry errors: {len(registry_errors)}")
    print("")

    if missing:
        print("MISSING_REGISTRY_ROW")
        for path in missing:
            print(f"  - {path}")
        print("")

    if registry_errors:
        print("REGISTRY_ERRORS")
        for error in registry_errors:
            print(f"  - {error}")
        print("")

    if not missing and not registry_errors:
        print("PASS: every discovered .py file has a valid registry row.")
    else:
        print("FAIL: registry enforcement found issues.")

    return len(missing) + len(registry_errors)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check file_registry.csv against repo .py files.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repo root. Default: current working directory.",
    )
    parser.add_argument(
        "--registry",
        default="file_registry.csv",
        help="Registry file path relative to repo root. Default: file_registry.csv.",
    )
    parser.add_argument(
        "--enforce-registry",
        action="store_true",
        help="Exit 1 if missing rows or invalid states are found.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    repo_root = find_repo_root(Path(args.repo_root))
    registry_path = repo_root / args.registry

    discovered_paths = discover_python_files(repo_root)
    registry_rows, registry_errors = load_registry(registry_path)

    issue_count = print_results(discovered_paths, registry_rows, registry_errors)

    if args.enforce_registry and issue_count:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
