#!/usr/bin/env python3
"""Reject likely private data, credentials, and generated binaries in a release tree."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}
PRIVATE_PATHS = (
    re.compile(r"(^|/)\.env(?:\..*)?$", re.I),
    re.compile(
        r"(^|/)(?:config|scenes|devices|credentials|secrets|tokens|keychain|brightness-levels|fade-status)"
        r"(?:[-_.].*)?\.(?:json|jsonl|ya?ml|toml|plist|txt|csv)$",
        re.I,
    ),
    re.compile(r"(^|/).*(?:readback|test-results|research|progress|session|credentials?).*", re.I),
    re.compile(r"\.(?:sqlite3?|db|pem|p12|pfx|key)$", re.I),
)
SECRET_TEXT = (
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key material"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"(?i)['\"]?\b(?:access[_ -]?token|refresh[_ -]?token|api[_ -]?key)['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{20,}"), "credential-like value"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}"), "bearer token"),
    (re.compile(r"(?i)\bpassword\s*[:=]\s*['\"][^'\"\r\n]{8,}['\"]"), "password-like value"),
)


def audit(root: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in EXCLUDED_DIRS for part in Path(rel).parts):
            continue
        if any(pattern.search(rel) for pattern in PRIVATE_PATHS):
            findings.append(f"{rel}: private-data filename")
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            findings.append(f"{rel}: cannot read ({exc})")
            continue
        if b"\0" in data:
            findings.append(f"{rel}: binary file")
            continue
        text = data.decode("utf-8", errors="replace")
        for pattern, reason in SECRET_TEXT:
            if pattern.search(text):
                findings.append(f"{rel}: {reason}")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    findings = audit(args.root.resolve())
    if findings:
        for finding in findings:
            print(f"FAIL: {finding}", file=sys.stderr)
        return 1
    print(f"Privacy audit passed: {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
