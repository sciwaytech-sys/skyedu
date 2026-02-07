from __future__ import annotations

import asyncio
import json
import os
import queue
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


CONFIG_NAME = "gui_config.json"

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
    "text, watermark, logo, letters, signature"
)


# ---------------- Config ----------------

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


def load_config(path: Path) -> AppConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    return AppConfig(
        comfy_workdir=data["comfy_workdir"],
        comfy_python=data["comfy_python"],
        comfy_args=data["comfy_args"],
        comfy_url=data["comfy_url"],
        project_workdir=data["project_workdir"],
        project_python=data["project_python"],
        pipeline_script=data["pipeline_script"],
        hf_endpoint=data.get("hf_endpoint", "https://hf.co"),
        editor_file=str(data.get("editor_file", "homework.txt")),
        tts_rate_percent=int(data.get("tts_rate_percent", -10)),
        voice_en=str(data.get("voice_en", "en-US-JennyNeural")),
        voice_zh=str(data.get("voice_zh", "zh-CN-XiaoxiaoNeural")),
        comfy_workflow_path=str(data.get("comfy_workflow_path", "assets/comfy/workflow_api.json")),
        picture_cards_type=str(data.get("picture_cards_type", "Realistic")),
    )


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
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- Utilities ----------------

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
    """
    Windows netstat parser. Returns PIDs that are LISTENING on the given local port.
    TIME_WAIT is ignored (not a blocker).
    """
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
        pid_str = parts[-1]
        try:
            pids.append(int(pid_str))
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


def _get_prompt_dict(workflow: Dict[str, Any]) -> Dict[str, Any]:
    if "prompt" in workflow and isinstance(workflow["prompt"], dict):
        return workflow["prompt"]
    return workflow


def _node_title_lower(node: Dict[str, Any]) -> str:
    meta = node.get("_meta") if isinstance(node, dict) else None
    if isinstance(meta, dict):
        t = meta.get("title")
        if isinstance(t, str):
            return t.lower()
    return ""


