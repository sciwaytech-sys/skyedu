from __future__ import annotations

# image_specs
from .image_specs import (
    VocabItem,
    ImageSpec,
    normalize_pos,
    slugify,
    infer_scene_type,
    parse_homework_vocabulary,
    build_image_spec,
    build_specs_from_homework_text,
    build_specs_from_parsed_spec,
    save_specs_json,
)

# image_validation
from .image_validation import (
    ValidationResult,
    ImageValidationError,
    ImageValidator,
)

# fallback_cards
from .fallback_cards import make_fallback_card

__all__ = [
    # image_specs
    "VocabItem",
    "ImageSpec",
    "normalize_pos",
    "slugify",
    "infer_scene_type",
    "parse_homework_vocabulary",
    "build_image_spec",
    "build_specs_from_homework_text",
    "build_specs_from_parsed_spec",
    "save_specs_json",
    # image_validation
    "ValidationResult",
    "ImageValidationError",
    "ImageValidator",
    # fallback_cards
    "make_fallback_card",
]
