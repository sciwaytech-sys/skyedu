from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image

from .image_specs import ImageSpec


@dataclass(slots=True)
class ValidationResult:
    accepted: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    image_path: str = ""


class ImageValidationError(RuntimeError):
    pass


class ImageValidator:
    """
    Lightweight validator.

    Important behavior:
    - validate semantic anchors against the semantic part of the prompt only
    - never punish explicit no-text safety clauses as if they were semantic content
    - use softer thresholds for easy literal items and stricter ones for relation/comparison scenes
    """

    _CJK_RE = re.compile(r"[一-鿿]")
    _SAFETY_CLAUSE_RE = re.compile(
        r"^(?:no\s+|without\s+)(text|letters|numbers|readable numbers|chinese characters|english words|labels?|captions?|signboards?|watermark|logo)",
        flags=re.IGNORECASE,
    )
    _GENERIC_ANCHORS = {
        "child",
        "plain background",
        "single obvious concept",
        "clear action",
        "clear relation",
        "clear count",
        "clear question context",
        "clear meaning",
        "person or group",
        "simple objects",
        "two items",
        "three items",
        "daily life",
        "single child or object",
    }
    _GENERIC_FORBIDDEN = {
        "text",
        "letters",
        "numbers",
        "numbers drawn as labels",
        "label",
        "caption",
        "english words",
        "chinese characters",
        "watermark",
        "logo",
    }

    def __init__(self, min_side: int = 256, min_entropy: float = 2.0, max_gray_ratio: float = 0.92) -> None:
        self.min_side = min_side
        self.min_entropy = min_entropy
        self.max_gray_ratio = max_gray_ratio

    def validate(self, image_path: str | Path, spec: ImageSpec, used_prompt: Optional[str] = None) -> ValidationResult:
        path = Path(image_path)
        reasons: list[str] = []
        score = 1.0
        if not path.exists():
            raise ImageValidationError(f"Image not found: {path}")

        try:
            img = Image.open(path).convert("RGB")
        except Exception as exc:
            raise ImageValidationError(f"Unable to open image '{path}': {exc}") from exc

        width, height = img.size
        if width < self.min_side or height < self.min_side:
            reasons.append(f"Image too small: {width}x{height}")
            score -= 0.40

        entropy = self._entropy(img)
        if entropy < self.min_entropy:
            reasons.append(f"Low entropy / likely blank or near-blank image: {entropy:.2f}")
            score -= 0.40

        gray_ratio = self._gray_ratio(img)
        if gray_ratio > self.max_gray_ratio:
            reasons.append(f"Image too gray/flat: gray_ratio={gray_ratio:.2f}")
            score -= 0.30

        if used_prompt:
            prompt_lc = used_prompt.lower()
            semantic_prompt = self._semantic_prompt_only(prompt_lc)
            core_hits, required_checks = self._count_anchor_hits(spec, semantic_prompt)
            if required_checks:
                min_hits, min_ratio = self._anchor_policy(spec, required_checks)
                hit_ratio = core_hits / required_checks
                if core_hits < min_hits or hit_ratio < min_ratio:
                    reasons.append(
                        f"Used prompt missed too many required semantic anchors ({core_hits}/{required_checks}; needed >= {min_hits})"
                    )
                    score -= 0.18

            forbidden_hits = self._forbidden_semantic_hits(spec, semantic_prompt)
            if forbidden_hits:
                reasons.append(f"Used semantic prompt contains forbidden anchors: {', '.join(forbidden_hits[:5])}")
                score -= 0.14

            if self._CJK_RE.search(prompt_lc):
                reasons.append("Used prompt contains Chinese characters; image prompts should avoid accidental language leakage")
                score -= 0.12

            banned_prompt_markers = [
                "chinese target meaning:",
                "example context:",
                "lesson theme:",
                "preposition:",
                "adjective:",
                "verb:",
                "question_word:",
            ]
            found_markers = [m for m in banned_prompt_markers if m in semantic_prompt]
            if found_markers:
                reasons.append(f"Used semantic prompt contains teaching/meta markers: {', '.join(found_markers)}")
                score -= 0.14

        accepted = score >= 0.60 and not any("too small" in r.lower() for r in reasons)
        return ValidationResult(
            accepted=accepted,
            score=max(0.0, min(1.0, score)),
            reasons=reasons,
            image_path=str(path),
        )

    def _semantic_prompt_only(self, prompt_lc: str) -> str:
        clauses = [c.strip() for c in re.split(r"[,\n]+", prompt_lc) if c.strip()]
        semantic_clauses: list[str] = []
        for clause in clauses:
            if self._SAFETY_CLAUSE_RE.search(clause):
                continue
            semantic_clauses.append(clause)
        return ", ".join(semantic_clauses)

    def _count_anchor_hits(self, spec: ImageSpec, semantic_prompt: str) -> tuple[int, int]:
        core_hits = 0
        required_checks = 0
        for token in list(spec.must_include[:6]):
            token_lc = str(token or "").strip().lower()
            if not token_lc:
                continue
            if not any(ch.isalpha() for ch in token_lc):
                continue
            if token_lc in self._GENERIC_FORBIDDEN or token_lc in self._GENERIC_ANCHORS:
                continue
            required_checks += 1
            if token_lc in semantic_prompt:
                core_hits += 1
        return core_hits, required_checks

    def _forbidden_semantic_hits(self, spec: ImageSpec, semantic_prompt: str) -> list[str]:
        out: list[str] = []
        for token in spec.must_exclude:
            token_lc = str(token or "").strip().lower()
            if not token_lc or token_lc in self._GENERIC_FORBIDDEN:
                continue
            if token_lc in semantic_prompt:
                out.append(token_lc)
        return out

    def _anchor_policy(self, spec: ImageSpec, required_checks: int) -> tuple[int, float]:
        render_mode = str(getattr(spec, "render_mode", "") or "").strip().lower()
        pos = str(getattr(spec, "pos", "") or "").strip().lower()
        scene_type = str(getattr(spec, "scene_type", "") or "").strip().lower()

        if render_mode in {"single_object", "object", "single"} or scene_type in {"literal_object_scene"}:
            return (1 if required_checks >= 1 else 0, 0.25)
        if render_mode in {"attribute_scene", "icon_card", "text_card", "portrait_scene"} or pos in {"noun", "adjective", "pronoun"}:
            return (1 if required_checks >= 1 else 0, 0.28)
        if render_mode in {"action_scene", "routine_scene", "counting_scene", "question_scene"} or pos in {"verb", "phrase", "expression", "number", "question_word"}:
            return (min(2, required_checks), 0.34)
        if render_mode in {"relation_scene", "contrast_pair", "comparison_scene"} or pos in {"preposition"}:
            return (min(2, required_checks), 0.40)
        return (1 if required_checks >= 1 else 0, 0.30)

    @staticmethod
    def _entropy(img: Image.Image) -> float:
        histogram = img.convert("L").histogram()
        total = sum(histogram)
        if total == 0:
            return 0.0
        probs = [value / total for value in histogram if value]
        return -sum(p * math.log2(p) for p in probs)

    @staticmethod
    def _gray_ratio(img: Image.Image) -> float:
        rgb = img.convert("RGB")
        pixels = list(rgb.getdata())
        if not pixels:
            return 1.0
        gray_count = 0
        for r, g, b in pixels:
            if abs(r - g) <= 8 and abs(g - b) <= 8 and abs(r - b) <= 8:
                gray_count += 1
        return gray_count / len(pixels)


__all__ = ["ImageValidator", "ValidationResult", "ImageValidationError"]
