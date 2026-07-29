#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from m1_shapes import is_m1_shapes, unpack_shapes
from resource_fork import FormatError, unpack


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unpack a classic Mac Images/resource file into editable resource blobs."
    )
    parser.add_argument("input", type=Path, help="MacBinary or raw resource-fork file")
    parser.add_argument(
        "output", type=Path, nargs="?", help="output directory (default: <input>.unpacked)"
    )
    args = parser.parse_args()
    output = args.output or args.input.with_name(args.input.name + ".unpacked")
    try:
        if is_m1_shapes(args.input):
            count = unpack_shapes(args.input, output)
            noun = "editable images"
        else:
            count = unpack(args.input, output)
            noun = "resources"
    except (OSError, FormatError, ValueError) as error:
        parser.error(str(error))
    print(f"unpacked {count} {noun} into {output}")


if __name__ == "__main__":
    main()
