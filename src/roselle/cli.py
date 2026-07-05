from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .converter import vectorize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roselle",
        description="Convert raster graphics into SVG assets with reports.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    vectorize_parser = subparsers.add_parser(
        "vectorize",
        help="Convert an image into SVG and a review bundle.",
    )
    vectorize_parser.add_argument("input", help="Input PNG, JPG, or WebP image.")
    vectorize_parser.add_argument(
        "--out-dir",
        default="out/roselle",
        help="Directory for the conversion bundle.",
    )
    vectorize_parser.add_argument(
        "--palette-size",
        type=int,
        default=20,
        help="Maximum color groups used by final.svg.",
    )
    vectorize_parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable conversion result.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "vectorize":
        result = vectorize(
            input_path=Path(args.input),
            out_dir=Path(args.out_dir),
            palette_size=args.palette_size,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"final.svg: {result['final_svg']}")
            print(f"layered.svg: {result['layered_svg']}")
            print(f"report: {result['report']}")
        return 0 if result["status"] == "ok" else 1

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
