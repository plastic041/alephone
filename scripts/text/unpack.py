#!/usr/bin/env python3
"""
Extract editable terminal text directly from Marathon.appl.

Usage:
    python3 unpack.py
    python3 unpack.py [Marathon.appl] [output_directory]

Defaults are resolved relative to this script, so it can be run from either
the project directory or the krpatch directory.
"""

import argparse
from pathlib import Path

import m1res
import termcodec


TERM = b"term"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_APPL = SCRIPT_DIR.parent / "Marathon.appl"
DEFAULT_OUTDIR = SCRIPT_DIR / "terminals_edit"


def unpack(src, outdir):
    appl = m1res.MacBinaryFile(src)
    terms = sorted(appl.by_type(TERM), key=lambda resource: resource.id)
    if not terms:
        raise ValueError(f"no 'term' resources in {src}")

    outdir.mkdir(parents=True, exist_ok=True)
    total = 0
    for resource in terms:
        path = outdir / f"term_{resource.id}.txt"
        with path.open("w", encoding="utf-8", newline="\n") as output:
            output.write(termcodec.decode(resource.data))
        total += len(resource.data)

    levels = sorted({(resource.id - 1000) // 10 for resource in terms})
    print(f"unpacked {len(terms)} terminals ({total} bytes) -> {outdir}")
    print(f"levels covered: {levels}")


def main():
    parser = argparse.ArgumentParser(
        description="Unpack Marathon.appl terminal resources as editable UTF-8 text."
    )
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_APPL)
    parser.add_argument("output_directory", nargs="?", type=Path, default=DEFAULT_OUTDIR)
    args = parser.parse_args()

    unpack(args.source.resolve(), args.output_directory.resolve())


if __name__ == "__main__":
    main()
