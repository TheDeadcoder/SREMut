"""Removes date and time information left in stored results; --check only lists the files that still have some."""
import argparse
import gzip
import json
import sys
from pathlib import Path

from run_sweep import scrub, scrub_value, write_gz, write_json


def rescrub(path, check):
    if path.suffix == ".gz":
        raw = path.read_bytes()
        text = gzip.decompress(raw).decode(errors="replace")
        new = scrub(text)
        dirty = new != text or raw[4:8] != bytes(4)
        if dirty and not check:
            write_gz(path, new)
        return dirty
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: {exc}")
    new = scrub_value(data)
    if new != data and not check:
        write_json(path, new)
    return new != data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("dirs", nargs="+", type=Path)
    args = ap.parse_args()
    dirty = [path for d in args.dirs for path in sorted(d.rglob("*"))
             if path.suffix in (".gz", ".json") and rescrub(path, args.check)]
    for path in dirty:
        print(path)
    return 1 if args.check and dirty else 0


if __name__ == "__main__":
    sys.exit(main())
