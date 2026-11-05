"""Command-line skeleton for context-manager (F01).

The parameter surface is complete, but the audit and diagnose pipelines belong
to F10.  Until they are connected, valid requests stop with ``not_implemented``
and exit code 1 without creating any output directory.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NoReturn, Sequence

from . import __version__
from .contracts import (
    EXIT_FAILED,
    EXIT_USAGE,
    SCHEMA_VERSION,
    ContractError,
    build_request,
)

_PROG = "context-manager"

_REQUIRED_RE = re.compile(r"^the following arguments are required:")
_KNOWN_ARGUMENT_RE = re.compile(r"^argument ([A-Za-z0-9_.-]+): ")


def _restrict_message(message: str) -> str:
    """Return an argparse error message that never repeats caller-supplied values."""

    if _REQUIRED_RE.match(message):
        return message
    if message.startswith("unrecognized arguments:"):
        return "unrecognized arguments"
    match = _KNOWN_ARGUMENT_RE.match(message)
    if match:
        return f"argument {match.group(1)}: invalid value"
    return "invalid arguments"


class _RedactingParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {_restrict_message(message)}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _RedactingParser(
        prog=_PROG,
        description="Audit project instructions and diagnose repeated coding-agent failures.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{_PROG} {__version__} (schema_version {SCHEMA_VERSION})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    audit = subparsers.add_parser(
        "audit",
        help="check the current project instructions",
        description="Check the current project instructions for stale or conflicting rules.",
    )
    _add_common_arguments(audit)
    audit.add_argument(
        "--case",
        default=None,
        metavar="FILE",
        help=argparse.SUPPRESS,
    )

    diagnose = subparsers.add_parser(
        "diagnose",
        help="diagnose repeated failures from a synthetic incident package",
        description="Audit the project and correlate it with a synthetic incident package.",
    )
    _add_common_arguments(diagnose)
    diagnose.add_argument(
        "--case",
        required=True,
        metavar="FILE",
        help="synthetic incident package to analyse",
    )
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, metavar="PATH", help="project root")
    parser.add_argument("--project-id", required=True, metavar="ID", help="local project alias")
    parser.add_argument(
        "--scope",
        action="append",
        default=None,
        metavar="PREFIX",
        help="project-relative scope prefix; repeatable (default: '.')",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        metavar="ISO_TIME",
        help="timezone-aware ISO 8601 timestamp (default: now)",
    )
    parser.add_argument(
        "--decisions",
        default=None,
        metavar="FILE",
        help="decision registry document",
    )
    parser.add_argument(
        "--semantic",
        choices=("off", "local"),
        default="off",
        help="semantic analysis mode (default: off)",
    )
    parser.add_argument(
        "--model-config",
        default=None,
        metavar="FILE",
        help="local model configuration; required with --semantic local",
    )
    parser.add_argument("--out", required=True, metavar="PATH", help="output root outside the project")


def print_contract_error(error: ContractError) -> None:
    print(f"{_PROG}: {error.code}", file=sys.stderr)
    if error.errors:
        for field_error in error.errors:
            print(
                f"  {field_error.path}: {field_error.code}: {field_error.message}",
                file=sys.stderr,
            )
    else:
        print(f"  {error.message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        # argparse already printed help, version, or a usage error.
        code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
        return code

    try:
        build_request(args, cwd=Path.cwd())
    except ContractError as error:
        print_contract_error(error)
        return error.exit_code

    command = getattr(args, "command", None) or "audit"
    print(f"{_PROG}: not_implemented", file=sys.stderr)
    print(f"  {command} pipeline is not available in this build", file=sys.stderr)
    return EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover - module execution is covered by CLI tests
    raise SystemExit(main())