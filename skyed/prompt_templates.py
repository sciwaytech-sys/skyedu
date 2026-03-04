# skyed/prompt_templates.py
from __future__ import annotations

from typing import Tuple

"""Prompt templates

This project generates *flashcard-like* images. We keep prompts:
- short
- deterministic
- safe for kids

We support multiple "render modes" so verbs/adjectives don't get forced into
"single object" compositions.

Render modes (subset):
- single_object
- action_scene
- contrast_pair
- attribute_scene
- icon_card
- text_card
"""

# --------------------------
# Global negative constraints
# --------------------------

NEG_ALWAYS = (
    "abstract, surreal, cubism, expressionism, glitch, noise, watercolor, oil painting, sketch, lineart, "
    "lowpoly, 3d render, blurry, low quality, worst quality, messy background, "
    "deformed, disfigured, mutated, extra limbs, bad anatomy, bad hands, "
    "text, watermark, logo, letters, signature"
)


def normalize_style(picture_cards_type: str) -> str:
    """Normalize UI style labels to internal keys."""
    s = (picture_cards_type or "").strip().lower()
    if s.startswith("real"):
        return "realistic"
    return "cartoon"


# --------------------------
# Positive prompt templates
# --------------------------

# SINGLE OBJECT (nouns)
POS_REALISTIC_SINGLE = (
    "photo, highly realistic, natural lighting, sharp focus, detailed textures, clean composition, "
    "plain background, product photo, studio quality, centered subject\n"
    "SUBJECT: {subject} (single object, clear, centered, no clutter)"
)

POS_CARTOON_SINGLE = (
    "cute cartoon illustration, clean lines, flat shading, bright cheerful colors, children book style, "
    "simple background, centered subject, clear silhouette\n"
    "SUBJECT: {subject} (single object, clear, centered, no clutter)"
)

# ACTION SCENE (verbs / some sentences)
POS_REALISTIC_ACTION = (
    "photo, highly realistic, natural lighting, sharp focus, clean composition, "
    "one main subject, kid-friendly, no text\n"
    "SCENE: {subject} (clear action, simple background, minimal clutter)"
)

POS_CARTOON_ACTION = (
    "cute cartoon illustration, clean lines, bright cheerful colors, children book style, "
    "one main subject, kid-friendly, no text\n"
    "SCENE: {subject} (clear action, simple background, minimal clutter)"
)

# CONTRAST PAIR (adjectives like big/small)
POS_REALISTIC_CONTRAST = (
    "photo, highly realistic, studio lighting, sharp focus, clean composition, plain background\n"
    "PAIR: {subject} (two items side by side, clear comparison, minimal clutter)"
)

POS_CARTOON_CONTRAST = (
    "cute cartoon illustration, clean lines, bright colors, educational flashcard style, plain background\n"
    "PAIR: {subject} (two items side by side, clear comparison, minimal clutter)"
)

# ATTRIBUTE SCENE (adjective applied to a noun)
POS_REALISTIC_ATTRIBUTE = (
    "photo, highly realistic, studio lighting, sharp focus, clean composition, plain background\n"
    "SUBJECT: {subject} (single main object, attribute clearly visible, minimal clutter)"
)

POS_CARTOON_ATTRIBUTE = (
    "cute cartoon illustration, clean lines, bright colors, educational flashcard style, plain background\n"
    "SUBJECT: {subject} (single main object, attribute clearly visible, minimal clutter)"
)

# ICON/TEXT fallback (when the word is too abstract)
POS_REALISTIC_ICON = (
    "simple flat icon, clean composition, plain background, centered, no text\n"
    "ICON: {subject} (simple symbol, kid-friendly)"
)

POS_CARTOON_ICON = (
    "simple flat icon, clean composition, plain background, centered, no text\n"
    "ICON: {subject} (simple symbol, kid-friendly)"
)


def render_prompts(style: str, subject: str, render_mode: str = "single_object") -> Tuple[str, str]:
    """Return (positive_prompt, negative_prompt)."""

    style_k = (style or "").strip().lower()
    mode = (render_mode or "single_object").strip().lower()

    is_real = style_k.startswith("real")

    if mode in ("single", "single_object", "object"):
        pos = (POS_REALISTIC_SINGLE if is_real else POS_CARTOON_SINGLE).format(subject=subject)
    elif mode in ("action", "action_scene", "scene"):
        pos = (POS_REALISTIC_ACTION if is_real else POS_CARTOON_ACTION).format(subject=subject)
    elif mode in ("contrast", "contrast_pair", "pair"):
        pos = (POS_REALISTIC_CONTRAST if is_real else POS_CARTOON_CONTRAST).format(subject=subject)
    elif mode in ("attribute", "attribute_scene"):
        pos = (POS_REALISTIC_ATTRIBUTE if is_real else POS_CARTOON_ATTRIBUTE).format(subject=subject)
    elif mode in ("icon", "icon_card", "text", "text_card"):
        pos = (POS_REALISTIC_ICON if is_real else POS_CARTOON_ICON).format(subject=subject)
    else:
        # Safe fallback
        pos = (POS_REALISTIC_SINGLE if is_real else POS_CARTOON_SINGLE).format(subject=subject)

    return pos, NEG_ALWAYS