def patch_workflow_prompts_only(
    workflow_obj: Dict[str, Any],
    *,
    positive_text: str,
    negative_text: str,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """
    Minimal-safe patch:
      - Updates CLIPTextEncode / CLIPTextEncodeSDXL inputs.text for positive/negative.
    Heuristic:
      - if node title includes 'negative' -> negative
      - if node title includes 'positive' -> positive
      - else first encode = positive, second encode = negative (if exists)
    """
    prompt = _get_prompt_dict(workflow_obj)
    if not isinstance(prompt, dict) or not prompt:
        raise ValueError("Workflow JSON does not look like ComfyUI API workflow (missing prompt dict).")

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
        elif "pos" in title or "positive" in title:
            pos_targets.append((nid, n))

    if not pos_targets:
        pos_targets.append(clip_nodes[0])
    if not neg_targets and len(clip_nodes) >= 2:
        neg_targets.append(clip_nodes[1])

    def set_text(nodes: List[Tuple[str, Dict[str, Any]]], text: str) -> int:
        c = 0
        for _, n in nodes:
            inp = n.get("inputs")
            if isinstance(inp, dict):
                inp["text"] = text
                c += 1
        return c

    stats = {"clip_text_pos": 0, "clip_text_neg": 0}
    stats["clip_text_pos"] = set_text(pos_targets, positive_text)
    if neg_targets:
        stats["clip_text_neg"] = set_text(neg_targets, negative_text)

    return workflow_obj, stats


# ---------------- Qt helpers ----------------

class LogEmitter(QtCore.QObject):
    line = QtCore.Signal(str)


class ProcessHarness(QtCore.QObject):
    """
    Wrapper around QProcess to run a process and stream output lines.
    """
    started = QtCore.Signal()
    finished = QtCore.Signal(int)
    output = QtCore.Signal(str)

    def __init__(self, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self.proc = QtCore.QProcess(self)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read_out)
        self.proc.started.connect(self.started)
        self.proc.finished.connect(self._finished)

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

    def _read_out(self) -> None:
        data = self.proc.readAllStandardOutput().data().decode(errors="ignore")
        if data:
            self.output.emit(data)

    def _finished(self, exit_code: int, _status: QtCore.QProcess.ExitStatus) -> None:
        self.output.emit(f"[Process] EXIT: {exit_code}\n")
        self.finished.emit(exit_code)


# ---------------- Main Window ----------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, root_dir: Path, cfg: AppConfig, cfg_path: Path):
        super().__init__()
        self.root_dir = root_dir
        self.cfg = cfg
        self.cfg_path = cfg_path

        self.setWindowTitle("SkyEd Automation — Qt")
        self.resize(1450, 900)

        # Processes
        self.comfy_proc = ProcessHarness(self)
        self.pipe_proc = ProcessHarness(self)
        self.comfy_external_pid: Optional[int] = None

        # Wire logs
        self.comfy_proc.output.connect(lambda t: self.append_log(t, prefix="[ComfyUI] "))
        self.pipe_proc.output.connect(lambda t: self.append_log(t, prefix="[Pipeline] "))

        # UI
        self._build_ui()
        self._apply_dark_hint(False)

        # Timers
        self.status_timer = QtCore.QTimer(self)
        self.status_timer.timeout.connect(self.refresh_comfy_status)
        self.status_timer.start(2500)

        # initial load
        self.load_default_editor_if_exists()

        # try auto-start comfy if needed
        QtCore.QTimer.singleShot(700, self.auto_start_comfy_if_needed)

        # load voices in background
        QtCore.QTimer.singleShot(500, self.load_voices_background)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        # Toolbar
        tb = QtWidgets.QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(18, 18))
        self.addToolBar(tb)

        # File actions
        act_new = QtGui.QAction("New", self)
        act_new.triggered.connect(self.editor_new)
        tb.addAction(act_new)

        act_load = QtGui.QAction("Load…", self)
        act_load.triggered.connect(self.editor_load)
        tb.addAction(act_load)

        act_save = QtGui.QAction("Save", self)
        act_save.triggered.connect(self.editor_save_default)
        tb.addAction(act_save)

        act_save_as = QtGui.QAction("Save As…", self)
        act_save_as.triggered.connect(self.editor_save_as)
        tb.addAction(act_save_as)

        tb.addSeparator()

        # Picture cards type + Apply
        tb.addWidget(QtWidgets.QLabel("Picture cards type: "))
        self.combo_picture = QtWidgets.QComboBox()
        self.combo_picture.addItems(["Realistic", "Cartoon"])
        if self.cfg.picture_cards_type in ("Realistic", "Cartoon"):
            self.combo_picture.setCurrentText(self.cfg.picture_cards_type)
        tb.addWidget(self.combo_picture)

        btn_apply = QtWidgets.QToolButton()
        btn_apply.setText("Apply → Comfy workflow")
        btn_apply.clicked.connect(self.apply_picture_type_to_workflow)
        tb.addWidget(btn_apply)

        tb.addSeparator()

        # Audio controls
        tb.addWidget(QtWidgets.QLabel("Audio speed: "))
        self.lbl_rate = QtWidgets.QLabel(_rate_string(int(self.cfg.tts_rate_percent)))
        tb.addWidget(self.lbl_rate)

        self.slider_rate = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider_rate.setRange(-30, 30)
        self.slider_rate.setFixedWidth(140)
        self.slider_rate.setValue(int(self.cfg.tts_rate_percent))
        self.slider_rate.valueChanged.connect(self.on_rate_changed)
        tb.addWidget(self.slider_rate)

        tb.addWidget(QtWidgets.QLabel(" EN: "))
        self.combo_voice_en = QtWidgets.QComboBox()
        self.combo_voice_en.setEditable(True)
        self.combo_voice_en.setMinimumWidth(230)
        self.combo_voice_en.setCurrentText(self.cfg.voice_en)
        self.combo_voice_en.currentTextChanged.connect(self.on_voice_changed)
        tb.addWidget(self.combo_voice_en)

        tb.addWidget(QtWidgets.QLabel(" ZH: "))
        self.combo_voice_zh = QtWidgets.QComboBox()
        self.combo_voice_zh.setEditable(True)
        self.combo_voice_zh.setMinimumWidth(230)
        self.combo_voice_zh.setCurrentText(self.cfg.voice_zh)
        self.combo_voice_zh.currentTextChanged.connect(self.on_voice_changed)
        tb.addWidget(self.combo_voice_zh)

        btn_test_en = QtWidgets.QToolButton()
        btn_test_en.setText("Test EN")
        btn_test_en.clicked.connect(lambda: self.test_tts("en"))
        tb.addWidget(btn_test_en)

        btn_test_zh = QtWidgets.QToolButton()
        btn_test_zh.setText("Test ZH")
        btn_test_zh.clicked.connect(lambda: self.test_tts("zh"))
        tb.addWidget(btn_test_zh)

        tb.addSeparator()

        # Run controls
        btn_run = QtWidgets.QToolButton()
        btn_run.setText("Run")
        btn_run.clicked.connect(lambda: self.run_pipeline(False))
        tb.addWidget(btn_run)

        btn_run_pub = QtWidgets.QToolButton()
        btn_run_pub.setText("Run + Publish")
        btn_run_pub.clicked.connect(lambda: self.run_pipeline(True))
        tb.addWidget(btn_run_pub)

        btn_stop = QtWidgets.QToolButton()
        btn_stop.setText("Stop")
        btn_stop.clicked.connect(self.stop_pipeline)
        tb.addWidget(btn_stop)

        tb.addSeparator()

        # Comfy controls
        btn_refresh = QtWidgets.QToolButton()
        btn_refresh.setText("Refresh Comfy")
        btn_refresh.clicked.connect(self.refresh_comfy_status)
        tb.addWidget(btn_refresh)

        btn_open = QtWidgets.QToolButton()
        btn_open.setText("Open Comfy UI")
        btn_open.clicked.connect(lambda: webbrowser.open(self.cfg.comfy_url))
        tb.addWidget(btn_open)

        btn_start = QtWidgets.QToolButton()
        btn_start.setText("Start Comfy")
        btn_start.clicked.connect(self.start_comfy)
        tb.addWidget(btn_start)

        btn_stop_comfy = QtWidgets.QToolButton()
        btn_stop_comfy.setText("Stop Comfy")
        btn_stop_comfy.clicked.connect(self.stop_comfy)
        tb.addWidget(btn_stop_comfy)

        # Central splitter: editor top, log bottom
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.setCentralWidget(splitter)

        self.editor = QtWidgets.QPlainTextEdit()
        self.editor.setPlaceholderText("Paste your homework.txt content here…")
        font = QtGui.QFont("Consolas", 11)
        self.editor.setFont(font)
        splitter.addWidget(self.editor)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QtGui.QFont("Consolas", 10))
        splitter.addWidget(self.log)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # Status bar
        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")

        self.append_log("GUI ready.\n")

    def _apply_dark_hint(self, dark: bool) -> None:
        # Keep default style; if you want dark mode later, we can add palette.
        if dark:
            self.status.showMessage("Theme: dark (not enabled in this build)")
        else:
            pass

    # ---------- Logging ----------

    def append_log(self, text: str, prefix: str = "") -> None:
        if not text:
            return
        # Preserve existing line breaks, just prefix block
        if prefix:
            lines = text.splitlines(True)
            text = "".join(prefix + ln for ln in lines)
        self.log.moveCursor(QtGui.QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QtGui.QTextCursor.End)

    # ---------- Editor file ops ----------

    def default_editor_path(self) -> Path:
        p = Path(self.cfg.editor_file)
        if not p.is_absolute():
            p = (self.root_dir / p).resolve()
        return p

    def load_default_editor_if_exists(self) -> None:
        p = self.default_editor_path()
        if p.exists():
            txt = p.read_text(encoding="utf-8", errors="ignore")
            self.editor.setPlainText(txt)
            self.status.showMessage(f"Loaded: {p.name}")
        else:
            self.status.showMessage(f"Ready (default file not found: {p.name})")

    def editor_new(self) -> None:
        self.editor.setPlainText("")
        self.status.showMessage("Editor cleared")

    def editor_load(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load homework text", str(self.root_dir), "Text Files (*.txt);;All Files (*.*)")
        if not path:
            return
        p = Path(path)
        txt = p.read_text(encoding="utf-8", errors="ignore")
        self.editor.setPlainText(txt)

        # set as new default
        try:
            rel = p.resolve().relative_to(self.root_dir.resolve())
            self.cfg.editor_file = str(rel).replace("\\", "/")
        except Exception:
            self.cfg.editor_file = str(p)
        save_config(self.cfg_path, self.cfg)
        self.status.showMessage(f"Loaded: {p.name} (set as default)")

    def editor_save_default(self) -> None:
        p = self.default_editor_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        txt = self.editor.toPlainText().rstrip() + "\n"
        p.write_text(txt, encoding="utf-8")
        self.status.showMessage(f"Saved: {p.name}")

    def editor_save_as(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save homework as", str(self.root_dir), "Text Files (*.txt);;All Files (*.*)")
        if not path:
            return
        p = Path(path)
        txt = self.editor.toPlainText().rstrip() + "\n"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt, encoding="utf-8")

        try:
            rel = p.resolve().relative_to(self.root_dir.resolve())
            self.cfg.editor_file = str(rel).replace("\\", "/")
        except Exception:
            self.cfg.editor_file = str(p)
        save_config(self.cfg_path, self.cfg)
        self.status.showMessage(f"Saved: {p.name} (set as default)")

    # ---------- Persist audio fields ----------

    def on_rate_changed(self, v: int) -> None:
        self.cfg.tts_rate_percent = int(v)
        self.lbl_rate.setText(_rate_string(int(v)))
        save_config(self.cfg_path, self.cfg)

    def on_voice_changed(self, _txt: str) -> None:
        self.cfg.voice_en = self.combo_voice_en.currentText().strip()
        self.cfg.voice_zh = self.combo_voice_zh.currentText().strip()
        save_config(self.cfg_path, self.cfg)

    # ---------- Voice list loading ----------

    def load_voices_background(self) -> None:
        def worker():
            try:
                import edge_tts  # type: ignore

                async def fetch():
                    return await edge_tts.list_voices()

                voices = asyncio.run(fetch())
                en: List[str] = []
                zh: List[str] = []
                for v in voices:
                    short = v.get("ShortName") or ""
                    locale = (v.get("Locale") or "").lower()
                    if short.startswith("en-") or locale.startswith("en-"):
                        en.append(short)
                    if short.startswith("zh-") or locale.startswith("zh-"):
                        zh.append(short)

                en = sorted(set([x for x in en if x]))
                zh = sorted(set([x for x in zh if x]))
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "_apply_voice_lists",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(list, en),
                    QtCore.Q_ARG(list, zh),
                )
            except Exception as e:
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "_log_voice_error",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, str(e)),
                )

        threading.Thread(target=worker, daemon=True).start()

    @QtCore.Slot(list, list)
    def _apply_voice_lists(self, en_list: List[str], zh_list: List[str]) -> None:
        # Keep editable; just load options
        cur_en = self.combo_voice_en.currentText()
        cur_zh = self.combo_voice_zh.currentText()

        self.combo_voice_en.blockSignals(True)
        self.combo_voice_zh.blockSignals(True)

        self.combo_voice_en.clear()
        self.combo_voice_en.addItems(en_list)
        self.combo_voice_en.setEditable(True)
        self.combo_voice_en.setCurrentText(cur_en or self.cfg.voice_en)

        self.combo_voice_zh.clear()
        self.combo_voice_zh.addItems(zh_list)
        self.combo_voice_zh.setEditable(True)
        self.combo_voice_zh.setCurrentText(cur_zh or self.cfg.voice_zh)

        self.combo_voice_en.blockSignals(False)
        self.combo_voice_zh.blockSignals(False)

        self.append_log("[Audio] voice list loaded.\n")

    @QtCore.Slot(str)
    def _log_voice_error(self, err: str) -> None:
        self.append_log(f"[Audio] voice list load failed (manual entry ok): {err}\n")

    # ---------- TTS test ----------

    def test_tts(self, lang: str) -> None:
        voice = self.combo_voice_en.currentText().strip() if lang == "en" else self.combo_voice_zh.currentText().strip()
        rate = int(self.cfg.tts_rate_percent)
        rate_str = _rate_string(rate)
        sample_text = (
            "Hello. This is Sky Education audio test. One, two, three."
            if lang == "en"
            else "你好。这是思恺教育音频测试。一二三。"
        )
        out_dir = (self.root_dir / "output" / "_tts_preview").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"tts_test_{lang}_{int(time.time())}.mp3"

        # persist
        self.cfg.voice_en = self.combo_voice_en.currentText().strip()
        self.cfg.voice_zh = self.combo_voice_zh.currentText().strip()
        save_config(self.cfg_path, self.cfg)

        def worker():
            try:
                import edge_tts  # type: ignore

                async def synth():
                    communicate = edge_tts.Communicate(sample_text, voice=voice, rate=rate_str)
                    await communicate.save(str(out_path))

                asyncio.run(synth())
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "_log_tts_done",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, str(out_path)),
                )
            except Exception as e:
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "_log_tts_fail",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, str(e)),
                )

        threading.Thread(target=worker, daemon=True).start()

    @QtCore.Slot(str)
    def _log_tts_done(self, path: str) -> None:
        self.append_log(f"[Audio] test generated: {path}\n")
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            pass

    @QtCore.Slot(str)
    def _log_tts_fail(self, err: str) -> None:
        self.append_log(f"[Audio] test failed: {err}\n")

    # ---------- Comfy status/control ----------

    def refresh_comfy_status(self) -> None:
        _, port = _extract_host_port(self.cfg.comfy_url)
        pids = _netstat_listening_pids(port)
        http_ok = comfy_http_reachable(self.cfg.comfy_url)

        if pids:
            self.comfy_external_pid = pids[0]
            name = _tasklist_name(pids[0])
            self.status.showMessage(f"ComfyUI LISTENING on {port} (PID {pids[0]}: {name}) • HTTP={'OK' if http_ok else 'NO'}")
            return

        self.comfy_external_pid = None
        if http_ok:
            self.status.showMessage("ComfyUI reachable (PID unknown) • HTTP=OK")
        else:
            self.status.showMessage("ComfyUI not reachable")

    def auto_start_comfy_if_needed(self) -> None:
        if comfy_http_reachable(self.cfg.comfy_url):
            self.append_log("[ComfyUI] already reachable → no auto-start.\n")
            return
        _, port = _extract_host_port(self.cfg.comfy_url)
        if _netstat_listening_pids(port):
            self.append_log("[ComfyUI] LISTENING detected → no auto-start.\n")
            return
        self.append_log("[ComfyUI] not running → auto start.\n")
        self.start_comfy()

    def start_comfy(self) -> None:
        # Hard guard: never start a second instance.
        if comfy_http_reachable(self.cfg.comfy_url):
            self.append_log("[ComfyUI] reachable → NOT starting a second instance.\n")
            return

        _, port = _extract_host_port(self.cfg.comfy_url)
        pids = _netstat_listening_pids(port)
        if pids:
            name = _tasklist_name(pids[0])
            self.append_log(f"[ComfyUI] LISTENING detected (PID {pids[0]}: {name}) → NOT starting.\n")
            return

        env = os.environ.copy()
        env["HF_ENDPOINT"] = self.cfg.hf_endpoint

        cmd_program = self.cfg.comfy_python
        cmd_args = self.cfg.comfy_args

        self.comfy_proc.start(cmd_program, cmd_args, self.cfg.comfy_workdir, env)
        self.status.showMessage("Starting ComfyUI…")
        QtCore.QTimer.singleShot(900, self._poll_comfy_ready)

    def _poll_comfy_ready(self) -> None:
        if comfy_http_reachable(self.cfg.comfy_url):
            self.append_log("[ComfyUI] READY (HTTP OK)\n")
            self.refresh_comfy_status()
            return
        if not self.comfy_proc.is_running():
            self.append_log("[ComfyUI] failed to start (process exited)\n")
            self.refresh_comfy_status()
            return
        QtCore.QTimer.singleShot(900, self._poll_comfy_ready)

    def stop_comfy(self) -> None:
        # Only stop if we started it via QProcess
        if self.comfy_proc.is_running():
            self.comfy_proc.terminate()
            return

        if self.comfy_external_pid:
            self.append_log(f"[ComfyUI] External PID {self.comfy_external_pid} detected (use taskkill if needed).\n")
        else:
            self.append_log("[ComfyUI] No managed process to stop.\n")

    # ---------- Apply picture cards type to workflow ----------

    def apply_picture_type_to_workflow(self) -> None:
        sel = self.combo_picture.currentText().strip()
        if sel not in ("Realistic", "Cartoon"):
            QtWidgets.QMessageBox.critical(self, "Invalid", "Picture cards type must be Realistic or Cartoon.")
            return

        self.cfg.picture_cards_type = sel
        save_config(self.cfg_path, self.cfg)

        wf_rel = self.cfg.comfy_workflow_path.strip()
        wf_path = (self.root_dir / wf_rel).resolve()
        if not wf_path.exists():
            QtWidgets.QMessageBox.critical(self, "Workflow not found", f"Not found:\n{wf_path}")
            return

        pos = POS_REALISTIC if sel == "Realistic" else POS_CARTOON
        neg = NEG_ALWAYS

        try:
            obj = _load_json(wf_path)
            backup = wf_path.with_suffix(f".bak_{_timestamp()}.json")
            _save_json(backup, obj)

            patched, stats = patch_workflow_prompts_only(obj, positive_text=pos, negative_text=neg)
            _save_json(wf_path, patched)

            self.append_log(
                f"[Images] Applied Picture cards type={sel}\n"
                f"         workflow: {wf_path}\n"
                f"         backup:   {backup.name}\n"
                f"         patched:  pos_nodes={stats.get('clip_text_pos')} neg_nodes={stats.get('clip_text_neg')}\n"
            )
            self.status.showMessage(f"Applied: Picture cards type = {sel}")

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Apply failed", f"Workflow update failed:\n{e}")
            self.append_log(f"[Images] apply failed: {e}\n")

    # ---------- Pipeline ----------

    def run_pipeline(self, publish: bool) -> None:
        # save editor → default file
        self.editor_save_default()

        pipeline_script = str((Path(self.cfg.project_workdir) / self.cfg.pipeline_script).resolve())
        if not Path(pipeline_script).exists():
            QtWidgets.QMessageBox.critical(self, "Missing pipeline script", f"Not found:\n{pipeline_script}")
            return

        input_path = str(self.default_editor_path())
        args = [pipeline_script, "--input", input_path]
        if publish:
            args.append("--publish")

        env = os.environ.copy()
        env["HF_ENDPOINT"] = self.cfg.hf_endpoint

        # TTS env so pipeline can obey GUI
        env["SKYED_TTS_RATE"] = _rate_string(int(self.cfg.tts_rate_percent))
        env["SKYED_VOICE_EN"] = str(self.cfg.voice_en)
        env["SKYED_VOICE_ZH"] = str(self.cfg.voice_zh)

        self.append_log(
            f"[Audio] env: SKYED_TTS_RATE={env['SKYED_TTS_RATE']} "
            f"SKYED_VOICE_EN={env['SKYED_VOICE_EN']} SKYED_VOICE_ZH={env['SKYED_VOICE_ZH']}\n"
        )

        self.pipe_proc.start(self.cfg.project_python, args, self.cfg.project_workdir, env)
        self.status.showMessage("Pipeline running…")

    def stop_pipeline(self) -> None:
        self.pipe_proc.terminate()
        self.status.showMessage("Stop requested")


def main() -> None:
    root_dir = Path(__file__).resolve().parent
    cfg_path = root_dir / CONFIG_NAME
    if not cfg_path.exists():
        print(f"ERROR: missing {CONFIG_NAME} in {root_dir}")
        sys.exit(1)

    cfg = load_config(cfg_path)

    app = QtWidgets.QApplication(sys.argv)

    # Use native style (Windows)
    app.setStyle("Fusion")  # keeps it consistent; still modern. If you want native-only, remove this line.

    win = MainWindow(root_dir, cfg, cfg_path)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
