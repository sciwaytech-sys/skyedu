from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageStat

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

    What it catches reliably:
    - blank/gray/low-entropy images
    - tiny/corrupted images
    - prompt collisions caused by missing required anchor words in the used prompt

    What it does NOT guarantee without an external vision model:
    - true semantic understanding of the rendered content

    This is still useful because the main fix is stronger prompt construction.
    """

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
            core_hits = 0
            required_checks = 0
            for token in spec.must_include[:6]:
                token_lc = token.lower()
                if any(ch.isalpha() for ch in token_lc):
                    required_checks += 1
                    if token_lc in prompt_lc:
                        core_hits += 1
            if required_checks:
                hit_ratio = core_hits / required_checks
                if hit_ratio < 0.45:
                    reasons.append(
                        f"Used prompt missed too many required anchors ({core_hits}/{required_checks})"
                    )
                    score -= 0.30

            forbidden_hits = [token for token in spec.must_exclude if token.lower() in prompt_lc]
            if forbidden_hits:
                reasons.append(f"Used prompt contains forbidden anchors: {', '.join(forbidden_hits[:5])}")
                score -= 0.35

        accepted = score >= 0.60 and not any("too small" in r.lower() for r in reasons)
        return ValidationResult(
            accepted=accepted,
            score=max(0.0, min(1.0, score)),
            reasons=reasons,
            image_path=str(path),
        )

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
