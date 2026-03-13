from __future__ import annotations

import argparse
from pathlib import Path

from skyed.image_specs import build_specs_from_homework_text, save_specs_json


def main() -> int:
    ap = argparse.ArgumentParser(description="Build image specs JSON from homework.txt")
    ap.add_argument("--input", required=True, help="Path to homework.txt")
    ap.add_argument("--out", required=True, help="Path to output JSON")
    args = ap.parse_args()

    input_path = Path(args.input)
    text = input_path.read_text(encoding="utf-8")
    specs = build_specs_from_homework_text(text)
    out_path = save_specs_json(specs, args.out)
    print(f"Saved {len(specs)} image specs -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
