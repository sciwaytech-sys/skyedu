from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def _resolve_default_output_dir() -> Path:
    """
    Support both layouts:
      1) <project_root>/quiz_server.py            -> output at <project_root>/output
      2) <project_root>/skyed/quiz_server.py      -> output at <project_root>/output
    """
    here = Path(__file__).resolve()

    # If file is inside package folder "skyed", use parent of that folder
    if here.parent.name.lower() == "skyed":
        return (here.parent.parent / "output").resolve()

    # Otherwise assume file is at project root
    return (here.parent / "output").resolve()


DEFAULT_OUTPUT_DIR = _resolve_default_output_dir()
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))).resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="SkyEd Quiz Static Server")

# Serve: /quiz/<slug>/... from output/<slug>/...
app.mount("/quiz", StaticFiles(directory=str(OUTPUT_DIR), html=True), name="quiz")


@app.get("/healthz")
def healthz() -> dict:
    return {
        "ok": True,
        "output_dir": str(OUTPUT_DIR),
        "exists": OUTPUT_DIR.exists(),
    }
