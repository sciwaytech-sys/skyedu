# skyed/prompt_templates.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

# Keep templates short and deterministic for vocab cards.
# SUBJECT is injected as a single object, centered, plain background.

POS_REALISTIC = (
    "photo, highly realistic, natural lighting, sharp focus, detailed textures, clean composition, "
    "centered subject, plain background, product photo, studio quality\n"
    "SUBJECT: {subject} (single object, clear, centered, no clutter)"
)

POS_CARTOON = (
    "cute cartoon illustration, clean lines, flat shading, bright cheerful colors, children book style, "
    "simple background, centered subject, clear silhouette\n"
    "SUBJECT: {subject} (single object, clear, centered, no clutter)"
)

NEG_ALWAYS = (
    "abstract, surreal, cubism, expressionism, glitch, noise, watercolor, oil painting, sketch, lineart, "
    "lowpoly, 3d render, blurry, low quality, worst quality, messy background, "
    "deformed, disfigured, mutated, extra limbs, bad anatomy, bad hands, "
    "text, watermark, logo, letters, signature"
)

def normalize_style(picture_cards_type: str) -> str:
    s = (picture_cards_type or "").strip().lower()
    if s.startswith("real"):
        return "realistic"
    return "cartoon"

def render_prompts(style: str, subject: str) -> Tuple[str, str]:
    style = (style or "").strip().lower()
    if style.startswith("real"):
        pos = POS_REALISTIC.format(subject=subject)
    else:
        pos = POS_CARTOON.format(subject=subject)
    neg = NEG_ALWAYS
    return pos, neg
