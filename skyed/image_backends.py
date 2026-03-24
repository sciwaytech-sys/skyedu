# skyed/image_backends.py
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
from PIL import Image
from io import BytesIO

from .prompt_templates import render_prompts


def _stable_seed_for_text(text: str) -> int:
    import hashlib
    h = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _decode_cf_image_json(obj: Any) -> bytes:
    # Cloudflare /client/v4 endpoints often return:
    # { "success": true, "result": { "image": "<base64>" } }
    # but docs also show direct { "image": "<base64>" } in Workers code.
    if isinstance(obj, dict):
        if "result" in obj and isinstance(obj["result"], dict):
            obj = obj["result"]
        if "image" in obj and isinstance(obj["image"], str):
            return base64.b64decode(obj["image"])
    raise ValueError("Cloudflare response missing base64 'image' field")


def _ensure_png_bytes(raw: bytes) -> bytes:
    # Convert whatever we received (jpg/png/webp) into PNG bytes for consistent downstream usage.
    im = Image.open(BytesIO(raw))
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


@dataclass
class ImageGenRequest:
    subject: str
    style: str  # 'realistic' | 'cartoon'
    render_mode: str = "single_object"  # single_object|action_scene|contrast_pair|attribute_scene|icon_card|text_card
    width: int = 768
    height: int = 768
    steps: int = 20
    seed: Optional[int] = None
    positive_prompt: Optional[str] = None
    negative_prompt: Optional[str] = None


class BaseImageBackend:
    name: str = "base"

    def generate_png(self, req: ImageGenRequest, *, timeout_s: int = 180) -> bytes:
        raise NotImplementedError


class CloudflareFluxBackend(BaseImageBackend):
    name = "cloudflare_flux"

    def __init__(self, *, account_id: str, api_token: str, model: str) -> None:
        self.account_id = account_id.strip()
        self.api_token = api_token.strip()
        self.model = model.strip() or "@cf/black-forest-labs/flux-1-schnell"

    def generate_png(self, req: ImageGenRequest, *, timeout_s: int = 180) -> bytes:
        if not self.account_id:
            raise ValueError("CF_ACCOUNT_ID missing")
        if not self.api_token:
            raise ValueError("CF_API_TOKEN missing")

        # Flux schnell supports: prompt (required), steps (default 4, max 8), seed (used in examples).
        # It returns JSON {image: base64}. Official docs: https://developers.cloudflare.com/workers-ai/models/flux-1-schnell/
        steps = int(req.steps)
        if steps < 1:
            steps = 1
        if steps > 8:
            steps = 8

        seed_basis = req.positive_prompt or req.subject
        seed = req.seed if req.seed is not None else _stable_seed_for_text(seed_basis)

        pos, _neg = render_prompts(req.style, req.subject, req.render_mode)
        if req.positive_prompt:
            pos = req.positive_prompt
        # Cloudflare pricing is tile-based, and the current docs/pricing reference image tiles and
        # model parameters including image dimensions in the full schema. We therefore send width/height
        # explicitly instead of relying on the provider default canvas size. Some schema variants may reject
        # these extra keys, so we fall back to the minimal prompt/steps/seed payload on 4xx validation errors.
        base_payload: Dict[str, Any] = {
            "prompt": pos,
            "steps": steps,
            "seed": int(seed),
        }
        w = int(req.width) if req.width else 768
        h = int(req.height) if req.height else 768
        w = max(256, min(2048, w))
        h = max(256, min(2048, h))
        payload_with_size: Dict[str, Any] = dict(base_payload)
        payload_with_size["width"] = w
        payload_with_size["height"] = h

        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{self.model}"
        headers = {"Authorization": f"Bearer {self.api_token}"}

        def _post(payload: Dict[str, Any]) -> requests.Response:
            return requests.post(url, headers=headers, json=payload, timeout=timeout_s)

        r = _post(payload_with_size)
        if r.status_code >= 400:
            body = r.text[:1200] if hasattr(r, "text") else ""
            lowered = body.lower()
            if r.status_code in (400, 422) and any(tok in lowered for tok in ["width", "height", "unknown field", "additional properties"]):
                r = _post(base_payload)
            if r.status_code >= 400:
                body = r.text[:1200] if hasattr(r, "text") else ""
                raise requests.HTTPError(
                    f"Cloudflare image request failed: {r.status_code} {r.reason}. Body: {body}",
                    response=r,
                )
        obj = r.json()
        raw = _decode_cf_image_json(obj)
        return _ensure_png_bytes(raw)


