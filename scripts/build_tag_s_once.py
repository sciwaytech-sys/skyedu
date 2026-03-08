from pathlib import Path
import os
import re
import sys
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skyed.parser import parse_homework_text
from skyed.tag_gamegen import export_tag_s_matching_pairs

load_dotenv(PROJECT_ROOT / ".env")

text = (PROJECT_ROOT / "homework.txt").read_text(encoding="utf-8", errors="ignore")
spec = parse_homework_text(text)

tags = list(spec.get("tags") or [])
if not tags:
    for line in text.splitlines():
        m = re.match(r"^\s*#?\s*Tags?\s*[:：]\s*(.+)\s*$", line.strip(), flags=re.IGNORECASE)
        if m:
            tags = [x.strip() for x in re.split(r"[,，、]", m.group(1)) if x.strip()]
            break

out_root = PROJECT_ROOT / Path(os.getenv("OUTPUT_DIR", "output")) / "tag_s"
vocab = spec.get("vocab", []) or []

print("tags =", tags)
print("out_root =", out_root)

for t in tags:
    p = export_tag_s_matching_pairs(tag=t, vocab=vocab, out_dir=out_root)
    print("exported:", p)
