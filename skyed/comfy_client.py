from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# -----------------------------------------------------------------------------
# ComfyUI client (HTTP)
# -----------------------------------------------------------------------------
# This module provides a single stable entry point used by skyed.cards:
#   generate_image_for_word(...)
#
# It supports workflow JSON exported from ComfyUI:
#   - {"prompt": {...}}  (preferred)
#   - {...}              (bare node-map; will be wrapped on submit)
#
# It patches prompts deterministically via KSampler wiring:
#   KSampler.inputs.positive -> CLIPTextEncode node id
#   KSampler.inputs.negative -> CLIPTextEncode node id
#
# Placeholders supported in positive prompt text:
#   {subject} , ${subject} , __POS__
# -----------------------------------------------------------------------------

POS_REALISTIC_TEMPLATE = (
    "photo, highly realistic, natural lighting, sharp focus, detailed textures, clean composition, "
    "centered subject, plain white background, product photo style, studio quality, soft shadow\n"
    "SUBJECT: {subject} (single object, isolated, centered, no clutter, no room, no scene)\n"
    "NO TEXT, NO LOGO"
)

POS_CARTOON_TEMPLATE = (
    "cute cartoon illustration, clean lines, flat shading, bright cheerful colors, children book style, "
    "simple white background, centered subject, clear silhouette\n"
    "SUBJECT: {subject} (single object, isolated, centered, no clutter, no room, no scene)\n"
    "NO TEXT, NO LOGO"
)

NEG_DEFAULT = (
    "abstract, surreal, cubism, expressionism, glitch, noise, watercolor, oil painting, sketch, lineart, "
    "lowpoly, 3d render, blurry, low quality, worst quality, messy background, "
    "deformed, disfigured, mutated, extra limbs, bad anatomy, bad hands, "
    "text, watermark, logo, letters, signature, frame, border, collage, "
    "people, person, face, body, hands, fingers, building, room, landscape, scenery, furniture set"
)


class ComfyUIError(RuntimeError):
    pass


def _base_url(comfy_url: str) -> str:
    u = (comfy_url or "").strip().rstrip("/")
    if not u:
        raise ValueError("comfy_url is empty.")
    return u


def _load_workflow_anyshape(workflow_path: Path) -> Dict[str, Any]:
    if not workflow_path.exists():
        raise FileNotFoundError(f"workflow_path not found: {workflow_path}")
    root = json.loads(workflow_path.read_text(encoding="utf-8"))
    if isinstance(root, dict) and "prompt" in root and isinstance(root["prompt"], dict):
        prompt = root["prompt"]
    else:
        prompt = root
    if not isinstance(prompt, dict) or not prompt:
        raise ValueError("Workflow JSON invalid: expected {'prompt': {...}} or bare node-map.")
    return prompt


def _is_clip_node(node: Dict[str, Any]) -> bool:
    ct = str(node.get("class_type") or "")
    return ct in ("CLIPTextEncode", "CLIPTextEncodeSDXL")


def _is_ksampler_node(node: Dict[str, Any]) -> bool:
    ct = str(node.get("class_type") or "")
    return ct in ("KSampler", "KSamplerAdvanced")


def _resolve_ref_node_id(ref: Any) -> Optional[str]:
    # ComfyUI links are usually ["<node_id>", <output_index>]
    if isinstance(ref, (list, tuple)) and ref:
        return str(ref[0])
    if isinstance(ref, str) and ref:
        return ref
    return None


