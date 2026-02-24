# skyed/comfy_client.py
from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple, Optional

import requests


def load_workflow_api_anyshape(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("workflow_api.json must be an object/dict")

    if "prompt" in obj and isinstance(obj.get("prompt"), dict):
        return obj, obj["prompt"]

    # exported workflows are often the prompt dict directly (numeric node keys)
    return obj, obj


def _find_ksampler_nodes(prompt: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in prompt.items():
        if isinstance(v, dict) and v.get("class_type") == "KSampler":
            out[str(k)] = v
    return out


def patch_workflow_prompts_only(prompt: Dict[str, Any], *, positive_text: str, negative_text: str) -> Dict[str, int]:
    """
    Deterministically patch CLIPTextEncode nodes used by the KSampler wiring.
    We DO NOT use node titles or UI names.

    We patch:
      - node referenced by KSampler.inputs.positive -> inputs.text = positive_text
      - node referenced by KSampler.inputs.negative -> inputs.text = negative_text
    """
    ks = _find_ksampler_nodes(prompt)
    pos_patched = 0
    neg_patched = 0

    def _patch_ref(ref: Any, text: str) -> int:
        if not (isinstance(ref, (list, tuple)) and len(ref) >= 1):
            return 0
        node_id = str(ref[0])
        node = prompt.get(node_id)
        if not isinstance(node, dict):
            return 0
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            return 0
        if "text" in inputs:
            inputs["text"] = text
            return 1
        return 0

    for _kid, kv in ks.items():
        inputs = kv.get("inputs", {})
        if not isinstance(inputs, dict):
            continue
        pos_patched += _patch_ref(inputs.get("positive"), positive_text)
        neg_patched += _patch_ref(inputs.get("negative"), negative_text)

    return {"clip_text_pos": pos_patched, "clip_text_neg": neg_patched, "ksamplers": len(ks)}


def _post_prompt(comfy_url: str, prompt: Dict[str, Any], *, timeout_s: int) -> str:
    payload = {"prompt": prompt, "client_id": str(uuid.uuid4())}
    r = requests.post(f"{comfy_url.rstrip('/')}/prompt", json=payload, timeout=timeout_s)
    r.raise_for_status()
    data = r.json()
    pid = data.get("prompt_id") or data.get("promptId") or data.get("id")
    if not pid:
        raise ValueError(f"ComfyUI /prompt response missing prompt_id: {data}")
    return str(pid)


def _poll_history(comfy_url: str, prompt_id: str, *, timeout_s: int) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    url = f"{comfy_url.rstrip('/')}/history/{prompt_id}"
    last: Optional[Dict[str, Any]] = None
    while time.time() < deadline:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        # ComfyUI often returns {prompt_id: {...}}
        if isinstance(data, dict) and prompt_id in data and isinstance(data[prompt_id], dict):
            last = data[prompt_id]
        elif isinstance(data, dict):
            last = data
        else:
            last = None

        if last and isinstance(last, dict):
            outputs = last.get("outputs")
            if isinstance(outputs, dict) and outputs:
                return last
        time.sleep(0.6)
    raise TimeoutError(f"Timed out waiting for ComfyUI history {prompt_id}")


def _extract_first_image_meta(history_obj: Dict[str, Any]) -> Dict[str, Any]:
    outputs = history_obj.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("ComfyUI history missing outputs")
    for _node_id, out in outputs.items():
        if not isinstance(out, dict):
            continue
        imgs = out.get("images")
        if isinstance(imgs, list) and imgs:
            meta = imgs[0]
            if isinstance(meta, dict) and meta.get("filename"):
                return meta
    raise ValueError("No images found in ComfyUI outputs")


def _download_image(comfy_url: str, meta: Dict[str, Any], *, timeout_s: int) -> bytes:
    params = {
        "filename": meta.get("filename", ""),
        "subfolder": meta.get("subfolder", ""),
        "type": meta.get("type", "output"),
    }
    r = requests.get(f"{comfy_url.rstrip('/')}/view", params=params, timeout=timeout_s)
    r.raise_for_status()
    return r.content


def generate_image_bytes(
    *,
    comfy_url: str,
    workflow_path: Path,
    positive_text: str,
    negative_text: str,
    seed: int = 42,
    steps: int = 28,
    cfg: float = 6.0,
    timeout_s: int = 600,
) -> bytes:
    """Generate an image from a workflow json and return raw bytes."""
    _obj, prompt = load_workflow_api_anyshape(Path(workflow_path))
    prompt2: Dict[str, Any] = copy.deepcopy(prompt)

    # Patch prompts deterministically via KSampler wiring.
    patch_workflow_prompts_only(prompt2, positive_text=positive_text, negative_text=negative_text)

    # Patch KSampler numeric params if present (safe: only if keys exist)
    for _nid, node in list(prompt2.items()):
        if isinstance(node, dict) and node.get("class_type") == "KSampler":
            inputs = node.get("inputs", {})
            if isinstance(inputs, dict):
                if "seed" in inputs:
                    inputs["seed"] = int(seed)
                if "steps" in inputs:
                    inputs["steps"] = int(steps)
                if "cfg" in inputs:
                    inputs["cfg"] = float(cfg)

    prompt_id = _post_prompt(comfy_url, prompt2, timeout_s=timeout_s)
    hist = _poll_history(comfy_url, prompt_id, timeout_s=timeout_s)
    meta = _extract_first_image_meta(hist)
    return _download_image(comfy_url, meta, timeout_s=timeout_s)


def generate_image_for_word(
    *,
    comfy_url: str,
    workflow_path: Path,
    word: str,
    out_path: Path,
    seed: int = 42,
    steps: int = 28,
    cfg: float = 6.0,
    timeout_s: int = 600,
    style: str = "cartoon",
) -> Path:
    from .prompt_templates import render_prompts
    pos, neg = render_prompts(style, word)
    raw = generate_image_bytes(
        comfy_url=comfy_url,
        workflow_path=Path(workflow_path),
        positive_text=pos,
        negative_text=neg,
        seed=seed,
        steps=steps,
        cfg=cfg,
        timeout_s=timeout_s,
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    return out_path
