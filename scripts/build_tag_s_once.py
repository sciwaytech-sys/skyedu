from pathlib import Path
import os
import re
from dotenv import load_dotenv

from skyed.parser import parse_homework_text
from skyed.tag_gamegen import export_tag_s_matching_pairs

load_dotenv()

text = Path("homework.txt").read_text(encoding="utf-8", errors="ignore")
spec = parse_homework_text(text)

# Minimal tags extraction WITHOUT changing parser:
# Look for a line like: #Tags: classroom, furniture
tags = []
for line in text.splitlines():
    m = re.match(r"^\s*#?\s*Tags?\s*[:：]\s*(.+)\s*$", line.strip(), flags=re.IGNORECASE)
    if m:
        tags = [x.strip() for x in m.group(1).split(",") if x.strip()]
        break

out_root = Path(os.getenv("OUTPUT_DIR", "output")) / "tag_s"
vocab = spec.get("vocab", []) or []

print("tags =", tags)
print("out_root =", out_root)

for t in tags:
    p = export_tag_s_matching_pairs(tag=t, vocab=vocab, out_dir=out_root)
    print("exported:", p)