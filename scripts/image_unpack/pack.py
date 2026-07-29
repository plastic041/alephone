#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from m1_shapes import pack_shapes
from resource_fork import FormatError, pack


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pack an unpacked directory into a classic Mac Images/resource file."
    )
    parser.add_argument("input", type=Path, help="directory containing manifest.json")
    parser.add_argument("output", type=Path, help="output MacBinary or raw resource-fork file")
    args = parser.parse_args()
    try:
        manifest = json.loads((args.input / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") == "marathon-1-shapes":
            count = pack_shapes(args.input, args.output)
            message = f"packed {count} changed menu shapes into {args.output}"
        else:
            count = pack(args.input, args.output)
            message = f"packed {count} resources into {args.output}"
    except (OSError, FormatError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(message)


if __name__ == "__main__":
    main()
