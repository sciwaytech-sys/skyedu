from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict

import requests


def load_workflow(path: Path) -> Dict[str, dict]:
    """
    Loads ComfyUI API prompt JSON.

    Accepts:
      1) { "1": {class_type..., inputs...}, ... }
      2) { "prompt": { "1": {...}, ... } }

    Rejects UI workflow exports that contain "nodes"/"links".
    """
    obj: Any = json.loads(path.read_text(encoding="utf-8"))

    # Wrapper format
    if isinstance(obj, dict) and isinstance(obj.get("prompt"), dict):
        prompt = obj["prompt"]
        if _looks_like_prompt(prompt):
            return prompt
        raise RuntimeError("workflow_api.json has 'prompt' but it doesn't look like an API prompt dict.")

    # Direct prompt dict
    if isinstance(obj, dict) and _looks_like_prompt(obj):
        return obj

    # Common UI workflow export shape
    if isinstance(obj, dict) and ("nodes" in obj or "links" in obj):
        raise RuntimeError(
            "workflow_api.json appears to be a UI workflow export (contains 'nodes'/'links'). "
            "You must export/save in API format so it becomes a dict of node_id -> {class_type, inputs}."
        )

    raise RuntimeError(
        "workflow_api.json is not in ComfyUI API prompt format. "
        "Expected dict of node_id -> node dict (or a wrapper with key 'prompt')."
    )


def _looks_like_prompt(d: dict) -> bool:
    # At least one value should be a dict containing 'class_type'
    for v in d.values():
        if isinstance(v, dict) and "class_type" in v and isinstance(v.get("inputs", {}), dict):
            return True
    return False


def patch_workflow(
    workflow: Dict[str, dict],
    positive: str,
    negative: str,
    seed: int = 42,
    steps: int | None = None,
    cfg: float | None = None,
) -> Dict[str, dict]:
    pos_found = False
    neg_found = False

    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue

        if inputs.get("text") == "__POS__":
            inputs["text"] = positive
            pos_found = True
        if inputs.get("text") == "__NEG__":
            inputs["text"] = negative
            neg_found = True

    if not (pos_found and neg_found):
        raise RuntimeError(
            "Could not find __POS__/__NEG__ placeholders in workflow. "
            "Set your positive prompt text to __POS__ and negative to __NEG__ before exporting API JSON."
        )

    # Patch first KSampler we find
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == "KSampler":
            k = node.get("inputs", {})
            if isinstance(k, dict):
                if "seed" in k:
                    k["seed"] = seed
                if steps is not None and "steps" in k:
                    k["steps"] = steps
                if cfg is not None and "cfg" in k:
                    k["cfg"] = cfg
            break

    return workflow


def queue_prompt(comfy_url: str, workflow: Dict[str, dict]) -> str:
    client_id = str(uuid.uuid4())
    r = requests.post(
        f"{comfy_url}/prompt",
        json={"prompt": workflow, "client_id": client_id},
        timeout=60,
    )
    r.raise_for_status()
    j = r.json()
    return j["prompt_id"]


def download_first_image(comfy_url: str, prompt_id: str, out_path: Path, timeout_s: int = 600) -> Path:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        h = requests.get(f"{comfy_url}/history/{prompt_id}", timeout=30).json()

        entry = None
        if isinstance(h, dict):
            if prompt_id in h and isinstance(h[prompt_id], dict):
                entry = h[prompt_id]
            elif h:
                entry = next(iter(h.values()))
        if isinstance(entry, dict):
            outputs = entry.get("outputs", {})
            if isinstance(outputs, dict):
                for node_out in outputs.values():
                    if not isinstance(node_out, dict):
                        continue
                    imgs = node_out.get("images", [])
                    if not isinstance(imgs, list):
                        continue
                    for im in imgs:
                        if not isinstance(im, dict) or "filename" not in im:
                            continue
                        params = {
                            "filename": im["filename"],
                            "subfolder": im.get("subfolder", ""),
                            "type": im.get("type", "output"),
                        }
                        data = requests.get(f"{comfy_url}/view", params=params, timeout=120).content
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        out_path.write_bytes(data)
                        return out_path

        time.sleep(1)

    raise TimeoutError("ComfyUI generation timed out")


def generate_image_for_word(
    comfy_url: str,
    workflow_path: Path,
    word: str,
    out_path: Path,
    seed: int = 42,
    steps: int = 28,
    cfg: float = 6.0,
    timeout_s: int = 600,
) -> Path:
    positive = (
        f"high quality children's book illustration of {word}, centered, clean background, "
        f"bright colors, simple shapes, soft shadow, no text, no watermark"
    )
    negative = "text, watermark, logo, letters, blurry, low quality, noise, deformed"

    wf = load_workflow(workflow_path)
    wf = patch_workflow(wf, positive=positive, negative=negative, seed=seed, steps=steps, cfg=cfg)

    pid = queue_prompt(comfy_url, wf)
    return download_first_image(comfy_url, pid, out_path, timeout_s=timeout_s)
