from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from PySide6 import QtCore, QtGui, QtWidgets

# Load .env for the GUI process so os.getenv() can see values when launching the pipeline.
# Safe fallback if python-dotenv is not installed in this environment.
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*args, **kwargs):  # type: ignore
        return False

CONFIG_NAME = "gui_config.json"


def _strip_wrapping_quotes(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1].strip()
    return s


def _clean_env_value(v: Any) -> str:
    return _strip_wrapping_quotes(str(v or "").strip())


def _set_env_if_nonempty(env: Dict[str, str], key: str, value: Any) -> None:
    """Only set env var if value is non-empty; otherwise remove it.

    This prevents the GUI from overwriting .env values with empty strings.
    """
    cleaned = _clean_env_value(value)
    if cleaned:
        env[key] = cleaned
    else:
        env.pop(key, None)


# Style-locked prompt templates (minimizes abstract outputs)
POS_REALISTIC = (
    "photo, highly realistic, natural lighting, sharp focus, detailed textures, clean composition, "
    "centered subject, plain background, product photo style, studio quality\n"
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
    "text, watermark, logo, letters, signature, gibberish typography, misspelled text, nonsense letters"
)


@dataclass
class AppConfig:
    comfy_workdir: str
    comfy_python: str
    comfy_args: List[str]
    comfy_url: str

    project_workdir: str
    project_python: str
    pipeline_script: str

    hf_endpoint: str = "https://hf.co"

    # editor default file (Load/Save target)
    editor_file: str = "homework.txt"

    # audio
    tts_rate_percent: int = -10
    voice_en: str = "en-US-JennyNeural"
    voice_zh: str = "zh-CN-XiaoxiaoNeural"

    # comfy workflow
    comfy_workflow_path: str = "assets/comfy/workflow_api.json"
    picture_cards_type: str = "Realistic"  # Realistic | Cartoon
    lesson_theme: str = "sky"  # sky | sky_tiles | strict_dark | fun_mission (legacy app/strict/fun alias internally)
    picture_reader_theme: str = "fun_mission"
    ocr_backend: str = "auto"
    ocr_device: str = "cpu"

    # image generation backend
    image_backend: str = "comfyui"  # comfyui | cloudflare_flux | hf_endpoint
    img_width: int = 768
    img_height: int = 768
    img_steps: int = 28
    img_timeout_s: int = 600
    img_concurrency: int = 1

    # Cloudflare Workers AI (Flux)
    cf_account_id: str = ""
    cf_api_token: str = ""
    cf_model: str = "@cf/black-forest-labs/flux-1-schnell"

    # Hugging Face image endpoint (dedicated endpoint URL)
    hf_image_endpoint_url: str = ""
    hf_token: str = ""
    hf_guidance: float = 6.0


def _default_config(root_dir: Path) -> AppConfig:
    # Known baseline defaults from your project snapshot
    project_workdir = str(root_dir.resolve())
    project_python = str((root_dir / ".venv" / "Scripts" / "python.exe").resolve())
    return AppConfig(
        comfy_workdir=r"C:\AI\ComfyUI",
        comfy_python=r"C:\AI\ComfyUI\.venv\Scripts\python.exe",
        comfy_args=["main.py", "--listen", "127.0.0.1", "--port", "8188"],
        comfy_url="http://127.0.0.1:8188",
        project_workdir=project_workdir,
        project_python=project_python,
        pipeline_script="run_pipeline.py",
        hf_endpoint="https://hf.co",
        editor_file="homework.txt",
        tts_rate_percent=-16,
        voice_en="en-US-JennyNeural",
        voice_zh="zh-CN-XiaoxiaoNeural",
        comfy_workflow_path=str((root_dir / "assets" / "comfy" / "workflow_api.json").resolve()),
        picture_cards_type="Realistic",
        lesson_theme="sky",
        picture_reader_theme="fun_mission",
        ocr_backend="auto",
        ocr_device="cpu",

        image_backend="comfyui",
        img_width=768,
        img_height=768,
        img_steps=28,
        img_timeout_s=600,
        img_concurrency=1,
        cf_account_id="",
        cf_api_token="",
        cf_model="@cf/black-forest-labs/flux-1-schnell",
        hf_image_endpoint_url="",
        hf_token="",
        hf_guidance=6.0,
    )


def _autofill_cfg_from_env(cfg: "AppConfig") -> None:
    """If sensitive fields are empty in gui_config.json, pull them from env (including .env)."""
    # Cloudflare
    if not _clean_env_value(cfg.cf_account_id):
        v = os.getenv("CF_ACCOUNT_ID", "")
        if _clean_env_value(v):
            cfg.cf_account_id = _clean_env_value(v)

    if not _clean_env_value(cfg.cf_api_token):
        v = os.getenv("CF_API_TOKEN", "")
        if _clean_env_value(v):
            cfg.cf_api_token = _clean_env_value(v)

    v = os.getenv("CF_MODEL", "")
    if _clean_env_value(v) and not _clean_env_value(cfg.cf_model):
        cfg.cf_model = _clean_env_value(v)

    # Hugging Face
    if not _clean_env_value(cfg.hf_image_endpoint_url):
        v = os.getenv("HF_IMAGE_ENDPOINT_URL", os.getenv("HF_ENDPOINT", ""))
        if _clean_env_value(v):
            cfg.hf_image_endpoint_url = _clean_env_value(v)

    if not _clean_env_value(cfg.hf_token):
        v = os.getenv("HF_TOKEN", "")
        if _clean_env_value(v):
            cfg.hf_token = _clean_env_value(v)

    # Optional backend autofill if user prefers env-driven setup
    v = os.getenv("IMG_BACKEND", "")
    if _clean_env_value(v) and (cfg.image_backend or "").strip().lower() in ("", "comfyui"):
        cfg.image_backend = _clean_env_value(v)


def _coerce_list(x: Any, fallback: List[str]) -> List[str]:
    if isinstance(x, list) and all(isinstance(i, (str, int, float)) for i in x):
        return [str(i) for i in x]
    return fallback


def load_config(path: Path, *, root_dir: Path) -> AppConfig:
    """
    Robust loader:
    - If file missing keys, fills defaults from baseline.
    - If file is totally wrong shape, repairs it.
    - Writes repaired config back to disk.
    """
    base = _default_config(root_dir)

    if not path.exists():
        _autofill_cfg_from_env(base)
        save_config(path, base)
        return base

    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        if not isinstance(data, dict):
            raise ValueError("config is not a JSON object")
    except Exception:
        # Hard repair
        _autofill_cfg_from_env(base)
        save_config(path, base)
        return base

    # Fill with defaults
    comfy_workdir = str(data.get("comfy_workdir", base.comfy_workdir))
    comfy_python = str(data.get("comfy_python", base.comfy_python))
    comfy_args = _coerce_list(data.get("comfy_args", base.comfy_args), base.comfy_args)
    comfy_url = str(data.get("comfy_url", base.comfy_url))

    project_workdir = str(data.get("project_workdir", base.project_workdir))
    project_python = str(data.get("project_python", base.project_python))
    pipeline_script = str(data.get("pipeline_script", base.pipeline_script))

    cfg = AppConfig(
        comfy_workdir=comfy_workdir,
        comfy_python=comfy_python,
        comfy_args=comfy_args,
        comfy_url=comfy_url,
        project_workdir=project_workdir,
        project_python=project_python,
        pipeline_script=pipeline_script,
        hf_endpoint=str(data.get("hf_endpoint", base.hf_endpoint)),
        editor_file=str(data.get("editor_file", base.editor_file)),
        tts_rate_percent=int(data.get("tts_rate_percent", base.tts_rate_percent)),
        voice_en=str(data.get("voice_en", base.voice_en)),
        voice_zh=str(data.get("voice_zh", base.voice_zh)),
        comfy_workflow_path=str(data.get("comfy_workflow_path", base.comfy_workflow_path)),
        picture_cards_type=str(data.get("picture_cards_type", base.picture_cards_type)),
        lesson_theme=str(data.get("lesson_theme", base.lesson_theme)).strip().lower() or base.lesson_theme,
        picture_reader_theme=str(data.get("picture_reader_theme", base.picture_reader_theme)).strip().lower() or base.picture_reader_theme,
        ocr_backend=str(data.get("ocr_backend", base.ocr_backend)).strip().lower() or base.ocr_backend,
        ocr_device=str(data.get("ocr_device", base.ocr_device)).strip().lower() or base.ocr_device,
        image_backend=str(data.get("image_backend", base.image_backend)),
        img_width=int(data.get("img_width", base.img_width)),
        img_height=int(data.get("img_height", base.img_height)),
        img_steps=int(data.get("img_steps", base.img_steps)),
        img_timeout_s=int(data.get("img_timeout_s", base.img_timeout_s)),
        img_concurrency=int(data.get("img_concurrency", base.img_concurrency)),
        cf_account_id=_clean_env_value(data.get("cf_account_id", base.cf_account_id)),
        cf_api_token=_clean_env_value(data.get("cf_api_token", base.cf_api_token)),
        cf_model=_clean_env_value(data.get("cf_model", base.cf_model)) or base.cf_model,
        hf_image_endpoint_url=_clean_env_value(
            data.get("hf_image_endpoint_url", data.get("hf_endpoint", base.hf_image_endpoint_url))),
        hf_token=_clean_env_value(data.get("hf_token", base.hf_token)),
        hf_guidance=float(data.get("hf_guidance", base.hf_guidance)),
    )

    _autofill_cfg_from_env(cfg)
    cfg.lesson_theme = _normalize_theme_value(cfg.lesson_theme)
    cfg.picture_reader_theme = _normalize_theme_value(cfg.picture_reader_theme)
    cfg.ocr_backend = (cfg.ocr_backend or "auto").strip().lower() or "auto"
    cfg.ocr_device = (cfg.ocr_device or "cpu").strip().lower() or "cpu"

    # Write back a repaired normalized config (important)
    save_config(path, cfg)
    return cfg


