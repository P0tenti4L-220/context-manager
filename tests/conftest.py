"""Shared pytest configuration for the F01 test entry point."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--local-model-config",
        action="store",
        default=None,
        metavar="PATH",
        help="path to a local model TOML configuration",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "local_model: needs a local model runner and --local-model-config",
    )


@pytest.fixture
def local_model_config(request: pytest.FixtureRequest) -> dict:
    value = request.config.getoption("--local-model-config")
    if not value:
        pytest.skip("local_model_test: not_run (pass --local-model-config <file>)")
    path = Path(value)
    if not path.is_file():
        pytest.fail(f"local_model_test: config file not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        pytest.fail(f"local_model_test: config file is not readable UTF-8: {exc}")
    try:
        parsed = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        pytest.fail(f"local_model_test: invalid TOML: {exc}")
    if not isinstance(parsed, dict):
        pytest.fail("local_model_test: config root is not a TOML table")
    return parsed


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]