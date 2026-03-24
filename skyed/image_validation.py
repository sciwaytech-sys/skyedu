from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

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

    Key rules:
    - judge semantic anchors against the semantic prompt only
    - ignore safety clauses like 'no text' when checking forbidden tokens
    - use softer thresholds for easy single-object/counting scenes
    - treat prompt contamination separately from real image blankness problems
    - reject OCR-detected gibberish/text artifacts so lesson images do not ship with nonsense writing
    """

    _CJK_RE = re.compile(r"[一-鿿]")
    _SAFETY_CLAUSE_RE = re.compile(
        r"(?:^|,|;)\s*(?:no\s+|without\s+)(text|letters|numbers|chinese characters|english words|labels?|captions?|signboards?|watermark|logo)",
        flags=re.IGNORECASE,
    )

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
            score -= 0.28

        detected_text = self._ocr_detected_text(img, spec, allow_supported_text=self._allow_image_text())
        if detected_text:
            reasons.append(f"Generated image contains text-like artifacts: {', '.join(detected_text[:4])}")
            score -= 0.24

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
                    score -= 0.20

            forbidden_hits = self._forbidden_semantic_hits(spec, semantic_prompt)
            if forbidden_hits:
                reasons.append(f"Used semantic prompt contains forbidden anchors: {', '.join(forbidden_hits[:5])}")
                score -= 0.18

            if self._CJK_RE.search(used_prompt):
                reasons.append("Used prompt contains Chinese characters; image prompts should avoid language leakage")
                score -= 0.15

            banned_prompt_markers = [
                "chinese target meaning:",
                "example context:",
                "lesson theme:",
                "preposition:",
                "adjective:",
                "verb:",
                "question_word:",
                "number:",
            ]
            found_markers = [m for m in banned_prompt_markers if m in semantic_prompt]
            if found_markers:
                reasons.append(f"Used semantic prompt contains teaching/meta markers: {', '.join(found_markers)}")
                score -= 0.16

            text_guard_tokens = ["no text", "no letters", "no numbers"]
            guard_hits = sum(1 for tok in text_guard_tokens if tok in prompt_lc)
            if guard_hits < 2 and (spec.render_mode not in {"symbolic_card"}):
                reasons.append("Used prompt missing strong no-text guardrails")
                score -= 0.08

        accepted = score >= 0.60 and not any("too small" in r.lower() for r in reasons)
        return ValidationResult(accepted=accepted, score=max(0.0, round(score, 3)), reasons=reasons, image_path=str(path))

    def _semantic_prompt_only(self, prompt_lc: str) -> str:
        tokens = []
        for raw in re.split(r"[,;]", prompt_lc or ""):
            frag = raw.strip()
            if not frag:
                continue
            if self._SAFETY_CLAUSE_RE.search(frag):
                continue
            tokens.append(frag)
        return ", ".join(tokens)

    def _anchor_policy(self, spec: ImageSpec, required_checks: int) -> tuple[int, float]:
        scene_type = (spec.scene_type or "").lower()
        render_mode = (spec.render_mode or "").lower()
        if render_mode in {"single_object", "counting_scene"}:
            return (1 if required_checks <= 2 else 2, 0.34)
        if "comparison" in scene_type or render_mode in {"relation_scene", "contrast_pair"}:
            return (max(2, math.ceil(required_checks * 0.45)), 0.40)
        if render_mode in {"guided_scene", "action_scene", "attribute_scene"}:
            return (max(2, math.ceil(required_checks * 0.34)), 0.34)
        return (max(1, math.ceil(required_checks * 0.34)), 0.34)

    def _count_anchor_hits(self, spec: ImageSpec, semantic_prompt: str) -> tuple[int, int]:
        must_include = [self._normalize_token(x) for x in (spec.must_include or []) if self._normalize_token(x)]
        required_checks = len(must_include)
        hits = 0
        for token in must_include:
            if token and token in semantic_prompt:
                hits += 1
        return hits, required_checks

    def _forbidden_semantic_hits(self, spec: ImageSpec, semantic_prompt: str) -> list[str]:
        forbidden = []
        for token in (spec.must_exclude or []):
            norm = self._normalize_token(token)
            if not norm:
                continue
            if norm in {"text", "letters", "numbers", "english words", "chinese characters", "labels", "label"}:
                continue
            if norm in semantic_prompt:
                forbidden.append(norm)
        return forbidden

    def _allow_image_text(self) -> bool:
        return os.getenv("SKYED_ALLOW_IMAGE_TEXT", "").strip().lower() in {"1", "true", "yes", "on"}

    def _ocr_detected_text(self, img: Image.Image, spec: ImageSpec, *, allow_supported_text: bool) -> list[str]:
        try:
            import pytesseract  # type: ignore
        except Exception:
            return []

        try:
            scan = self._prepare_for_text_scan(img)
            data = pytesseract.image_to_data(scan, lang="eng", config="--psm 6", output_type=pytesseract.Output.DICT)
        except Exception:
            return []

        allowed = self._allowed_text_tokens(spec) if allow_supported_text else set()
        found: list[str] = []
        seen = set()
        n = len(data.get("text", []))
        for i in range(n):
            raw = str(data["text"][i] or "").strip()
            if not raw:
                continue
            try:
                conf = float(str(data.get("conf", ["0"])[i]))
            except Exception:
                conf = 0.0
            if conf < 42:
                continue
            for token in re.findall(r"[A-Za-z]{3,}|[一-鿿]{2,}", raw):
                norm = self._normalize_token(token)
                if not norm or norm in seen:
                    continue
                if allow_supported_text and self._token_allowed(norm, allowed):
                    continue
                seen.add(norm)
                found.append(norm)
        return found

    @staticmethod
    def _prepare_for_text_scan(img: Image.Image) -> Image.Image:
        gray = ImageOps.autocontrast(img.convert("L"))
        w, h = gray.size
        if max(w, h) < 900:
            gray = gray.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
        return gray.point(lambda p: 255 if p > 170 else 0)

    def _allowed_text_tokens(self, spec: ImageSpec) -> set[str]:
        allowed: set[str] = set()
        candidates = [spec.word] + list(spec.must_include or [])
        for raw in candidates:
            for token in re.findall(r"[A-Za-z]{2,}|[一-鿿]{2,}", raw or ""):
                allowed.add(self._normalize_token(token))
        return allowed

    @staticmethod
    def _token_allowed(token: str, allowed: set[str]) -> bool:
        if token in allowed:
            return True
        return any(token in item or item in token for item in allowed if item)

    @staticmethod
    def _normalize_token(token: str) -> str:
        t = (token or "").strip().lower()
        t = re.sub(r"\s+", " ", t)
        return t

    @staticmethod
    def _entropy(img: Image.Image) -> float:
        hist = img.convert("L").histogram()
        total = sum(hist)
        if total <= 0:
            return 0.0
        entropy = 0.0
        for count in hist:
            if count <= 0:
                continue
            p = count / total
            entropy -= p * math.log2(p)
        return entropy

    @staticmethod
    def _gray_ratio(img: Image.Image) -> float:
        rgb = img.resize((128, 128)).convert("RGB")
        gray_like = 0
        total = 128 * 128
        for r, g, b in list(rgb.getdata()):
            if abs(r - g) < 9 and abs(r - b) < 9 and abs(g - b) < 9:
                gray_like += 1
        return gray_like / max(1, total)
