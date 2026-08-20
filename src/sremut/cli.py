"""Command-line boundary for the SREMut scaffold."""

import argparse
import json
import sys
from collections.abc import Sequence

from sremut.runtime_identity import status_document


def build_parser() -> argparse.ArgumentParser:
    """Build the closed scaffold command interface."""
    parser = argparse.ArgumentParser(
        prog="sremut",
        description="SREMut scaffold identity interface.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    status_parser = subparsers.add_parser(
        "status",
        help="Report pinned runtime identity.",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        required=True,
        help="Emit deterministic JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Emit scaffold status."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command != "status" or not arguments.json:
        parser.error("only status --json is available")

    document = status_document()
    sys.stdout.write(json.dumps(document, sort_keys=True, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0 if document["runtime_requirements_match"] else 1
