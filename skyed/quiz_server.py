from __future__ import annotations
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")

app = FastAPI()

# Serve: /quiz/<slug>/... from output/<slug>/quiz/...
# We mount the whole output folder under /quiz (read-only)
app.mount("/quiz", StaticFiles(directory=OUTPUT_DIR, html=True), name="quiz")
