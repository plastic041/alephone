#!/usr/bin/env python3
"""
Pack edited terminal text directly into Marathon.appl.

Usage:
    python3 pack.py
    python3 pack.py [input_directory] [source.appl] [output.appl]

Only resources with a matching term_<id>.txt file are replaced. When the
output already exists, its original contents are preserved as output.appl.bak
unless that backup already exists.
"""

import argparse
from pathlib import Path

import m1res
import termcodec
import logon_center


TERM = b"term"
MAX_TERM = 32767
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_APPL = SCRIPT_DIR.parent / "Marathon.appl"
DEFAULT_INDIR = SCRIPT_DIR / "terminals_edit"


def pack(indir, src, dst):
    appl = m1res.MacBinaryFile(src)
    widths = logon_center.FontWidths()

    replaced = 0
    grew = 0
    padded = 0
    for resource in appl.by_type(TERM):
        path = indir / f"term_{resource.id}.txt"
        if not path.exists():
            continue

        source_text = path.read_text(encoding="utf-8")
        text, changes = logon_center.pad_logon_title(source_text, widths)
        padded += len(changes)
        data = termcodec.encode_all_escaped(text)

        normalized = text.replace("\r\n", "\n").rstrip("\n")
        if termcodec.decode(data).rstrip("\n") != normalized:
            raise ValueError(f"{path}: encode/decode mismatch; refusing to write")
        if len(data) > MAX_TERM:
            raise ValueError(
                f"{path}: {len(data)} bytes exceeds the {MAX_TERM}-byte terminal limit"
            )

        grew += len(data) - len(resource.data)
        resource.data = data
        replaced += 1

    if not replaced:
        raise ValueError(f"no term_<id>.txt files found in {indir}")

    if dst.exists():
        backup = Path(str(dst) + ".bak")
        if not backup.exists():
            backup.write_bytes(dst.read_bytes())
            print(f"backed up {dst} -> {backup}")

    appl.write(dst)

    # Re-open the result to validate its MacBinary CRC and resource map.
    check = m1res.MacBinaryFile(dst)
    if len(check.resources) != len(appl.resources):
        raise ValueError("resource count changed after writing; output is invalid")

    print(f"packed {replaced} terminals ({grew:+d} bytes) -> {dst}")
    print(f"centered {padded} logon title(s) using calculated leading spaces")


def main():
    parser = argparse.ArgumentParser(
        description="Pack edited UTF-8 terminal text into Marathon.appl."
    )
    parser.add_argument("input_directory", nargs="?", type=Path, default=DEFAULT_INDIR)
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_APPL)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_APPL)
    args = parser.parse_args()

    pack(
        args.input_directory.resolve(),
        args.source.resolve(),
        args.output.resolve(),
    )


if __name__ == "__main__":
    main()