class HuggingFaceEndpointBackend(BaseImageBackend):
    name = "hf_endpoint"

    def __init__(self, *, endpoint_url: str, token: str) -> None:
        self.endpoint_url = endpoint_url.strip()
        self.token = token.strip()

    def generate_png(self, req: ImageGenRequest, *, timeout_s: int = 180) -> bytes:
        if not self.endpoint_url:
            raise ValueError("HF_IMAGE_ENDPOINT_URL missing")
        if not self.token:
            raise ValueError("HF_TOKEN missing")

        pos, neg = render_prompts(req.style, req.subject, req.render_mode)
        if req.positive_prompt:
            pos = req.positive_prompt
        if req.negative_prompt:
            neg = req.negative_prompt

        payload: Dict[str, Any] = {
            "inputs": pos,
            "parameters": {
                "negative_prompt": neg,
                "num_inference_steps": int(req.steps),
                "width": int(req.width),
                "height": int(req.height),
            },
        }
        if req.seed is not None:
            payload["parameters"]["seed"] = int(req.seed)

        headers = {"Authorization": f"Bearer {self.token}"}

        r = requests.post(self.endpoint_url, headers=headers, json=payload, timeout=timeout_s)
        r.raise_for_status()

        ctype = (r.headers.get("Content-Type") or "").lower()
        if ctype.startswith("image/"):
            raw = r.content
            return _ensure_png_bytes(raw)

        # Some endpoints may respond JSON; handle base64 patterns.
        try:
            obj = r.json()
        except Exception:
            # last resort: treat as raw bytes
            return _ensure_png_bytes(r.content)

        # Try common fields
        if isinstance(obj, dict):
            for key in ("image", "generated_image", "result"):
                if key in obj:
                    val = obj[key]
                    if isinstance(val, str):
                        raw = base64.b64decode(val)
                        return _ensure_png_bytes(raw)
                    if isinstance(val, dict) and "image" in val and isinstance(val["image"], str):
                        raw = base64.b64decode(val["image"])
                        return _ensure_png_bytes(raw)

        raise ValueError("HF endpoint response not an image and no decodable base64 field found")


class ComfyUIBackend(BaseImageBackend):
    name = "comfyui"

    def __init__(self, *, base_url: str, workflow_path: Path) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.workflow_path = Path(workflow_path)

    def generate_png(self, req: ImageGenRequest, *, timeout_s: int = 600) -> bytes:
        from .comfy_client import generate_image_bytes  # local import to avoid cycles
        pos, neg = render_prompts(req.style, req.subject, req.render_mode)
        if req.positive_prompt:
            pos = req.positive_prompt
        if req.negative_prompt:
            neg = req.negative_prompt
        seed_basis = req.positive_prompt or req.subject
        seed = req.seed if req.seed is not None else _stable_seed_for_text(seed_basis)
        return generate_image_bytes(
            comfy_url=self.base_url,
            workflow_path=self.workflow_path,
            positive_text=pos,
            negative_text=neg,
            seed=int(seed),
            steps=int(req.steps),
            cfg=6.0,
            timeout_s=timeout_s,
        )


class LocalAssetsOnlyBackend(BaseImageBackend):
    """Sentinel backend used when the user wants all AI image logic disabled."""

    name = "local_assets_only"

    def generate_png(self, req: ImageGenRequest, *, timeout_s: int = 180) -> bytes:
        raise RuntimeError("Local assets only backend does not generate images")


class NoopBackend(BaseImageBackend):
    """Debug backend: returns a placeholder PNG.

    Useful to validate pipeline outputs without any image backend.
    """

    name = "noop"

    def generate_png(self, req: ImageGenRequest, *, timeout_s: int = 180) -> bytes:
        from PIL import Image, ImageDraw, ImageFont

        W, H = int(req.width), int(req.height)
        img = Image.new("RGB", (max(64, W), max(64, H)), (240, 245, 250))
        d = ImageDraw.Draw(img)
        msg = f"NOOP\n{req.render_mode}\n{(req.subject or '')[:64]}"
        try:
            f = ImageFont.load_default()
        except Exception:
            f = None
        d.text((12, 12), msg, fill=(20, 40, 80), font=f)
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()


def backend_from_env() -> Tuple[BaseImageBackend, str]:
    name = (os.environ.get("IMG_BACKEND") or "comfyui").strip().lower()
    if name in ("local_assets_only", "local_assets", "local", "offline"):
        b = LocalAssetsOnlyBackend()
        return b, b.name
    if name in ("none", "noop", "debug"):
        b = NoopBackend()
        return b, b.name
    if name in ("cloudflare", "cloudflare_flux", "cf", "flux"):
        b = CloudflareFluxBackend(
            account_id=os.environ.get("CF_ACCOUNT_ID", ""),
            api_token=os.environ.get("CF_API_TOKEN", ""),
            model=os.environ.get("CF_MODEL", "@cf/black-forest-labs/flux-1-schnell"),
        )
        return b, b.name
    if name in ("hf", "hf_endpoint", "huggingface", "hugging_face"):
        b = HuggingFaceEndpointBackend(
            endpoint_url=os.environ.get("HF_IMAGE_ENDPOINT_URL", os.environ.get("HF_ENDPOINT", "")),
            token=os.environ.get("HF_TOKEN", ""),
        )
        return b, b.name

    # default: comfyui
    b = ComfyUIBackend(
        base_url=os.environ.get("COMFY_URL", "http://127.0.0.1:8188"),
        workflow_path=Path(os.environ.get("COMFY_WORKFLOW", "assets/comfy/workflow_api.json")),
    )
    return b, b.name