def save_config(path: Path, cfg: AppConfig) -> None:
    data = {
        "comfy_workdir": cfg.comfy_workdir,
        "comfy_python": cfg.comfy_python,
        "comfy_args": cfg.comfy_args,
        "comfy_url": cfg.comfy_url,
        "project_workdir": cfg.project_workdir,
        "project_python": cfg.project_python,
        "pipeline_script": cfg.pipeline_script,
        "hf_endpoint": cfg.hf_endpoint,
        "editor_file": cfg.editor_file,
        "tts_rate_percent": int(cfg.tts_rate_percent),
        "voice_en": str(cfg.voice_en),
        "voice_zh": str(cfg.voice_zh),
        "comfy_workflow_path": str(cfg.comfy_workflow_path),
        "picture_cards_type": str(cfg.picture_cards_type),
        "lesson_theme": str(cfg.lesson_theme),
        "picture_reader_theme": str(cfg.picture_reader_theme),
        "ocr_backend": str(cfg.ocr_backend),
        "ocr_device": str(cfg.ocr_device),
        "image_backend": str(cfg.image_backend),
        "img_width": int(cfg.img_width),
        "img_height": int(cfg.img_height),
        "img_steps": int(cfg.img_steps),
        "img_timeout_s": int(cfg.img_timeout_s),
        "img_concurrency": int(cfg.img_concurrency),
        "cf_account_id": str(cfg.cf_account_id),
        "cf_api_token": str(cfg.cf_api_token),
        "cf_model": str(cfg.cf_model),
        "hf_image_endpoint_url": str(cfg.hf_image_endpoint_url),
        "hf_token": str(cfg.hf_token),
        "hf_guidance": float(cfg.hf_guidance),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _rate_string(rate_percent: int) -> str:
    return f"+{rate_percent}%" if rate_percent >= 0 else f"{rate_percent}%"


def _extract_host_port(url: str) -> Tuple[str, int]:
    u = urlparse(url)
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    return host, port


def _netstat_listening_pids(port: int) -> List[int]:
    try:
        out = subprocess.check_output(["netstat", "-ano", "-p", "TCP"], text=True, errors="ignore")
    except Exception:
        try:
            out = subprocess.check_output(["netstat", "-ano"], text=True, errors="ignore")
        except Exception:
            return []

    target = f":{port}"
    pids: List[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if "TCP" not in line.upper():
            continue
        if target not in line:
            continue
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            pids.append(int(parts[-1]))
        except Exception:
            pass
    return sorted(set(pids))


def _tasklist_name(pid: int) -> str:
    try:
        out = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}"], text=True, errors="ignore")
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if len(lines) >= 3:
            return lines[2].split()[0]
    except Exception:
        pass
    return "unknown"


def comfy_http_reachable(url: str) -> bool:
    try:
        r = requests.get(url, timeout=1.2)
        return r.status_code == 200
    except Exception:
        return False


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8", errors="ignore"))


def _save_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

PUBLISH_SURFACES = {
    "Standard Homework": [("Sky", "sky"), ("Fun Mission", "fun_mission")],
    "Kid Homework": [("Sky Tiles", "sky_tiles")],
    "Older Students": [("Strict Dark", "strict_dark")],
}

THEME_ALIASES = {
    "app": "sky",
    "sky": "sky",
    "sky_tiles": "sky_tiles",
    "strict": "strict_dark",
    "strict_dark": "strict_dark",
    "fun": "fun_mission",
    "fun_mission": "fun_mission",
}

def _normalize_theme_value(value: str) -> str:
    return THEME_ALIASES.get((value or "sky").strip().lower(), "sky")


class PublishPresetDialog(QtWidgets.QDialog):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, current_theme: str = "sky"):
        super().__init__(parent)
        self.setWindowTitle("Publish lesson options")
        self.setModal(True)
        self.resize(460, 220)
        self.setStyleSheet("QDialog{background:#fff7ed;} QLabel{color:#7c2d12;} QComboBox{background:white;border:1px solid #fdba74;border-radius:10px;padding:6px 10px;color:#7c2d12;} QComboBox QAbstractItemView{background:white;color:#7c2d12;selection-background-color:#fed7aa;selection-color:#7c2d12;border:1px solid #fdba74;} QPushButton{background:#fb923c;color:white;border:0;border-radius:10px;padding:7px 12px;font-weight:700;} QPushButton:hover{background:#f97316;}")
        lay = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel("Choose the lesson family and publish surface for this publish run.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        form = QtWidgets.QFormLayout()
        self.combo_family = QtWidgets.QComboBox()
        self.combo_family.addItems(list(PUBLISH_SURFACES.keys()))
        form.addRow("Lesson family:", self.combo_family)

        self.combo_surface = QtWidgets.QComboBox()
        form.addRow("Publish surface:", self.combo_surface)
        lay.addLayout(form)

        note = QtWidgets.QLabel(
            "Sky = balanced standard homework. Sky Tiles = kid image/audio-first. "
            "Strict Dark = older-student, text-first study mode. Fun Mission = guided checkpoint style."
        )
        note.setWordWrap(True)
        lay.addWidget(note)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self.combo_family.currentTextChanged.connect(self._refresh_surfaces)

        current = _normalize_theme_value(current_theme)
        family = "Standard Homework"
        for fam, items in PUBLISH_SURFACES.items():
            if any(key == current for _, key in items):
                family = fam
                break
        self.combo_family.setCurrentText(family)
        self._refresh_surfaces(family)
        for i in range(self.combo_surface.count()):
            if self.combo_surface.itemData(i) == current:
                self.combo_surface.setCurrentIndex(i)
                break

    def _refresh_surfaces(self, family: str) -> None:
        self.combo_surface.clear()
        for label, key in PUBLISH_SURFACES.get(family, []):
            self.combo_surface.addItem(label, key)

    def selected_theme(self) -> str:
        return _normalize_theme_value(str(self.combo_surface.currentData() or "sky"))


def _maybe_reexec_with_configured_python(cfg: AppConfig, root_dir: Path) -> bool:
    """
    Relaunch the GUI with the configured project interpreter if the current process
    was started from the wrong venv/interpreter.
    """
    try:
        current = Path(sys.executable).resolve()
    except Exception:
        current = Path(sys.executable)
    try:
        target = Path(cfg.project_python).resolve()
    except Exception:
        target = Path(cfg.project_python)

    if not target.exists():
        return False

    if os.getenv("SKYED_GUI_REEXEC_DONE", "0") == "1":
        return False

    if str(current).lower() == str(target).lower():
        return False

    env = os.environ.copy()
    env["SKYED_GUI_REEXEC_DONE"] = "1"
    script = str(Path(__file__).resolve())
    subprocess.Popen([str(target), script, *sys.argv[1:]], cwd=str(root_dir), env=env)
    return True


def _run_python_subprocess(
    python_exe: str,
    args: List[str],
    *,
    cwd: str,
    timeout: int = 120,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    proc_env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [python_exe, *args],
        cwd=cwd,
        env=proc_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _list_edge_tts_voices_via_python(python_exe: str, cwd: str, timeout: int = 90) -> Tuple[bool, str]:
    code = r"""
import asyncio, json, edge_tts
async def main():
    voices = await edge_tts.list_voices()
    names = sorted({(v.get("ShortName") or "") for v in voices if (v.get("ShortName") or "")})
    print(json.dumps(names, ensure_ascii=False))
asyncio.run(main())
"""
    cp = _run_python_subprocess(python_exe, ["-c", code], cwd=cwd, timeout=timeout)
    if cp.returncode != 0:
        return False, (cp.stderr or cp.stdout or f"edge_tts voice load failed with code {cp.returncode}").strip()
    return True, cp.stdout.strip()


def _run_edge_tts_cli(
    python_exe: str,
    *,
    text: str,
    out_path: Path,
    voice: str,
    cwd: str,
    timeout: int = 180,
) -> Tuple[bool, str]:
    cp = _run_python_subprocess(
        python_exe,
        ["-m", "edge_tts", "--text", text, "--write-media", str(out_path), "--voice", voice],
        cwd=cwd,
        timeout=timeout,
    )
    if cp.returncode != 0:
        return False, (cp.stderr or cp.stdout or f"edge_tts failed with code {cp.returncode}").strip()
    return True, cp.stdout.strip()


def _is_node_map(d: Dict[str, Any]) -> bool:
    if not isinstance(d, dict) or not d:
        return False
    return all(isinstance(k, str) and k.isdigit() for k in d.keys())


def load_workflow_api_anyshape(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    root = _load_json(path)
    if isinstance(root, dict) and "prompt" in root and isinstance(root["prompt"], dict):
        return root, root["prompt"]
    if isinstance(root, dict) and _is_node_map(root):
        return {"prompt": root}, root
    raise ValueError("Workflow JSON invalid: expected {'prompt': {...}} or bare node-map of nodes.")


def save_workflow_api(path: Path, prompt_dict: Dict[str, Any]) -> None:
    _save_json(path, {"prompt": prompt_dict})


def _node_title_lower(node: Dict[str, Any]) -> str:
    meta = node.get("_meta") if isinstance(node, dict) else None
    if isinstance(meta, dict):
        t = meta.get("title")
        if isinstance(t, str):
            return t.lower()
    return ""


def patch_workflow_prompts_only(
        prompt: Dict[str, Any],
        *,
        positive_text: str,
        negative_text: str,
) -> Dict[str, int]:
    if not isinstance(prompt, dict) or not prompt:
        raise ValueError("Workflow prompt dict is empty/invalid.")

    clip_nodes: List[Tuple[str, Dict[str, Any]]] = []
    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "")
        if ct in ("CLIPTextEncode", "CLIPTextEncodeSDXL"):
            clip_nodes.append((str(node_id), node))

    if not clip_nodes:
        raise ValueError("No CLIPTextEncode nodes found to patch.")

    pos_targets: List[Tuple[str, Dict[str, Any]]] = []
    neg_targets: List[Tuple[str, Dict[str, Any]]] = []

    for nid, n in clip_nodes:
        title = _node_title_lower(n)
        if "neg" in title or "negative" in title:
            neg_targets.append((nid, n))
        elif "pos" in title or "positive" in title or "prompt" in title:
            pos_targets.append((nid, n))

    if not pos_targets:
        pos_targets.append(clip_nodes[0])

    if not neg_targets and len(clip_nodes) >= 2:
        for cand in clip_nodes[1:]:
            if cand[0] != pos_targets[0][0]:
                neg_targets.append(cand)
                break

    def set_text(nodes: List[Tuple[str, Dict[str, Any]]], text: str) -> int:
        c = 0
        for _, n in nodes:
            inp = n.get("inputs")
            if not isinstance(inp, dict):
                n["inputs"] = {}
                inp = n["inputs"]
            inp["text"] = text
            c += 1
        return c

    stats = {"clip_text_pos": 0, "clip_text_neg": 0}
    stats["clip_text_pos"] = set_text(pos_targets, positive_text)
    if neg_targets:
        stats["clip_text_neg"] = set_text(neg_targets, negative_text)
    return stats


class ProcessHarness(QtCore.QObject):
    output = QtCore.Signal(str)
    finished = QtCore.Signal(int)

    def __init__(self, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self.proc = QtCore.QProcess(self)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read_out)
        self.proc.finished.connect(self._on_finished)

    def is_running(self) -> bool:
        return self.proc.state() != QtCore.QProcess.NotRunning

    def start(self, program: str, args: List[str], cwd: str, env: Dict[str, str]) -> None:
        if self.is_running():
            self.output.emit("[Process] Already running.\n")
            return

        process_env = QtCore.QProcessEnvironment.systemEnvironment()
        for k, v in env.items():
            process_env.insert(k, v)

        self.proc.setProcessEnvironment(process_env)
        self.proc.setWorkingDirectory(cwd)
        self.output.emit(f"[Process] START: {program} {' '.join(args)}\n")
        self.proc.start(program, args)

    def terminate(self) -> None:
        if not self.is_running():
            self.output.emit("[Process] Not running.\n")
            return
        self.output.emit("[Process] STOP requested...\n")
        self.proc.terminate()
        if not self.proc.waitForFinished(8000):
            self.output.emit("[Process] terminate timed out -> kill\n")
            self.proc.kill()

    def _on_finished(self, exit_code: int, _status: QtCore.QProcess.ExitStatus) -> None:
        self.finished.emit(int(exit_code))

    def _read_out(self) -> None:
        data = self.proc.readAllStandardOutput().data().decode(errors="ignore")
        if data:
            self.output.emit(data)


class MainWindow(QtWidgets.QMainWindow):
    voices_loaded = QtCore.Signal(object, object)
    voices_failed = QtCore.Signal(str)

    def __init__(self, root_dir: Path, cfg: AppConfig, cfg_path: Path):
        super().__init__()
        self.root_dir = root_dir
        self.cfg = cfg
        self.cfg_path = cfg_path

        self.setWindowTitle("SkyEd Automation — Qt")
        self.resize(1600, 900)
        self.asset_batcher_window = None
        logo_icon = (self.root_dir / "assets" / "branding" / "sky_logo.png").resolve()
        if logo_icon.exists():
            self.setWindowIcon(QtGui.QIcon(str(logo_icon)))

        self.comfy_proc = ProcessHarness(self)
        self.pipe_proc = ProcessHarness(self)
        self.comfy_external_pid: Optional[int] = None

        self.comfy_proc.output.connect(lambda t: self.append_log(t, prefix="[ComfyUI] "))
        self.pipe_proc.output.connect(lambda t: self.append_log(t, prefix="[Pipeline] "))

        self.pipe_proc.finished.connect(self._on_pipeline_finished)

        self.voices_loaded.connect(self._apply_voice_lists)
        self.voices_failed.connect(self._on_voice_error)

        self.status_timer = QtCore.QTimer(self)
        self.status_timer.setInterval(15000)
        self.status_timer.timeout.connect(self.refresh_comfy_status)

        self._error_lines_seen: set[str] = set()
        self._traceback_buffer: List[str] = []
        self._traceback_active: bool = False

        self._build_ui()
        self._apply_branding()

        self.load_default_editor_if_exists()
        self.update_comfy_status_polling(force_refresh=True)
        QtCore.QTimer.singleShot(450, self.load_voices_background)
        QtCore.QTimer.singleShot(0, self.apply_default_sizes)

    def resolve_workflow_path(self) -> Path:
        raw = (self.cfg.comfy_workflow_path or "").strip()
        if not raw:
            return (self.root_dir / "assets" / "comfy" / "workflow_api.json").resolve()
        p = Path(raw)
        if p.is_absolute():
            return p
        return (self.root_dir / p).resolve()

    def _choose_publish_theme(self) -> Optional[Tuple[str, str, str]]:
        selected_theme = _normalize_theme_value(self.cfg.lesson_theme)
        dlg = PublishPresetDialog(parent=self, current_theme=selected_theme)
        dlg.raise_()
        dlg.activateWindow()
        if dlg.exec() != int(QtWidgets.QDialog.DialogCode.Accepted):
            self.append_log("[Publish] cancelled by user\n")
            return None
        selected_theme = dlg.selected_theme()
        if selected_theme == "sky_tiles":
            selected_lesson_mode = "kid_homework"
            selected_surface_variant = "tiles"
        elif selected_theme == "strict_dark":
            selected_lesson_mode = "reading_listening"
            selected_surface_variant = "strict_dark"
        else:
            selected_lesson_mode = "standard_homework"
            selected_surface_variant = "classic"
        self.cfg.lesson_theme = selected_theme
        if hasattr(self, "combo_lesson_theme"):
            self.combo_lesson_theme.setCurrentText(selected_theme)
        save_config(self.cfg_path, self.cfg)
        return selected_theme, selected_lesson_mode, selected_surface_variant

    def run_generate(self) -> None:
        self.run_pipeline("generate")

    def run_generate_publish(self) -> None:
        self.run_pipeline("generate_publish")

    def run_publish_only(self) -> None:
        self.run_pipeline("publish_only")

    def _build_ui(self) -> None:
        tb = QtWidgets.QToolBar("Main")
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(18, 18))
        self.addToolBar(tb)

        tb.setStyleSheet(
            """
            QToolBar { spacing: 8px; padding: 8px 10px; background:#F4F8FC; border-bottom:1px solid #D9E7F5; }
            QToolButton { background:#ffffff; border:1px solid #D7E5F2; border-radius:10px; padding:8px 12px; font-size:10pt; font-weight:700; color:#16324A; }
            QToolButton:hover { border-color:#007BFF; background:#F8FBFF; }
            QToolButton:pressed { background:#EAF4FF; }
            QComboBox { min-height: 32px; padding: 2px 8px; font-size: 10pt; background:#ffffff; border:1px solid #D7E5F2; border-radius:10px; color:#16324A; }
            QSlider { min-height: 28px; }
            QLabel { font-size: 10pt; color:#16324A; }
            """
        )

        def add_btn(text: str, fn) -> QtWidgets.QToolButton:
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.clicked.connect(fn)
            tb.addWidget(b)
            return b

        add_btn("New", self.editor_new)
        add_btn("Load…", self.editor_load)
        add_btn("Save", self.editor_save_default)
        add_btn("Save As…", self.editor_save_as)

        tb.addSeparator()

        tb.addWidget(QtWidgets.QLabel("Picture cards type:"))
        self.combo_picture = QtWidgets.QComboBox()
        self.combo_picture.addItems(["Realistic", "Cartoon"])
        if self.cfg.picture_cards_type in ("Realistic", "Cartoon"):
            self.combo_picture.setCurrentText(self.cfg.picture_cards_type)
        tb.addWidget(self.combo_picture)

        tb.addWidget(QtWidgets.QLabel("Backend:"))
        self.combo_backend = QtWidgets.QComboBox()
        self.combo_backend.addItems(["ComfyUI", "Cloudflare FLUX", "HF Endpoint"])
        # map config -> UI
        _b = (self.cfg.image_backend or "comfyui").lower().strip()
        if _b in ("cloudflare", "cloudflare_flux", "cf", "flux"):
            self.combo_backend.setCurrentText("Cloudflare FLUX")
        elif _b in ("hf", "hf_endpoint", "huggingface", "hugging_face"):
            self.combo_backend.setCurrentText("HF Endpoint")
        else:
            self.combo_backend.setCurrentText("ComfyUI")
        self.combo_backend.currentTextChanged.connect(self.on_backend_changed)
        tb.addWidget(self.combo_backend)

        self.btn_apply_picture = QtWidgets.QToolButton()
        self.btn_apply_picture.setText("Apply → Comfy workflow")
        self.btn_apply_picture.clicked.connect(self.apply_picture_type_to_workflow)
        tb.addWidget(self.btn_apply_picture)
        self.on_backend_changed(self.combo_backend.currentText())

        tb.addSeparator()

        tb.addWidget(QtWidgets.QLabel("Audio speed:"))
        self.lbl_rate = QtWidgets.QLabel(_rate_string(int(self.cfg.tts_rate_percent)))
        tb.addWidget(self.lbl_rate)

        self.slider_rate = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider_rate.setRange(-30, 30)
        self.slider_rate.setFixedWidth(150)
        self.slider_rate.setValue(int(self.cfg.tts_rate_percent))
        self.slider_rate.valueChanged.connect(self.on_rate_changed)
        tb.addWidget(self.slider_rate)

        tb.addWidget(QtWidgets.QLabel("EN:"))
        self.combo_voice_en = QtWidgets.QComboBox()
        self.combo_voice_en.setEditable(True)
        self.combo_voice_en.setMinimumWidth(220)
        self.combo_voice_en.setCurrentText(self.cfg.voice_en)
        self.combo_voice_en.currentTextChanged.connect(self.on_voice_changed)
        tb.addWidget(self.combo_voice_en)

        tb.addWidget(QtWidgets.QLabel("ZH:"))
        self.combo_voice_zh = QtWidgets.QComboBox()
        self.combo_voice_zh.setEditable(True)
        self.combo_voice_zh.setMinimumWidth(220)
        self.combo_voice_zh.setCurrentText(self.cfg.voice_zh)
        self.combo_voice_zh.currentTextChanged.connect(self.on_voice_changed)
        tb.addWidget(self.combo_voice_zh)

        tb.addSeparator()
        add_btn("Generate", lambda: self.run_pipeline(mode="generate"))
        add_btn("Generate + Publish", lambda: self.run_pipeline(mode="generate_publish"))
        add_btn("Asset Batcher", self.open_asset_batcher)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        tb.addWidget(spacer)

        more_btn = QtWidgets.QToolButton()
        more_btn.setText("More ▾")
        more_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        tb.addWidget(more_btn)

        more_menu = QtWidgets.QMenu(self)
        act_refresh_voices = more_menu.addAction("Refresh Voices")
        more_menu.addSeparator()
        act_pub_only = more_menu.addAction("Publish Only")
        act_stop = more_menu.addAction("Stop Pipeline")
        more_menu.addSeparator()
        act_test_en = more_menu.addAction("Test EN")
        act_test_zh = more_menu.addAction("Test ZH")
        more_menu.addSeparator()
        act_refresh = more_menu.addAction("Refresh Comfy")
        act_open = more_menu.addAction("Open Comfy UI")
        act_start = more_menu.addAction("Start Comfy")
        act_stop_comfy = more_menu.addAction("Stop Comfy")

        more_btn.setMenu(more_menu)

        act_refresh_voices.triggered.connect(self.load_voices_background)
        act_pub_only.triggered.connect(self.run_publish_only)
        act_stop.triggered.connect(self.stop_pipeline)
        act_test_en.triggered.connect(lambda: self.test_tts("en"))
        act_test_zh.triggered.connect(lambda: self.test_tts("zh"))
        act_refresh.triggered.connect(self.refresh_comfy_status)
        act_open.triggered.connect(lambda: webbrowser.open(self.cfg.comfy_url))
        act_start.triggered.connect(self.start_comfy)
        act_stop_comfy.triggered.connect(self.stop_comfy)

        tb.addSeparator()
        tb.addWidget(self._build_logo_label(42))

        self.hsplit = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.hsplit.setChildrenCollapsible(False)
        self.setCentralWidget(self.hsplit)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.vsplit = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.vsplit.setChildrenCollapsible(False)
        left_layout.addWidget(self.vsplit)

        self.editor = QtWidgets.QPlainTextEdit()
        self.editor.setPlaceholderText("Paste your homework.txt content here…")
        self.editor.setFont(QtGui.QFont("Consolas", 11))
        self.vsplit.addWidget(self.editor)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QtGui.QFont("Consolas", 10))
        self.log.setPlaceholderText("Live process log…")
        self.vsplit.addWidget(self.log)

        self.error_box = QtWidgets.QPlainTextEdit()
        self.error_box.setReadOnly(True)
        self.error_box.setFont(QtGui.QFont("Consolas", 10))
        self.error_box.setPlaceholderText("Generation errors and reasons will appear here…")
        self.error_box.setStyleSheet("QPlainTextEdit { background:#fff7f7; border:1px solid #e7b4b4; color:#7a1212; }")
        self.vsplit.addWidget(self.error_box)

        self.hsplit.addWidget(left)

        self.right_tabs = QtWidgets.QTabWidget()
        self.right_tabs.setDocumentMode(True)
        self.right_tabs.addTab(self._build_tab_quick_actions(), "Quick")
        self.right_tabs.addTab(self._build_tab_audio(), "Audio")
        self.right_tabs.addTab(self._build_tab_images(), "Images")
        self.right_tabs.addTab(self._build_tab_publish(), "Publish")
        self.right_tabs.addTab(self._build_tab_picture_reader(), "Picture Reader")
        self.hsplit.addWidget(self.right_tabs)

        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")

        wf = self.resolve_workflow_path()
        self.append_log(
            f"GUI ready.\n"
            f"[ENV] Python(current)={sys.executable}\n"
            f"[ENV] Python(configured)={self.cfg.project_python}\n"
            f"[CFG] loaded from: {self.cfg_path}\n"
            f"[CFG] workflow(raw)={self.cfg.comfy_workflow_path}\n"
            f"[CFG] workflow(resolved)={wf} exists={wf.exists()}\n"
        )

    def apply_default_sizes(self) -> None:
        w = self.hsplit.width() or 1600
        left_w = int(w * 0.42)
        self.hsplit.setSizes([left_w, max(200, w - left_w)])
        h = self.vsplit.height() or 900
        editor_h = int(h * 0.60)
        log_h = int(h * 0.24)
        error_h = max(130, h - editor_h - log_h)
        self.vsplit.setSizes([editor_h, log_h, error_h])

    def _group_box(self, title: str) -> QtWidgets.QGroupBox:
        g = QtWidgets.QGroupBox(title)
        g.setStyleSheet("QGroupBox { font-weight: 600; }")
        return g

    def _logo_path(self) -> Path:
        return (self.root_dir / "assets" / "branding" / "sky_logo.png").resolve()

    def _build_logo_label(self, max_size: int = 96) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel()
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(max_size, max_size)
        label.setMaximumSize(max_size, max_size)
        label.setStyleSheet("QLabel{background:transparent;border:0;}")
        logo_path = self._logo_path()
        if logo_path.exists():
            pix = QtGui.QPixmap(str(logo_path))
            if not pix.isNull():
                label.setPixmap(pix.scaled(max_size, max_size, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation))
        return label

    def open_asset_batcher(self) -> None:
        try:
            if self.asset_batcher_window is None:
                from skyed_asset_batcher.app import MainWindow as AssetBatcherMainWindow

                self.asset_batcher_window = AssetBatcherMainWindow(
                    root_dir=self.root_dir,
                    config_file=(self.root_dir / "skyed_batcher_config.json").resolve(),
                )
                self.asset_batcher_window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
                self.asset_batcher_window.destroyed.connect(self._on_asset_batcher_closed)
            self.asset_batcher_window.show()
            self.asset_batcher_window.raise_()
            self.asset_batcher_window.activateWindow()
        except Exception as exc:
            self.append_log(f"[Asset Batcher] launch failed: {type(exc).__name__}: {exc}\n")
            QtWidgets.QMessageBox.critical(
                self,
                "Asset Batcher",
                f"Could not open Asset Batcher.\n\n{type(exc).__name__}: {exc}",
            )

    def _on_asset_batcher_closed(self, *_args) -> None:
        self.asset_batcher_window = None

    def _apply_branding(self) -> None:
        self.setWindowTitle("SkyEd Automation — Studio")
        self.setStyleSheet("""
        QMainWindow { background:#F4F8FC; }
        QTabWidget::pane { border:1px solid #D9E7F5; border-radius:16px; background:#ffffff; }
        QTabBar::tab { background:#EEF5FF; border:1px solid #D7E5F2; padding:10px 16px; margin-right:4px; border-top-left-radius:10px; border-top-right-radius:10px; font-weight:700; color:#16324A; }
        QTabBar::tab:selected { background:#ffffff; color:#007BFF; border-bottom-color:#ffffff; }
        QGroupBox { font-weight:700; border:1px solid #D9E7F5; border-radius:16px; margin-top:12px; background:#ffffff; color:#16324A; }
        QGroupBox::title { subcontrol-origin: margin; left:12px; padding:0 6px; color:#16324A; }
        QPushButton, QToolButton { background:#ffffff; border:1px solid #D7E5F2; border-radius:12px; padding:8px 12px; font-weight:700; color:#16324A; }
        QPushButton:hover, QToolButton:hover { border-color:#007BFF; background:#F8FBFF; }
        QPushButton:pressed, QToolButton:pressed { background:#EAF4FF; }
        QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox { background:#ffffff; border:1px solid #D7E5F2; border-radius:12px; padding:6px 8px; color:#16324A; selection-background-color:#CCE4FF; selection-color:#16324A; }
        QComboBox { background:#ffffff; border:1px solid #D7E5F2; border-radius:12px; padding:6px 10px; color:#16324A; selection-background-color:#CCE4FF; selection-color:#16324A; }
        QComboBox:hover { border-color:#007BFF; }
        QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width:26px; border-left:1px solid #E4EEF8; background:#F8FBFF; border-top-right-radius:12px; border-bottom-right-radius:12px; }
        QComboBox QAbstractItemView { background:#ffffff; color:#16324A; border:1px solid #D7E5F2; selection-background-color:#EAF4FF; selection-color:#16324A; outline:0; }
        QMenu { background:#ffffff; border:1px solid #D7E5F2; color:#16324A; }
        QMenu::item:selected { background:#EAF4FF; color:#16324A; }
        QStatusBar { background:#ffffff; border-top:1px solid #D9E7F5; color:#16324A; }
        QLabel { color:#16324A; }
        QSplitter::handle { background:#E2ECF7; }
        """)

    def _brand_panel(self, title: str, subtitle: str, chips: List[str]) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setStyleSheet(
            "QFrame{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #007BFF, stop:1 #FC7B13);border:0;border-radius:22px;}"
            "QLabel{color:white;background:transparent;border:0;}"
        )
        lay = QtWidgets.QVBoxLayout(frame)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(14)

        text_col = QtWidgets.QVBoxLayout()
        text_col.setSpacing(6)
        ttl = QtWidgets.QLabel(title)
        ttl.setStyleSheet("QLabel{font-size:18px;font-weight:800;color:white;}")
        sub = QtWidgets.QLabel(subtitle)
        sub.setWordWrap(True)
        sub.setStyleSheet("QLabel{font-size:12px;color:rgba(255,255,255,0.96);}")
        text_col.addWidget(ttl)
        text_col.addWidget(sub)

        top_row.addLayout(text_col, 1)
        top_row.addStretch(1)
        top_row.addWidget(self._build_logo_label(108), 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(top_row)

        chip_row = QtWidgets.QHBoxLayout()
        chip_row.setSpacing(8)
        for chip in chips:
            lbl = QtWidgets.QLabel(chip)
            lbl.setStyleSheet(
                "QLabel{background:rgba(255,255,255,0.18);border:1px solid rgba(255,255,255,0.26);"
                "border-radius:999px;padding:6px 10px;font-weight:700;color:white;}"
            )
            chip_row.addWidget(lbl)
        chip_row.addStretch(1)
        lay.addLayout(chip_row)
        return frame

    def _build_tab_quick_actions(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        lesson_host = (_clean_env_value(os.getenv("WP_BASE_URL") or os.getenv("WP_BASE") or "https://skyedu.fun")).rstrip("/") or "https://skyedu.fun"
        quiz_host = _clean_env_value(os.getenv("QUIZ_PUBLIC_BASE") or "https://skyedu.fun/quiz")
        tag_host = _clean_env_value(os.getenv("TAGS_PUBLIC_BASE") or "https://skyedu.fun/tag_s")
        lay.addWidget(self._brand_panel(
            "Sky Education Studio",
            "Generate, publish, and monitor lesson packages without breaking the original workflow. Generate + Publish still opens the publish-surface chooser for that run.",
            [f"WordPress: {lesson_host}", f"Quiz: {quiz_host}", f"tag_s: {tag_host}"]
        ))

        g1 = self._group_box("Pipeline")
        l1 = QtWidgets.QVBoxLayout(g1)
        btn1 = QtWidgets.QPushButton("Generate")
        btn2 = QtWidgets.QPushButton("Generate + Publish")
        btn3 = QtWidgets.QPushButton("Publish Only")
        btn4 = QtWidgets.QPushButton("Picture → Publish")
        btn5 = QtWidgets.QPushButton("Stop Pipeline")
        for b in (btn1, btn2, btn3, btn4, btn5):
            b.setMinimumHeight(36)
            l1.addWidget(b)
        btn1.clicked.connect(self.run_generate)
        btn2.clicked.connect(self.run_generate_publish)
        btn3.clicked.connect(self.run_publish_only)
        btn4.clicked.connect(self.run_picture_reader_publish)
        btn5.clicked.connect(self.stop_pipeline)

        g2 = self._group_box("ComfyUI")
        l2 = QtWidgets.QVBoxLayout(g2)
        bR = QtWidgets.QPushButton("Refresh status")
        bO = QtWidgets.QPushButton("Open UI")
        bS = QtWidgets.QPushButton("Start ComfyUI")
        bT = QtWidgets.QPushButton("Stop ComfyUI")
        for b in (bR, bO, bS, bT):
            b.setMinimumHeight(34)
            l2.addWidget(b)
        bR.clicked.connect(self.refresh_comfy_status)
        bO.clicked.connect(lambda: webbrowser.open(self.cfg.comfy_url))
        bS.clicked.connect(self.start_comfy)
        bT.clicked.connect(self.stop_comfy)

        g3 = self._group_box("Mini apps")
        l3 = QtWidgets.QVBoxLayout(g3)
        btn_batcher = QtWidgets.QPushButton("Open Asset Batcher")
        btn_batcher.setMinimumHeight(36)
        btn_batcher.clicked.connect(self.open_asset_batcher)
        l3.addWidget(btn_batcher)

        lay.addWidget(g1)
        lay.addWidget(g2)
        lay.addWidget(g3)
        lay.addStretch(1)
        return w
    def _build_tab_audio(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        g = self._group_box("Audio settings")
        gl = QtWidgets.QFormLayout(g)

        self.lbl_rate2 = QtWidgets.QLabel(self.lbl_rate.text())
        rate_box = QtWidgets.QHBoxLayout()
        rate_box.addWidget(self.lbl_rate2)
        rate_box.addStretch(1)
        gl.addRow("Rate:", rate_box)

        btn_refresh = QtWidgets.QPushButton("Refresh Voices")
        btn_refresh.setMinimumHeight(34)
        btn_refresh.clicked.connect(self.load_voices_background)

        btn_test_en = QtWidgets.QPushButton("Test EN")
        btn_test_zh = QtWidgets.QPushButton("Test ZH")
        btn_test_en.setMinimumHeight(34)
        btn_test_zh.setMinimumHeight(34)
        btn_test_en.clicked.connect(lambda: self.test_tts("en"))
        btn_test_zh.clicked.connect(lambda: self.test_tts("zh"))

        row = QtWidgets.QHBoxLayout()
        row.addWidget(btn_test_en)
        row.addWidget(btn_test_zh)

        gl.addRow("Voices:", btn_refresh)
        gl.addRow("Preview:", row)

        lay.addWidget(g)
        lay.addStretch(1)
        return w

    def _build_tab_images(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        g = self._group_box("Images")
        l = QtWidgets.QVBoxLayout(g)

        wf = self.resolve_workflow_path()
        info = QtWidgets.QLabel(
            "Workflow used:\n"
            f"{wf}\n\n"
            "Preview shows the latest generated vocab card PNGs from output/<lesson>/cards.\n"
            "Run Generate first if empty."
        )
        info.setWordWrap(True)
        l.addWidget(info)

        # Generator settings (ComfyUI / Cloudflare Flux / HF Endpoint)
        cfg_box = self._group_box("Generator settings")
        fl = QtWidgets.QFormLayout(cfg_box)

        self.combo_backend2 = QtWidgets.QComboBox()
        self.combo_backend2.addItems(["ComfyUI", "Cloudflare FLUX", "HF Endpoint"])
        self.combo_backend2.setCurrentText(self._backend_ui_from_key(self.cfg.image_backend))
        self.combo_backend2.currentTextChanged.connect(self.on_backend2_changed)
        fl.addRow("Backend:", self.combo_backend2)

        self.spin_img_w = QtWidgets.QSpinBox()
        self.spin_img_w.setRange(256, 2048)
        self.spin_img_w.setValue(int(self.cfg.img_width))
        self.spin_img_w.valueChanged.connect(lambda _v: self.on_image_settings_changed())
        fl.addRow("Width:", self.spin_img_w)

        self.spin_img_h = QtWidgets.QSpinBox()
        self.spin_img_h.setRange(256, 2048)
        self.spin_img_h.setValue(int(self.cfg.img_height))
        self.spin_img_h.valueChanged.connect(lambda _v: self.on_image_settings_changed())
        fl.addRow("Height:", self.spin_img_h)

        self.spin_img_steps = QtWidgets.QSpinBox()
        self.spin_img_steps.setRange(1, 100)
        self.spin_img_steps.setValue(int(self.cfg.img_steps))
        self.spin_img_steps.valueChanged.connect(lambda _v: self.on_image_settings_changed())
        fl.addRow("Steps:", self.spin_img_steps)

        self.spin_img_timeout = QtWidgets.QSpinBox()
        self.spin_img_timeout.setRange(30, 3600)
        self.spin_img_timeout.setValue(int(self.cfg.img_timeout_s))
        self.spin_img_timeout.valueChanged.connect(lambda _v: self.on_image_settings_changed())
        fl.addRow("Timeout (s):", self.spin_img_timeout)

        self.spin_img_conc = QtWidgets.QSpinBox()
        self.spin_img_conc.setRange(1, 16)
        self.spin_img_conc.setValue(int(self.cfg.img_concurrency))
        self.spin_img_conc.valueChanged.connect(lambda _v: self.on_image_settings_changed())
        fl.addRow("Concurrency:", self.spin_img_conc)

        # Cloudflare Flux
        self.edit_cf_account = QtWidgets.QLineEdit(self.cfg.cf_account_id)
        self.edit_cf_account.textChanged.connect(lambda _t: self.on_image_settings_changed())
        fl.addRow("CF Account ID:", self.edit_cf_account)

        self.edit_cf_model = QtWidgets.QLineEdit(self.cfg.cf_model)
        self.edit_cf_model.textChanged.connect(lambda _t: self.on_image_settings_changed())
        fl.addRow("CF Model:", self.edit_cf_model)

        self.edit_cf_token = QtWidgets.QLineEdit(self.cfg.cf_api_token)
        self.edit_cf_token.setEchoMode(QtWidgets.QLineEdit.Password)
        self.edit_cf_token.textChanged.connect(lambda _t: self.on_image_settings_changed())
        fl.addRow("CF API Token:", self.edit_cf_token)

        # Hugging Face endpoint
        self.edit_hf_url = QtWidgets.QLineEdit(self.cfg.hf_image_endpoint_url or "")
        self.edit_hf_url.setPlaceholderText("https://<your-endpoint>/")
        self.edit_hf_url.textChanged.connect(lambda _t: self.on_image_settings_changed())
        fl.addRow("HF Endpoint URL:", self.edit_hf_url)

        self.edit_hf_token = QtWidgets.QLineEdit(self.cfg.hf_token)
        self.edit_hf_token.setEchoMode(QtWidgets.QLineEdit.Password)
        self.edit_hf_token.textChanged.connect(lambda _t: self.on_image_settings_changed())
        fl.addRow("HF Token:", self.edit_hf_token)

        self.spin_hf_guidance = QtWidgets.QDoubleSpinBox()
        self.spin_hf_guidance.setRange(1.0, 20.0)
        self.spin_hf_guidance.setSingleStep(0.5)
        self.spin_hf_guidance.setValue(float(self.cfg.hf_guidance))
        self.spin_hf_guidance.valueChanged.connect(lambda _v: self.on_image_settings_changed())
        fl.addRow("HF Guidance:", self.spin_hf_guidance)

        l.addWidget(cfg_box)

        row = QtWidgets.QHBoxLayout()
        btn_refresh = QtWidgets.QPushButton("Refresh Preview")
        btn_open = QtWidgets.QPushButton("Open Latest Output Folder")
        btn_specs = QtWidgets.QPushButton("Open Latest Image Specs")
        btn_report = QtWidgets.QPushButton("Open Latest Image Report")
        btn_clear = QtWidgets.QPushButton("Clear Latest Image Cache")
        for b in (btn_refresh, btn_open, btn_specs, btn_report, btn_clear):
            b.setMinimumHeight(34)
        btn_refresh.clicked.connect(self.refresh_image_preview)
        btn_open.clicked.connect(self.open_latest_output_folder)
        btn_specs.clicked.connect(self.open_latest_image_specs)
        btn_report.clicked.connect(self.open_latest_image_report)
        btn_clear.clicked.connect(self.clear_latest_image_cache)
        row.addWidget(btn_refresh)
        row.addWidget(btn_open)
        row.addWidget(btn_specs)
        row.addWidget(btn_report)
        row.addWidget(btn_clear)
        row.addStretch(1)
        l.addLayout(row)

        self.images_scroll = QtWidgets.QScrollArea()
        self.images_scroll.setWidgetResizable(True)
        self.images_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        self.images_container = QtWidgets.QWidget()
        self.images_grid = QtWidgets.QGridLayout(self.images_container)
        self.images_grid.setContentsMargins(0, 0, 0, 0)
        self.images_grid.setHorizontalSpacing(12)
        self.images_grid.setVerticalSpacing(12)

        self.images_scroll.setWidget(self.images_container)
        l.addWidget(self.images_scroll, 1)

        lay.addWidget(g, 1)
        lay.addStretch(0)
        return w

    def _build_tab_publish(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        wp_base = (_clean_env_value(os.getenv("WP_BASE_URL") or os.getenv("WP_BASE") or "https://skyedu.fun")).rstrip("/") or "https://skyedu.fun"
        quiz_base = _clean_env_value(os.getenv("QUIZ_PUBLIC_BASE") or "https://skyedu.fun/quiz")
        tags_base = _clean_env_value(os.getenv("TAGS_PUBLIC_BASE") or "https://skyedu.fun/tag_s")

        lay.addWidget(self._brand_panel(
            "Publishing Surface",
            "The ECS migration target is now the main Sky Education domain. Publish actions still open a popup so you can choose the surface for that run.",
            [f"Site: {wp_base}", f"Quiz base: {quiz_base}", f"tag_s base: {tags_base}"]
        ))

        g = self._group_box("WordPress")
        fl = QtWidgets.QFormLayout(g)

        lbl_base = QtWidgets.QLabel(f"Base URL: {wp_base}")
        lbl_base.setWordWrap(True)
        fl.addRow(lbl_base)

        self.combo_lesson_theme = QtWidgets.QComboBox()
        self.combo_lesson_theme.addItems(["sky", "fun_mission", "strict_dark", "sky_tiles"])
        self.combo_lesson_theme.setCurrentText(_normalize_theme_value(self.cfg.lesson_theme))
        self.combo_lesson_theme.currentTextChanged.connect(self.on_lesson_theme_changed)
        fl.addRow("Default publish surface:", self.combo_lesson_theme)

        tip = QtWidgets.QLabel(
            "Default surface used when publishing. Publish actions will also open a popup so you can choose Sky, Sky Tiles, Fun Mission, or Strict Dark for that run."
        )
        tip.setWordWrap(True)
        fl.addRow(tip)

        lay.addWidget(g)
        lay.addStretch(1)
        return w


    def _build_tab_picture_reader(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        lay.addWidget(self._brand_panel(
            "Picture Reader Publisher",
            "Upload a bilingual picture page, parse the text, build a mobile-friendly reading frame, and publish it to Sky Education.",
            ["Single mobile reading block", "Touch-to-listen bilingual replay", "WordPress shortcode publishing"]
        ))

        g = self._group_box("Picture Reader")
        fl = QtWidgets.QFormLayout(g)

        self.edit_reader_image = QtWidgets.QLineEdit()
        self.edit_reader_image.setPlaceholderText("Select the source picture with bilingual text...")
        btn_browse = QtWidgets.QPushButton("Browse Image")
        btn_browse.setMinimumHeight(34)
        btn_browse.clicked.connect(self.browse_reader_image)
        row_image = QtWidgets.QHBoxLayout()
        row_image.addWidget(self.edit_reader_image, 1)
        row_image.addWidget(btn_browse)
        fl.addRow("Source image:", row_image)

        self.edit_reader_title = QtWidgets.QLineEdit()
        self.edit_reader_title.setPlaceholderText("Optional page title override")
        fl.addRow("Page title:", self.edit_reader_title)

        self.combo_reader_theme = QtWidgets.QComboBox()
        self.combo_reader_theme.addItems(["fun_mission", "sky", "sky_tiles", "strict_dark"])
        self.combo_reader_theme.setCurrentText(_normalize_theme_value(getattr(self.cfg, "picture_reader_theme", "fun_mission")))
        fl.addRow("Reader theme:", self.combo_reader_theme)

        self.combo_reader_ocr_backend = QtWidgets.QComboBox()
        self.combo_reader_ocr_backend.addItems(["auto", "tesseract", "easyocr", "paddle"])
        self.combo_reader_ocr_backend.setCurrentText((getattr(self.cfg, "ocr_backend", "auto") or "auto").strip().lower())
        fl.addRow("OCR backend:", self.combo_reader_ocr_backend)

        self.combo_reader_ocr_device = QtWidgets.QComboBox()
        self.combo_reader_ocr_device.addItems(["cpu", "cuda"])
        self.combo_reader_ocr_device.setCurrentText((getattr(self.cfg, "ocr_device", "cpu") or "cpu").strip().lower())
        fl.addRow("OCR device:", self.combo_reader_ocr_device)

        note = QtWidgets.QLabel(
            "This mode parses a bilingual image page, keeps the sentence order, builds one mobile reading block, generates touch-to-listen audio for every line, and publishes a phone-first reading page. Fun Mission is the default reader theme. Auto OCR now scores every available backend and keeps the best result."
        )
        note.setWordWrap(True)
        fl.addRow(note)

        btn_publish = QtWidgets.QPushButton("Parse Picture + Publish")
        btn_publish.setMinimumHeight(38)
        btn_publish.clicked.connect(self.run_picture_reader_publish)
        fl.addRow(btn_publish)

        lay.addWidget(g)
        lay.addStretch(1)
        return w

    def _find_latest_lesson_dir(self) -> Optional[Path]:
        out_root = self.root_dir / "output"
        if not out_root.exists():
            return None
        candidates: List[Path] = []
        for p in out_root.rglob("cards"):
            if p.is_dir():
                candidates.append(p.parent)
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    def open_latest_output_folder(self) -> None:
        p = self._find_latest_lesson_dir()
        if not p:
            QtWidgets.QMessageBox.information(self, "No output", "No output lesson folder found under ./output")
            return
        try:
            os.startfile(str(p))  # type: ignore[attr-defined]
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Open failed", str(e))

    def _open_latest_artifact(self, relative_path: str, label: str) -> None:
        lesson_dir = self._find_latest_lesson_dir()
        if not lesson_dir:
            QtWidgets.QMessageBox.information(self, "No output", "No output lesson folder found under ./output")
            return
        target = lesson_dir / relative_path
        if not target.exists():
            QtWidgets.QMessageBox.information(self, label, f"File not found:\n{target}")
            return
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, label, str(e))

    def open_latest_image_specs(self) -> None:
        self._open_latest_artifact("cards/image_specs.json", "Image Specs")

    def open_latest_image_report(self) -> None:
        self._open_latest_artifact("cards/image_report.json", "Image Report")

    def clear_latest_image_cache(self) -> None:
        lesson_dir = self._find_latest_lesson_dir()
        if not lesson_dir:
            QtWidgets.QMessageBox.information(self, "No output", "No output lesson folder found under ./output")
            return

        removed: List[str] = []
        targets = [
            lesson_dir / "cards" / "ai",
            lesson_dir / "cards" / "image_specs.json",
            lesson_dir / "cards" / "image_report.json",
            lesson_dir / "cards" / "image_plans.json",
            lesson_dir / "cards" / "ai_status.txt",
        ]
        for target in targets:
            if not target.exists():
                continue
            try:
                if target.is_dir():
                    import shutil
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink()
                removed.append(str(target))
            except Exception as e:
                self.append_log(f"[Images] Failed to remove {target}: {e}\n")

        if removed:
            self.append_log("[Images] Cleared latest image cache:\n" + "\n".join(removed) + "\n")
            QtWidgets.QMessageBox.information(self, "Image cache cleared", "Removed latest image AI cache/spec/report files.")
        else:
            QtWidgets.QMessageBox.information(self, "Image cache", "No image cache/spec/report files found in the latest lesson folder.")

    def _clear_images_grid(self) -> None:
        if not hasattr(self, "images_grid"):
            return
        while self.images_grid.count():
            it = self.images_grid.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def refresh_image_preview(self) -> None:
        lesson_dir = self._find_latest_lesson_dir()
        self._clear_images_grid()
        if not lesson_dir:
            self.append_log("[Images] No lesson output found for preview.\n")
            return

        cards_dir = lesson_dir / "cards"
        imgs = sorted(
            [p for p in cards_dir.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not imgs:
            self.append_log(f"[Images] No images in: {cards_dir}\n")
            return

        col_count = 2
        r = 0
        c = 0
        for p in imgs:
            frame = QtWidgets.QFrame()
            frame.setStyleSheet("QFrame{background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;}")
            v = QtWidgets.QVBoxLayout(frame)
            v.setContentsMargins(10, 10, 10, 10)
            v.setSpacing(8)

            lbl_img = QtWidgets.QLabel()
            lbl_img.setAlignment(QtCore.Qt.AlignCenter)
            lbl_img.setMinimumHeight(150)
            pix = QtGui.QPixmap(str(p))
            if not pix.isNull():
                lbl_img.setPixmap(
                    pix.scaled(520, 320, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                )
            else:
                lbl_img.setText("(image load failed)")

            lbl_name = QtWidgets.QLabel(p.name)
            lbl_name.setWordWrap(True)
            lbl_name.setStyleSheet("QLabel{font-weight:600;}")

            v.addWidget(lbl_img)
            v.addWidget(lbl_name)

            self.images_grid.addWidget(frame, r, c)
            c += 1
            if c >= col_count:
                c = 0
                r += 1

        self.append_log(f"[Images] Preview loaded: {len(imgs)} image(s) from {cards_dir}\n")

    def _on_pipeline_finished(self, exit_code: int) -> None:
        self.append_log(f"[Pipeline] FINISHED exit_code={exit_code}\n")
        QtCore.QTimer.singleShot(200, self.refresh_image_preview)

    def _clear_error_box(self) -> None:
        if hasattr(self, "error_box") and self.error_box is not None:
            self.error_box.clear()
        self._error_lines_seen.clear()
        self._traceback_buffer = []
        self._traceback_active = False

    def _append_error_line(self, line: str) -> None:
        clean = line.rstrip("\r\n")
        if not clean:
            return
        if clean in self._error_lines_seen:
            return
        self._error_lines_seen.add(clean)
        if hasattr(self, "error_box") and self.error_box is not None:
            self.error_box.moveCursor(QtGui.QTextCursor.End)
            self.error_box.insertPlainText(clean + "\n")
            self.error_box.moveCursor(QtGui.QTextCursor.End)

    def append_log(self, text: str, prefix: str = "") -> None:
        if not text:
            return
        if prefix:
            lines = text.splitlines(True)
            text = "".join(prefix + ln for ln in lines)

        # _build_ui() can call methods before self.log is created
        if not hasattr(self, "log") or self.log is None:
            return

        self.log.moveCursor(QtGui.QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QtGui.QTextCursor.End)

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            low = line.lower()
            if line.startswith("Traceback"):
                self._traceback_active = True
                self._traceback_buffer = [line]
                self._append_error_line(line)
                continue
            if self._traceback_active:
                self._traceback_buffer.append(line)
                self._append_error_line(line)
                if line.startswith(("AttributeError:", "RuntimeError:", "ValueError:", "TypeError:", "Exception:", "FileNotFoundError:", "ModuleNotFoundError:")):
                    self._traceback_active = False
                continue
            if "error" in low or "traceback" in low or "exception" in low:
                self._append_error_line(line)

    def default_editor_path(self) -> Path:
        p = Path(self.cfg.editor_file)
        if not p.is_absolute():
            p = (self.root_dir / p).resolve()
        return p

    def load_default_editor_if_exists(self) -> None:
        p = self.default_editor_path()
        if p.exists():
            self.editor.setPlainText(p.read_text(encoding="utf-8", errors="ignore"))
            self.status.showMessage(f"Loaded: {p.name}")
        else:
            self.status.showMessage(f"Ready (default file not found: {p.name})")

    def editor_new(self) -> None:
        self.editor.setPlainText("")
        self.status.showMessage("Editor cleared")

    def editor_load(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load homework text", str(self.root_dir), "Text Files (*.txt);;All Files (*.*)"
        )
        if not path:
            return
        p = Path(path)
        self.editor.setPlainText(p.read_text(encoding="utf-8", errors="ignore"))
        self.cfg.editor_file = str(p)
        save_config(self.cfg_path, self.cfg)
        self.status.showMessage(f"Loaded: {p.name}")

    def editor_save_default(self) -> None:
        p = self.default_editor_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.editor.toPlainText().rstrip() + "\n", encoding="utf-8")
        self.status.showMessage(f"Saved: {p.name}")

    def editor_save_as(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save homework as", str(self.root_dir), "Text Files (*.txt);;All Files (*.*)"
        )
        if not path:
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.editor.toPlainText().rstrip() + "\n", encoding="utf-8")
        self.cfg.editor_file = str(p)
        save_config(self.cfg_path, self.cfg)
        self.status.showMessage(f"Saved: {p.name}")

    def on_rate_changed(self, v: int) -> None:
        self.cfg.tts_rate_percent = int(v)
        s = _rate_string(int(v))
        self.lbl_rate.setText(s)
        if hasattr(self, "lbl_rate2"):
            self.lbl_rate2.setText(s)
        save_config(self.cfg_path, self.cfg)

    def on_voice_changed(self, _txt: str) -> None:
        self.cfg.voice_en = self.combo_voice_en.currentText().strip()
        self.cfg.voice_zh = self.combo_voice_zh.currentText().strip()
        save_config(self.cfg_path, self.cfg)

    def _backend_key_from_ui(self, txt: str) -> str:
        t = (txt or "").strip().lower()
        if "cloudflare" in t or "flux" in t:
            return "cloudflare_flux"
        if "hf" in t or "hugging" in t:
            return "hf_endpoint"
        return "comfyui"

    def _backend_ui_from_key(self, key: str) -> str:
        k = (key or "").strip().lower()
        if k in ("cloudflare", "cloudflare_flux", "cf", "flux"):
            return "Cloudflare FLUX"
        if k in ("hf", "hf_endpoint", "huggingface", "hugging_face"):
            return "HF Endpoint"
        return "ComfyUI"

    def on_backend_changed(self, _txt: str) -> None:
        # toolbar change → config + sync image tab widgets
        self.cfg.image_backend = self._backend_key_from_ui(self.combo_backend.currentText())
        save_config(self.cfg_path, self.cfg)

        # enable/disable workflow patch button
        b = (self.cfg.image_backend or "comfyui").lower().strip()
        if hasattr(self, "btn_apply_picture"):
            self.btn_apply_picture.setEnabled(b in ("comfyui", "comfy"))

        # sync secondary combo if present
        if hasattr(self, "combo_backend2"):
            ui = self._backend_ui_from_key(self.cfg.image_backend)
            if self.combo_backend2.currentText() != ui:
                self.combo_backend2.blockSignals(True)
                self.combo_backend2.setCurrentText(ui)
                self.combo_backend2.blockSignals(False)

        self.append_log(f"[Images] backend set to {self.cfg.image_backend}\n")
        self.update_comfy_status_polling(force_refresh=True)

    def on_backend2_changed(self, _txt: str) -> None:
        # image tab combo → sync toolbar combo then reuse handler
        ui = self.combo_backend2.currentText()
        if self.combo_backend.currentText() != ui:
            self.combo_backend.blockSignals(True)
            self.combo_backend.setCurrentText(ui)
            self.combo_backend.blockSignals(False)
        self.on_backend_changed(ui)

    def _is_comfy_backend_selected(self) -> bool:
        return (self.cfg.image_backend or "comfyui").lower().strip() in ("comfyui", "comfy")

    def update_comfy_status_polling(self, force_refresh: bool = False) -> None:
        if not hasattr(self, "status_timer"):
            return

        if self._is_comfy_backend_selected():
            if self.status_timer.interval() != 15000:
                self.status_timer.setInterval(15000)
            if not self.status_timer.isActive():
                self.status_timer.start()
            if force_refresh:
                self.refresh_comfy_status()
            return

        if self.status_timer.isActive():
            self.status_timer.stop()
        if hasattr(self, "status"):
            self.status.showMessage(
                f"ComfyUI status polling paused (image backend: {self.cfg.image_backend})"
            )

    def _capture_image_tab_settings(self) -> None:
        # Called before running pipeline; safe if tab widgets not yet created.
        try:
            if hasattr(self, "spin_img_w"):
                self.cfg.img_width = int(self.spin_img_w.value())
            if hasattr(self, "spin_img_h"):
                self.cfg.img_height = int(self.spin_img_h.value())
            if hasattr(self, "spin_img_steps"):
                self.cfg.img_steps = int(self.spin_img_steps.value())
            if hasattr(self, "spin_img_timeout"):
                self.cfg.img_timeout_s = int(self.spin_img_timeout.value())
            if hasattr(self, "spin_img_conc"):
                self.cfg.img_concurrency = int(self.spin_img_conc.value())
            if hasattr(self, "edit_cf_account"):
                self.cfg.cf_account_id = _clean_env_value(self.edit_cf_account.text())
            if hasattr(self, "edit_cf_model"):
                self.cfg.cf_model = _clean_env_value(self.edit_cf_model.text()) or self.cfg.cf_model
            if hasattr(self, "edit_cf_token"):
                self.cfg.cf_api_token = _clean_env_value(self.edit_cf_token.text())
            if hasattr(self, "edit_hf_url"):
                self.cfg.hf_image_endpoint_url = _clean_env_value(self.edit_hf_url.text())
            if hasattr(self, "edit_hf_token"):
                self.cfg.hf_token = _clean_env_value(self.edit_hf_token.text())
            if hasattr(self, "spin_hf_guidance"):
                self.cfg.hf_guidance = float(self.spin_hf_guidance.value())
        except Exception:
            pass

    def on_image_settings_changed(self) -> None:
        self._capture_image_tab_settings()
        save_config(self.cfg_path, self.cfg)

    def on_lesson_theme_changed(self, _txt: str) -> None:
        if hasattr(self, "combo_lesson_theme"):
            value = self.combo_lesson_theme.currentText().strip().lower()
            self.cfg.lesson_theme = _normalize_theme_value(value)
            save_config(self.cfg_path, self.cfg)
            self.append_log(f"[Publish] lesson theme set to {self.cfg.lesson_theme}\n")

    def load_voices_background(self) -> None:
        self.append_log(f"[Audio] loading voice list via: {self.cfg.project_python}\n")

        def worker():
            try:
                ok, payload = _list_edge_tts_voices_via_python(
                    self.cfg.project_python,
                    self.cfg.project_workdir,
                    timeout=90,
                )
                if not ok:
                    self.voices_failed.emit(f"{payload}\nPython={self.cfg.project_python}")
                    return
                names = json.loads(payload)
                if not isinstance(names, list):
                    raise ValueError("voice list payload is not a list")
                self.voices_loaded.emit(names, names)
            except Exception as e:
                self.voices_failed.emit(f"{e}\nPython={self.cfg.project_python}")

        threading.Thread(target=worker, daemon=True).start()

    @QtCore.Slot(object, object)
    def _apply_voice_lists(self, en_list: object, zh_list: object) -> None:
        if not isinstance(en_list, list):
            self.append_log("[Audio] voice list payload mismatch\n")
            return
        cur_en = self.combo_voice_en.currentText()
        cur_zh = self.combo_voice_zh.currentText()
        self.combo_voice_en.blockSignals(True)
        self.combo_voice_zh.blockSignals(True)
        self.combo_voice_en.clear()
        self.combo_voice_en.addItems(en_list)
        self.combo_voice_en.setEditable(True)
        self.combo_voice_en.setCurrentText(cur_en or self.cfg.voice_en)
        self.combo_voice_zh.clear()
        self.combo_voice_zh.addItems(en_list)
        self.combo_voice_zh.setEditable(True)
        self.combo_voice_zh.setCurrentText(cur_zh or self.cfg.voice_zh)
        self.combo_voice_en.blockSignals(False)
        self.combo_voice_zh.blockSignals(False)
        self.append_log(f"[Audio] voice list loaded: {len(en_list)}\n")

    @QtCore.Slot(str)
    def _on_voice_error(self, err: str) -> None:
        self.append_log(f"[Audio] voice list load failed: {err}\n")

    def test_tts(self, lang: str) -> None:
        voice = self.combo_voice_en.currentText().strip() if lang == "en" else self.combo_voice_zh.currentText().strip()
        rate_str = _rate_string(int(self.cfg.tts_rate_percent))
        sample_text = (
            "Hello. This is Sky Education audio test. One, two, three."
            if lang == "en"
            else "你好。这是思恺教育音频测试。一二三。"
        )
        out_dir = (self.root_dir / "output" / "_tts_preview").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"tts_test_{lang}_{int(time.time())}.mp3"

        def worker():
            try:
                import edge_tts  # type: ignore

                async def synth():
                    communicate = edge_tts.Communicate(sample_text, voice=voice, rate=rate_str)
                    await communicate.save(str(out_path))

                asyncio.run(synth())
                self.append_log(f"[Audio] test generated: {out_path}\n")
                try:
                    os.startfile(str(out_path))  # type: ignore[attr-defined]
                except Exception:
                    pass
            except Exception as e:
                self.append_log(f"[Audio] test failed: {e}\n")

        threading.Thread(target=worker, daemon=True).start()

    def refresh_comfy_status(self) -> None:
        _, port = _extract_host_port(self.cfg.comfy_url)
        pids = _netstat_listening_pids(port)
        http_ok = comfy_http_reachable(self.cfg.comfy_url)
        if pids:
            name = _tasklist_name(pids[0])
            self.status.showMessage(
                f"ComfyUI LISTENING on {port} (PID {pids[0]}:{name}) • HTTP={'OK' if http_ok else 'NO'}"
            )
        else:
            self.status.showMessage("ComfyUI reachable" if http_ok else "ComfyUI not reachable")

    def auto_start_comfy_if_needed(self) -> None:
        """
        Auto-start is intentionally disabled.

        We still check status on launch so the user can see whether ComfyUI is
        already running, but we never launch it automatically. Manual controls
        (Start Comfy / Stop Comfy / Open Comfy UI) remain the same.
        """
        if comfy_http_reachable(self.cfg.comfy_url):
            self.append_log("[ComfyUI] already reachable. Auto-start on GUI launch is disabled.\n")
            return

        _, port = _extract_host_port(self.cfg.comfy_url)
        if _netstat_listening_pids(port):
            self.append_log("[ComfyUI] LISTENING detected. Auto-start on GUI launch is disabled.\n")
            return

        self.append_log("[ComfyUI] auto-start on GUI launch is disabled. Use Start Comfy when needed.\n")

    def start_comfy(self) -> None:
        if comfy_http_reachable(self.cfg.comfy_url):
            self.append_log("[ComfyUI] reachable → NOT starting a second instance.\n")
            return
        env = os.environ.copy()

        # Persist current toolbar selections
        self.cfg.picture_cards_type = self.combo_picture.currentText().strip() or self.cfg.picture_cards_type
        self.cfg.image_backend = self._backend_key_from_ui(self.combo_backend.currentText())
        # Persist image tab settings if available
        self._capture_image_tab_settings()
        save_config(self.cfg_path, self.cfg)

        # Pass config to pipeline via env
        env["PICTURE_CARDS_TYPE"] = self.cfg.picture_cards_type
        env["IMG_BACKEND"] = self.cfg.image_backend
        env["IMG_WIDTH"] = str(int(self.cfg.img_width))
        env["IMG_HEIGHT"] = str(int(self.cfg.img_height))
        env["IMG_STEPS"] = str(int(self.cfg.img_steps))
        env["IMG_TIMEOUT_S"] = str(int(self.cfg.img_timeout_s))
        env["IMG_CONCURRENCY"] = str(int(self.cfg.img_concurrency))

        _set_env_if_nonempty(env, "CF_ACCOUNT_ID", self.cfg.cf_account_id)
        _set_env_if_nonempty(env, "CF_API_TOKEN", self.cfg.cf_api_token)
        _set_env_if_nonempty(env, "CF_MODEL", self.cfg.cf_model)

        _set_env_if_nonempty(env, "HF_IMAGE_ENDPOINT_URL", self.cfg.hf_image_endpoint_url or "")
        _set_env_if_nonempty(env, "HF_TOKEN", self.cfg.hf_token)
        env["HF_GUIDANCE"] = str(self.cfg.hf_guidance)

        # Keep backward-compatible key
        env["HF_ENDPOINT"] = self.cfg.hf_endpoint
        self.comfy_proc.start(self.cfg.comfy_python, self.cfg.comfy_args, self.cfg.comfy_workdir, env)
        QtCore.QTimer.singleShot(1200, self.refresh_comfy_status)

    def stop_comfy(self) -> None:
        self.comfy_proc.terminate()
        QtCore.QTimer.singleShot(250, self.refresh_comfy_status)

    def apply_picture_type_to_workflow(self) -> None:
        sel = self.combo_picture.currentText().strip()
        if sel not in ("Realistic", "Cartoon"):
            return
        self.cfg.picture_cards_type = sel
        save_config(self.cfg_path, self.cfg)

        # If we are not using ComfyUI for images, we still keep picture_cards_type for prompt templates,
        # but we do NOT patch the workflow file.
        b = (self.cfg.image_backend or "comfyui").lower().strip()
        if b not in ("comfyui", "comfy"):
            self.append_log("[Images] picture_cards_type saved; workflow patch skipped (backend is not ComfyUI)\n")
            return

        wf_path = self.resolve_workflow_path()
        if not wf_path.exists():
            QtWidgets.QMessageBox.critical(self, "Workflow not found", f"Not found:\n{wf_path}")
            return

        pos = POS_REALISTIC if sel == "Realistic" else POS_CARTOON
        neg = NEG_ALWAYS

        try:
            original_obj = _load_json(wf_path)
            backup = wf_path.with_suffix(f".bak_{_timestamp()}.json")
            _save_json(backup, original_obj)

            _, prompt = load_workflow_api_anyshape(wf_path)
            stats = patch_workflow_prompts_only(prompt, positive_text=pos, negative_text=neg)
            save_workflow_api(wf_path, prompt)

            self.append_log(
                f"[Images] Applied Picture cards type={sel}\n"
                f"         workflow: {wf_path}\n"
                f"         backup:   {backup.name}\n"
                f"         saved as: {{'prompt': ...}}\n"
                f"         patched:  pos_nodes={stats.get('clip_text_pos')} neg_nodes={stats.get('clip_text_neg')}\n"
            )
        except Exception as e:
            self.append_log(f"[Images] apply failed: {e}\n")
            QtWidgets.QMessageBox.critical(self, "Apply failed", str(e))


    def browse_reader_image(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select bilingual picture",
            str(self.root_dir),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)"
        )
        if path:
            self.edit_reader_image.setText(path)
            self.status.showMessage(f"Picture selected: {Path(path).name}")

    def run_picture_reader_publish(self) -> None:
        script = self._pipeline_script_path()
        if not script.exists():
            QtWidgets.QMessageBox.critical(self, "Missing pipeline script", f"Not found:\n{script}")
            return
        if not hasattr(self, "edit_reader_image"):
            QtWidgets.QMessageBox.warning(self, "Picture Reader", "Picture Reader controls are not available.")
            return
        image_path = Path(self.edit_reader_image.text().strip()).expanduser()
        if not image_path.exists():
            QtWidgets.QMessageBox.warning(self, "Picture Reader", "Please select an existing image file first.")
            return
        title = self.edit_reader_title.text().strip()
        selected_theme = _normalize_theme_value(self.combo_reader_theme.currentText() if hasattr(self, "combo_reader_theme") else getattr(self.cfg, "picture_reader_theme", "fun_mission"))
        selected_ocr_backend = (self.combo_reader_ocr_backend.currentText().strip().lower() if hasattr(self, "combo_reader_ocr_backend") else getattr(self.cfg, "ocr_backend", "auto")) or "auto"
        selected_ocr_device = (self.combo_reader_ocr_device.currentText().strip().lower() if hasattr(self, "combo_reader_ocr_device") else getattr(self.cfg, "ocr_device", "cpu")) or "cpu"
        self.cfg.picture_reader_theme = selected_theme
        self.cfg.ocr_backend = selected_ocr_backend
        self.cfg.ocr_device = selected_ocr_device
        save_config(self.cfg_path, self.cfg)

        env = os.environ.copy()
        env["HF_ENDPOINT"] = self.cfg.hf_endpoint
        env["SKYED_TTS_RATE"] = _rate_string(int(self.cfg.tts_rate_percent))
        env["SKYED_VOICE_EN"] = str(self.cfg.voice_en)
        env["SKYED_VOICE_ZH"] = str(self.cfg.voice_zh)
        wf = self.resolve_workflow_path()
        env["COMFY_URL"] = str(self.cfg.comfy_url)
        env["COMFY_WORKFLOW"] = str(wf)

        selected_surface_variant = "classic"
        if selected_theme == "sky_tiles":
            selected_surface_variant = "tiles"
        elif selected_theme == "strict_dark":
            selected_surface_variant = "strict_dark"

        args: List[str] = [
            str(script),
            "--page-kind", "picture_reader",
            "--input-image", str(image_path),
            "--theme", selected_theme,
            "--lesson-mode", "standard_homework",
            "--surface-variant", selected_surface_variant,
            "--ocr-backend", selected_ocr_backend,
            "--ocr-device", selected_ocr_device,
            "--publish",
        ]
        if title:
            args.extend(["--reader-title", title])

        self.append_log(f"[PictureReader] IMAGE={image_path}\n")
        self.append_log(f"[PictureReader] THEME={selected_theme} OCR={selected_ocr_backend}/{selected_ocr_device}\n")
        if title:
            self.append_log(f"[PictureReader] TITLE={title}\n")
        self.pipe_proc.start(self.cfg.project_python, args, self.cfg.project_workdir, env)

    def _pipeline_script_path(self) -> Path:
        return (Path(self.cfg.project_workdir) / self.cfg.pipeline_script).resolve()

    def run_pipeline(self, mode: str) -> None:
        self.editor_save_default()
        self._clear_error_box()
        script = self._pipeline_script_path()
        if not script.exists():
            QtWidgets.QMessageBox.critical(self, "Missing pipeline script", f"Not found:\n{script}")
            return

        selected_theme = _normalize_theme_value(self.cfg.lesson_theme)
        selected_lesson_mode = "standard_homework"
        selected_surface_variant = "classic"

        if mode in ("generate_publish", "publish_only"):
            chosen = self._choose_publish_theme()
            if not chosen:
                return
            selected_theme, selected_lesson_mode, selected_surface_variant = chosen
        else:
            if hasattr(self, "combo_lesson_theme"):
                value = self.combo_lesson_theme.currentText().strip().lower()
                selected_theme = _normalize_theme_value(value)
                self.cfg.lesson_theme = selected_theme
                save_config(self.cfg_path, self.cfg)

        input_path = str(self.default_editor_path())
        args: List[str] = [
            str(script),
            "--input", input_path,
            "--theme", selected_theme,
            "--lesson-mode", selected_lesson_mode,
            "--surface-variant", selected_surface_variant,
        ]
        if mode == "generate_publish":
            args.append("--publish")
        elif mode == "publish_only":
            args.append("--publish-only")
            # Backward-compatible: older pipeline versions require --publish to actually publish.
            args.append("--publish")

        env = os.environ.copy()
        env["HF_ENDPOINT"] = self.cfg.hf_endpoint
        env["SKYED_TTS_RATE"] = _rate_string(int(self.cfg.tts_rate_percent))
        env["SKYED_VOICE_EN"] = str(self.cfg.voice_en)
        env["SKYED_VOICE_ZH"] = str(self.cfg.voice_zh)

        # Persist current toolbar selections + image settings
        self.cfg.picture_cards_type = self.combo_picture.currentText().strip() or self.cfg.picture_cards_type
        self.cfg.image_backend = self._backend_key_from_ui(self.combo_backend.currentText())
        self._capture_image_tab_settings()
        save_config(self.cfg_path, self.cfg)

        # Pass image backend config to pipeline via env
        env["PICTURE_CARDS_TYPE"] = self.cfg.picture_cards_type
        env["IMG_BACKEND"] = self.cfg.image_backend
        env["IMG_WIDTH"] = str(int(self.cfg.img_width))
        env["IMG_HEIGHT"] = str(int(self.cfg.img_height))
        env["IMG_STEPS"] = str(int(self.cfg.img_steps))
        env["IMG_TIMEOUT_S"] = str(int(self.cfg.img_timeout_s))
        env["IMG_CONCURRENCY"] = str(int(self.cfg.img_concurrency))
        env["IMG_MAX_RETRIES"] = str(int(os.getenv("IMG_MAX_RETRIES", "2")))

        _set_env_if_nonempty(env, "CF_ACCOUNT_ID", self.cfg.cf_account_id)
        _set_env_if_nonempty(env, "CF_API_TOKEN", self.cfg.cf_api_token)
        _set_env_if_nonempty(env, "CF_MODEL", self.cfg.cf_model)

        _set_env_if_nonempty(env, "HF_IMAGE_ENDPOINT_URL", self.cfg.hf_image_endpoint_url or "")
        _set_env_if_nonempty(env, "HF_TOKEN", self.cfg.hf_token)
        env["HF_GUIDANCE"] = str(self.cfg.hf_guidance)

        # ensure pipeline uses same workflow path
        wf = self.resolve_workflow_path()
        env["COMFY_URL"] = str(self.cfg.comfy_url)
        env["COMFY_WORKFLOW"] = str(wf)

        self.append_log(f"[Images] COMFY_WORKFLOW={wf} exists={wf.exists()}\n")
        self.append_log(
            f"[Images] BACKEND={self.cfg.image_backend} STYLE={self.cfg.picture_cards_type} "
            f"W={self.cfg.img_width} H={self.cfg.img_height} STEPS={self.cfg.img_steps} "
            f"TIMEOUT={self.cfg.img_timeout_s}s CONCURRENCY={self.cfg.img_concurrency}\n"
        )
        self.append_log(f"[Publish] THEME={selected_theme} MODE={selected_lesson_mode} SURFACE={selected_surface_variant}\n")
        self.pipe_proc.start(self.cfg.project_python, args, self.cfg.project_workdir, env)

    def stop_pipeline(self) -> None:
        self.pipe_proc.terminate()


def main() -> None:
    root_dir = Path(__file__).resolve().parent
    os.chdir(root_dir)

    # Load .env for the GUI process so env-based autofill works.
    load_dotenv(dotenv_path=str(root_dir / ".env"), override=False)

    cfg_path = root_dir / CONFIG_NAME

    # Robust load (repairs config if needed)
    cfg = load_config(cfg_path, root_dir=root_dir)
    if not Path(cfg.project_workdir).exists():
        cfg.project_workdir = str(root_dir)
    if not Path(cfg.project_python).exists():
        cfg.project_python = str((root_dir / ".venv" / "Scripts" / "python.exe").resolve())
    save_config(cfg_path, cfg)

    # Do not silently re-exec into another interpreter here.
    # That can make the original process exit with code 0 while no GUI becomes visible
    # if the second launch fails. Keep the current interpreter for reliable startup.
    # if _maybe_reexec_with_configured_python(cfg, root_dir):
    #     return

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow(root_dir, cfg, cfg_path)
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