def _substitute_subject(text: str, subject: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    s = subject.strip()
    # Support multiple placeholder styles.
    return text.replace("{subject}", s).replace("${subject}", s).replace("__POS__", s)


def _pick_style_template(style: str) -> str:
    st = (style or "").strip().lower()
    return POS_REALISTIC_TEMPLATE if st.startswith("real") else POS_CARTOON_TEMPLATE


def _should_override_template(current_text: str, style: str) -> bool:
    """
    Conservative override:
    - If current prompt explicitly contradicts chosen style (contains 'cartoon' vs 'photo'),
      override with our template.
    - Otherwise keep the workflow's prompt and only substitute placeholders.
    """
    t = (current_text or "").lower()
    st = (style or "").lower()
    if st.startswith("real"):
        return ("cartoon" in t) and ("photo" not in t)
    return ("photo" in t) and ("cartoon" not in t)


def _patch_clip_text(prompt: Dict[str, Any], node_id: str, text: str) -> bool:
    node = prompt.get(str(node_id))
    if not isinstance(node, dict):
        return False
    if not _is_clip_node(node):
        return False
    inp = node.get("inputs")
    if not isinstance(inp, dict):
        node["inputs"] = {}
        inp = node["inputs"]
    inp["text"] = text
    return True


def _patch_ksampler_params(node: Dict[str, Any], seed: Optional[int], steps: Optional[int], cfg: Optional[float]) -> None:
    inp = node.get("inputs")
    if not isinstance(inp, dict):
        node["inputs"] = {}
        inp = node["inputs"]
    if seed is not None:
        inp["seed"] = int(seed)
    if steps is not None:
        inp["steps"] = int(steps)
    if cfg is not None:
        inp["cfg"] = float(cfg)


def _patch_prompt_for_subject(
    prompt: Dict[str, Any],
    *,
    subject: str,
    style: str,
    seed: Optional[int],
    steps: Optional[int],
    cfg: Optional[float],
    negative_text: Optional[str],
) -> Dict[str, int]:
    """
    Returns stats:
      {
        "ksampler": <count>,
        "clip_pos": <count>,
        "clip_neg": <count>,
      }
    """
    neg = (negative_text or NEG_DEFAULT).strip()
    stats = {"ksampler": 0, "clip_pos": 0, "clip_neg": 0}

    # Find KSampler nodes and patch via wiring for determinism.
    ks_nodes: List[Tuple[str, Dict[str, Any]]] = []
    for nid, node in prompt.items():
        if isinstance(node, dict) and _is_ksampler_node(node):
            ks_nodes.append((str(nid), node))

    # Fallback: if no KSampler found, patch ALL CLIP nodes (best-effort).
    if not ks_nodes:
        template = _pick_style_template(style)
        pos_text = _substitute_subject(template, subject)
        for nid, node in prompt.items():
            if not (isinstance(node, dict) and _is_clip_node(node)):
                continue
            current = ""
            if isinstance(node.get("inputs"), dict):
                current = str(node["inputs"].get("text", ""))
            # Heuristic: if current contains obvious "negative" keywords, treat as neg.
            if "worst quality" in current.lower() or "watermark" in current.lower() or "negative" in current.lower():
                if _patch_clip_text(prompt, str(nid), neg):
                    stats["clip_neg"] += 1
            else:
                if _patch_clip_text(prompt, str(nid), pos_text):
                    stats["clip_pos"] += 1
        return stats

    template = _pick_style_template(style)

    for _, ks_node in ks_nodes:
        stats["ksampler"] += 1
        _patch_ksampler_params(ks_node, seed=seed, steps=steps, cfg=cfg)

        inp = ks_node.get("inputs") if isinstance(ks_node, dict) else None
        if not isinstance(inp, dict):
            continue

        pos_ref = _resolve_ref_node_id(inp.get("positive"))
        neg_ref = _resolve_ref_node_id(inp.get("negative"))

        # Patch positive
        if pos_ref and isinstance(prompt.get(pos_ref), dict):
            pos_node = prompt[pos_ref]
            current = ""
            if isinstance(pos_node.get("inputs"), dict):
                current = str(pos_node["inputs"].get("text", ""))
            pos_text = template if (not current or _should_override_template(current, style)) else current
            pos_text = _substitute_subject(pos_text, subject)
            if _patch_clip_text(prompt, pos_ref, pos_text):
                stats["clip_pos"] += 1

        # Patch negative
        if neg_ref and _patch_clip_text(prompt, neg_ref, neg):
            stats["clip_neg"] += 1

    return stats


def _submit_prompt(base: str, prompt: Dict[str, Any], timeout_s: int) -> str:
    payload = {"prompt": prompt, "client_id": str(uuid.uuid4())}
    r = requests.post(f"{base}/prompt", json=payload, timeout=min(60, max(10, timeout_s)))
    if r.status_code != 200:
        raise ComfyUIError(f"ComfyUI /prompt failed: HTTP {r.status_code} - {r.text[:500]}")
    data = r.json()
    pid = data.get("prompt_id") or data.get("promptId") or data.get("id")
    if not pid:
        raise ComfyUIError(f"ComfyUI /prompt returned no prompt_id. Response keys: {list(data.keys())}")
    return str(pid)


def _poll_history_for_image(base: str, prompt_id: str, timeout_s: int) -> Dict[str, str]:
    """Return image descriptor: {'filename','subfolder','type'}."""
    deadline = time.time() + max(5, int(timeout_s))
    last_err: Optional[str] = None

    while time.time() < deadline:
        try:
            r = requests.get(f"{base}/history/{prompt_id}", timeout=30)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                time.sleep(1.0)
                continue

            hist = r.json()

            entry = None
            if isinstance(hist, dict):
                entry = hist.get(prompt_id)
                if not isinstance(entry, dict) and hist:
                    # sometimes history may be keyed differently; try first value
                    entry = next(iter(hist.values()))
            if not isinstance(entry, dict):
                time.sleep(1.0)
                continue

            outputs = entry.get("outputs")
            if not isinstance(outputs, dict) or not outputs:
                time.sleep(1.0)
                continue

            for node_out in outputs.values():
                if not isinstance(node_out, dict):
                    continue
                images = node_out.get("images")
                if isinstance(images, list) and images:
                    img0 = images[0]
                    if isinstance(img0, dict) and img0.get("filename"):
                        return {
                            "filename": str(img0.get("filename")),
                            "subfolder": str(img0.get("subfolder") or ""),
                            "type": str(img0.get("type") or "output"),
                        }

            time.sleep(0.75)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(1.0)

    raise TimeoutError(f"Timed out waiting for ComfyUI image. prompt_id={prompt_id}. last_err={last_err}")


def _download_image(base: str, image_desc: Dict[str, str]) -> bytes:
    params = {
        "filename": image_desc["filename"],
        "subfolder": image_desc.get("subfolder", ""),
        "type": image_desc.get("type", "output"),
    }
    r = requests.get(f"{base}/view", params=params, timeout=60)
    if r.status_code != 200:
        raise ComfyUIError(f"ComfyUI /view failed: HTTP {r.status_code} - {r.text[:200]}")
    return r.content


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
) -> None:
    """Generate one image for `word` and write it to `out_path`."""
    base = _base_url(comfy_url)

    prompt = _load_workflow_anyshape(Path(workflow_path))
    prompt = copy.deepcopy(prompt)

    _patch_prompt_for_subject(
        prompt,
        subject=word,
        style=style,
        seed=seed,
        steps=steps,
        cfg=cfg,
        negative_text=NEG_DEFAULT,
    )

    prompt_id = _submit_prompt(base, prompt, timeout_s=timeout_s)
    img_desc = _poll_history_for_image(base, prompt_id, timeout_s=timeout_s)
    data = _download_image(base, img_desc)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
