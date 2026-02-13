from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Resolve output directory robustly:
# - If OUTPUT_DIR is set, use it.
# - Otherwise default to <project_root>/output (project_root is the parent of /skyed).
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))).resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()

# Serve: /quiz/<slug>/... from output/<slug>/...
app.mount("/quiz", StaticFiles(directory=str(OUTPUT_DIR), html=True), name="quiz")
