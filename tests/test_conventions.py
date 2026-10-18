"""Repo conventions: narrow exceptions, no secrets, and the product name."""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

from gridiron_lab.logging import LOGGER_NAME, configure_logging, get_logger

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_SUFFIXES = {".py", ".md", ".html", ".toml", ".yml", ".example"}
# Split so this file does not itself contain the previous product name.
_LEGACY_NAME = "Fandro" + "meda"
_ALLOWED_LEGACY_REFS = (
    f"https://github.com/Jpidge/{_LEGACY_NAME}2",
    f"Jpidge/{_LEGACY_NAME}2",
)


def test_sources_catch_specific_exceptions() -> None:
    offenders: list[str] = []
    broad = {"Exception", "BaseException"}
    for path in (REPO_ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            caught = node.type
            if caught is None or (isinstance(caught, ast.Name) and caught.id in broad):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == []


def test_ruff_selects_blind_except_rule() -> None:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "BLE" in text


def test_gitignore_covers_secrets_and_local_data() -> None:
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for needle in (".env", "data/", "*.parquet", "oauth_tokens.json", "secrets/"):
        assert needle in text
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "YAHOO_REFRESH_TOKEN=" in example
    filled = [
        line
        for line in example.splitlines()
        if line.startswith("YAHOO_") and line.split("=", 1)[1].strip()
    ]
    assert filled == []


def test_product_name_is_not_the_previous_tool() -> None:
    banned = re.compile(re.escape(_LEGACY_NAME), re.IGNORECASE)
    leftovers: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in _SCAN_SUFFIXES:
            continue
        if any(part in {".git", ".venv", "venv"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        for allowed in _ALLOWED_LEGACY_REFS:
            text = text.replace(allowed, "")
        if banned.search(text):
            leftovers.append(str(path.relative_to(REPO_ROOT)))
    assert leftovers == []


def test_configure_logging_is_idempotent() -> None:
    logger = configure_logging(logging.INFO)
    handler_count = len(logger.handlers)
    again = configure_logging(logging.WARNING)
    assert again is logger
    assert len(logger.handlers) == handler_count
    assert get_logger("scoring").name == f"{LOGGER_NAME}.scoring"
