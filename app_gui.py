# app_gui.py
# SkyEd Automation GUI (Windows-friendly, ComfyUI-safe, modern layout)
#
# Added in this version:
# - Audio dashboard controls:
#   - TTS speed (rate %) slider
#   - EN/ZH voice picker (Edge TTS voices) with async background loading
#   - Manual voice entry fallback (works even if voice list can’t load)
#   - “Test EN / Test ZH” buttons to generate a short sample mp3 (if edge-tts installed)
# - Persists audio settings into gui_config.json (backward compatible)
# - Passes selections to pipeline via env vars:
#   SKYED_TTS_RATE, SKYED_VOICE_EN, SKYED_VOICE_ZH
#
# Optional theme:
#   pip install sv-ttk
#
# Run:
#   .\.venv\Scripts\python.exe app_gui.py

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import requests

CONFIG_NAME = "gui_config.json"


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

    # NEW (optional) audio settings (kept in gui_config.json)
    tts_rate_percent: int = -10  # matches your current requirement default
    voice_en: str = "en-US-JennyNeural"
    voice_zh: str = "zh-CN-XiaoxiaoNeural"


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
        tts_rate_percent=int(data.get("tts_rate_percent", -10)),
        voice_en=str(data.get("voice_en", "en-US-JennyNeural")),
        voice_zh=str(data.get("voice_zh", "zh-CN-XiaoxiaoNeural")),
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
        # NEW
        "tts_rate_percent": int(cfg.tts_rate_percent),
        "voice_en": str(cfg.voice_en),
        "voice_zh": str(cfg.voice_zh),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- Process runner ----------------

class ProcessRunner:
    """Start a subprocess and stream its output into a queue."""

    def __init__(self, name: str):
        self.name = name
        self.proc: Optional[subprocess.Popen] = None
        self.q: queue.Queue[str] = queue.Queue()

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, cmd: List[str], cwd: str, env: dict) -> None:
        if self.is_running():
            self.q.put(f"[{self.name}] Already running.\n")
            return

        self.q.put(f"[{self.name}] START: {' '.join(cmd)}\n")
        self.proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        threading.Thread(target=self._reader_thread, daemon=True).start()

    def _reader_thread(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            self.q.put(line)
        code = self.proc.poll()
        self.q.put(f"[{self.name}] EXIT: {code}\n")

    def stop(self) -> None:
        if not self.is_running():
            self.q.put(f"[{self.name}] Not running.\n")
            return
        assert self.proc is not None
        self.q.put(f"[{self.name}] STOP requested...\n")
        try:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.q.put(f"[{self.name}] terminate() timed out -> kill()\n")
                self.proc.kill()
        except Exception as e:
            self.q.put(f"[{self.name}] stop error: {e}\n")

    def drain(self) -> List[str]:
        out: List[str] = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                break
        return out


# ---------------- Utilities ----------------

def _extract_host_port(url: str) -> Tuple[str, int]:
    u = urlparse(url)
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    return host, port


def _netstat_listening_pids(port: int) -> List[int]:
    """Windows netstat parser. Returns PIDs that are LISTENING on the given local port."""
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


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h = m // 60
    m = m % 60
    return f"{h}h{m:02d}m"


def _rate_string(rate_percent: int) -> str:
    # edge-tts expects strings like "+10%" or "-10%"
    if rate_percent >= 0:
        return f"+{rate_percent}%"
    return f"{rate_percent}%"


# ---------------- GUI ----------------

class SkyEdGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SkyEd Automation")
        self.geometry("1400x900")
        self.minsize(1200, 760)

        self.root_dir = Path(__file__).resolve().parent
        self.cfg_path = self.root_dir / CONFIG_NAME
        if not self.cfg_path.exists():
            messagebox.showerror("Missing config", f"{CONFIG_NAME} not found in:\n{self.root_dir}")
            self.destroy()
            return

        self.cfg = load_config(self.cfg_path)

        self.comfy = ProcessRunner("ComfyUI")
        self.pipe = ProcessRunner("Pipeline")

        # PID of an already-running ComfyUI (not managed by this GUI)
        self.comfy_external_pid: Optional[int] = None

        # runtime state
        self._sv_ttk_available = False
        self._theme_mode = tk.StringVar(value="light")  # "light" | "dark"
        self._pipeline_started_at: Optional[float] = None
        self._pipeline_publish_flag: bool = False

        # voice list state
        self._voices_loaded = False
        self._voices_loading = False
        self._voices_en: List[str] = []
        self._voices_zh: List[str] = []

        self._apply_theme()
        self._style()
        self._build_ui()
        self._poll_logs()
        self._tick_status()

        self.after(250, self.refresh_comfy_status)
        self.after(550, self.auto_start_comfy_if_needed)
        self.after(350, self._load_voice_list_background)

    # ---------- Look & feel ----------

    def _apply_theme(self) -> None:
        try:
            import sv_ttk  # type: ignore
            self._sv_ttk_available = True
            sv_ttk.set_theme(self._theme_mode.get())
        except Exception:
            self._sv_ttk_available = False

    def _style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"))
        style.configure("Subheader.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Muted.TLabel", font=("Segoe UI", 9))
        style.configure("Mono.TLabel", font=("Consolas", 9))
        style.configure("TButton", padding=(12, 7))
        style.configure("Tool.TButton", padding=(10, 6))
        style.configure("Danger.TButton", padding=(10, 6))
        style.configure("Primary.TButton", padding=(12, 7))
        style.configure("Sidebar.TFrame", padding=14)
        style.configure("Topbar.TFrame", padding=(14, 10))
        style.configure("Work.TFrame", padding=12)
        style.configure("Status.TFrame", padding=(14, 8))
        style.configure("TNotebook.Tab", padding=(12, 8))

    # ---------- UI layout ----------

    def _build_ui(self) -> None:
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True)

        self.topbar = ttk.Frame(root, style="Topbar.TFrame")
        self.topbar.pack(fill="x")

        self.body = ttk.Frame(root)
        self.body.pack(fill="both", expand=True)

        self.statusbar = ttk.Frame(root, style="Status.TFrame")
        self.statusbar.pack(fill="x")

        self._build_topbar()
        self._build_body()
        self._build_statusbar()

        self._log("GUI ready.\n")
        self.after(150, self.load_file_into_editor)

    def _build_topbar(self) -> None:
        left = ttk.Frame(self.topbar)
        left.pack(side="left", fill="x", expand=True)

        right = ttk.Frame(self.topbar)
        right.pack(side="right")

        ttk.Label(left, text="SkyEd Automation", style="Header.TLabel").pack(side="top", anchor="w")
        host, port = _extract_host_port(self.cfg.comfy_url)
        ttk.Label(
            left,
            text=f"ComfyUI: {host}:{port}   •   Project: {Path(self.cfg.project_workdir).name}",
            style="Muted.TLabel",
        ).pack(side="top", anchor="w", pady=(2, 0))

        ttk.Button(
            right,
            text="Run (Generate)",
            style="Primary.TButton",
            command=lambda: self.run_pipeline(publish=False),
        ).pack(side="left", padx=6)
        ttk.Button(
            right,
            text="Run + Publish",
            style="Primary.TButton",
            command=lambda: self.run_pipeline(publish=True),
        ).pack(side="left", padx=6)
        ttk.Button(right, text="Stop", style="Danger.TButton", command=self.stop_pipeline).pack(side="left", padx=6)

        if self._sv_ttk_available:
            ttk.Separator(right, orient="vertical").pack(side="left", fill="y", padx=10)
            ttk.Label(right, text="Theme", style="Muted.TLabel").pack(side="left", padx=(0, 6))
            self.theme_btn = ttk.Button(right, text="Dark", style="Tool.TButton", command=self.toggle_theme)
            self.theme_btn.pack(side="left")

    def _build_body(self) -> None:
        pan = ttk.Panedwindow(self.body, orient="horizontal")
        pan.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.sidebar = ttk.Frame(pan, style="Sidebar.TFrame")
        self.workspace = ttk.Frame(pan, style="Work.TFrame")
        pan.add(self.sidebar, weight=1)
        pan.add(self.workspace, weight=3)

        self._build_sidebar()
        self._build_workspace()

    def _build_sidebar(self) -> None:
        # -------- ComfyUI card --------
        comfy_card = ttk.Labelframe(self.sidebar, text="ComfyUI")
        comfy_card.pack(fill="x", pady=(0, 12))

        self.comfy_state_var = tk.StringVar(value="Unknown")
        self.comfy_pid_var = tk.StringVar(value="PID: -")
        self.comfy_http_var = tk.StringVar(value="HTTP: -")

        row = ttk.Frame(comfy_card)
        row.pack(fill="x", pady=(8, 4), padx=8)
        ttk.Label(row, text="State:", style="Muted.TLabel").pack(side="left")
        ttk.Label(row, textvariable=self.comfy_state_var).pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(comfy_card)
        row2.pack(fill="x", pady=2, padx=8)
        ttk.Label(row2, textvariable=self.comfy_pid_var, style="Mono.TLabel").pack(side="left")
        ttk.Label(row2, text="  •  ", style="Muted.TLabel").pack(side="left")
        ttk.Label(row2, textvariable=self.comfy_http_var, style="Mono.TLabel").pack(side="left")

        btns = ttk.Frame(comfy_card)
        btns.pack(fill="x", pady=(8, 8), padx=8)
        ttk.Button(btns, text="Refresh", style="Tool.TButton", command=self.refresh_comfy_status).pack(side="left", padx=4)
        ttk.Button(btns, text="Start", style="Tool.TButton", command=self.start_comfy).pack(side="left", padx=4)
        ttk.Button(btns, text="Stop", style="Tool.TButton", command=self.stop_comfy).pack(side="left", padx=4)
        ttk.Button(btns, text="Open UI", style="Tool.TButton", command=self.open_comfy_ui).pack(side="left", padx=4)

        pid_btns = ttk.Frame(comfy_card)
        pid_btns.pack(fill="x", pady=(0, 8), padx=8)
        ttk.Button(pid_btns, text="Find PID", style="Tool.TButton", command=self.find_external_pid).pack(side="left", padx=4)
        ttk.Button(pid_btns, text="Kill PID", style="Tool.TButton", command=self.kill_external_pid).pack(side="left", padx=4)

        # -------- Input card --------
        io_card = ttk.Labelframe(self.sidebar, text="Input")
        io_card.pack(fill="x", pady=(0, 12))

        self.input_var = tk.StringVar(value=str(self.root_dir / "homework.txt"))
        ttk.Label(io_card, text="Homework file path", style="Muted.TLabel").pack(anchor="w", padx=8, pady=(8, 2))
        self.input_entry = ttk.Entry(io_card, textvariable=self.input_var)
        self.input_entry.pack(fill="x", padx=8, pady=(0, 8))

        io_btns = ttk.Frame(io_card)
        io_btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(io_btns, text="Browse", style="Tool.TButton", command=self.browse_input).pack(side="left", padx=4)
        ttk.Button(io_btns, text="Load → Editor", style="Tool.TButton", command=self.load_file_into_editor).pack(side="left", padx=4)
        ttk.Button(io_btns, text="Save Editor → File", style="Tool.TButton", command=self.save_editor_to_current_file).pack(side="left", padx=4)

        # -------- Audio / TTS card (NEW) --------
        tts_card = ttk.Labelframe(self.sidebar, text="Audio (TTS)")
        tts_card.pack(fill="x", pady=(0, 12))

        # rate slider
        self.tts_rate_var = tk.IntVar(value=int(self.cfg.tts_rate_percent))
        self.tts_rate_label_var = tk.StringVar(value=f"Speed: {_rate_string(self.tts_rate_var.get())}")

        ttk.Label(tts_card, textvariable=self.tts_rate_label_var).pack(anchor="w", padx=8, pady=(8, 2))
        rate_row = ttk.Frame(tts_card)
        rate_row.pack(fill="x", padx=8, pady=(0, 6))

        self.rate_scale = ttk.Scale(
            rate_row,
            from_=-30,
            to=30,
            orient="horizontal",
            command=self._on_rate_scale,
        )
        self.rate_scale.set(self.tts_rate_var.get())
        self.rate_scale.pack(side="left", fill="x", expand=True)

        ttk.Button(rate_row, text="Reset", style="Tool.TButton", command=self._reset_rate).pack(side="left", padx=(8, 0))

        # voice pickers
        self.voice_en_var = tk.StringVar(value=str(self.cfg.voice_en))
        self.voice_zh_var = tk.StringVar(value=str(self.cfg.voice_zh))

        ttk.Label(tts_card, text="EN voice", style="Muted.TLabel").pack(anchor="w", padx=8, pady=(6, 2))
        self.voice_en_combo = ttk.Combobox(tts_card, textvariable=self.voice_en_var, values=[], state="normal")
        self.voice_en_combo.pack(fill="x", padx=8, pady=(0, 6))

        ttk.Label(tts_card, text="ZH voice", style="Muted.TLabel").pack(anchor="w", padx=8, pady=(0, 2))
        self.voice_zh_combo = ttk.Combobox(tts_card, textvariable=self.voice_zh_var, values=[], state="normal")
        self.voice_zh_combo.pack(fill="x", padx=8, pady=(0, 8))

        test_row = ttk.Frame(tts_card)
        test_row.pack(fill="x", padx=8, pady=(0, 10))
        ttk.Button(test_row, text="Test EN", style="Tool.TButton", command=lambda: self._test_tts(lang="en")).pack(side="left", padx=4)
        ttk.Button(test_row, text="Test ZH", style="Tool.TButton", command=lambda: self._test_tts(lang="zh")).pack(side="left", padx=4)
        ttk.Button(test_row, text="Save Audio Settings", style="Tool.TButton", command=self._persist_audio_settings).pack(side="left", padx=4)

        self.tts_hint_var = tk.StringVar(value="Loading voices…")
        ttk.Label(tts_card, textvariable=self.tts_hint_var, style="Muted.TLabel").pack(anchor="w", padx=8, pady=(0, 8))

        # -------- Pipeline card --------
        pipe_card = ttk.Labelframe(self.sidebar, text="Pipeline")
        pipe_card.pack(fill="x", pady=(0, 12))

        self.pipeline_state_var = tk.StringVar(value="Idle")
        self.pipeline_last_var = tk.StringVar(value="Last: -")

        ttk.Label(pipe_card, textvariable=self.pipeline_state_var).pack(anchor="w", padx=8, pady=(8, 2))
        ttk.Label(pipe_card, textvariable=self.pipeline_last_var, style="Muted.TLabel").pack(anchor="w", padx=8, pady=(0, 8))

        pipe_btns = ttk.Frame(pipe_card)
        pipe_btns.pack(fill="x", padx=8, pady=(0, 10))
        ttk.Button(pipe_btns, text="Run (Generate)", style="Primary.TButton", command=lambda: self.run_pipeline(publish=False)).pack(fill="x", pady=3)
        ttk.Button(pipe_btns, text="Run + Publish", style="Primary.TButton", command=lambda: self.run_pipeline(publish=True)).pack(fill="x", pady=3)
        ttk.Button(pipe_btns, text="Stop Pipeline", style="Danger.TButton", command=self.stop_pipeline).pack(fill="x", pady=3)

    def _build_workspace(self) -> None:
        self.nb = ttk.Notebook(self.workspace)
        self.nb.pack(fill="both", expand=True)

        self.tab_editor = ttk.Frame(self.nb)
        self.nb.add(self.tab_editor, text="Editor")

        self.tab_logs = ttk.Frame(self.nb)
        self.nb.add(self.tab_logs, text="Logs")

        top = ttk.Frame(self.tab_editor)
        top.pack(fill="x", padx=10, pady=(10, 8))

        ttk.Label(top, text="Homework Editor", style="Subheader.TLabel").pack(side="left")

        btns = ttk.Frame(top)
        btns.pack(side="right")
        ttk.Button(btns, text="New", style="Tool.TButton", command=self.new_editor).pack(side="left", padx=4)
        ttk.Button(btns, text="Load...", style="Tool.TButton", command=self.load_file_into_editor).pack(side="left", padx=4)
        ttk.Button(btns, text="Save", style="Tool.TButton", command=self.save_editor_to_current_file).pack(side="left", padx=4)
        ttk.Button(btns, text="Save As...", style="Tool.TButton", command=self.save_editor_as).pack(side="left", padx=4)

        self.editor = ScrolledText(self.tab_editor, wrap="word", font=("Consolas", 11), height=10)
        self.editor.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        ltop = ttk.Frame(self.tab_logs)
        ltop.pack(fill="x", padx=10, pady=(10, 8))

        ttk.Label(ltop, text="Logs", style="Subheader.TLabel").pack(side="left")

        lbtns = ttk.Frame(ltop)
        lbtns.pack(side="right")
        ttk.Button(lbtns, text="Clear", style="Tool.TButton", command=self.clear_logs).pack(side="left", padx=4)
        ttk.Button(lbtns, text="Copy", style="Tool.TButton", command=self.copy_logs).pack(side="left", padx=4)
        ttk.Button(lbtns, text="Save...", style="Tool.TButton", command=self.save_logs_to_file).pack(side="left", padx=4)

        self.log = ScrolledText(self.tab_logs, wrap="word", font=("Consolas", 10), height=12)
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _build_statusbar(self) -> None:
        self.status_var = tk.StringVar(value="Ready")
        self.hint_var = tk.StringVar(value="")

        left = ttk.Frame(self.statusbar)
        left.pack(side="left", fill="x", expand=True)

        right = ttk.Frame(self.statusbar)
        right.pack(side="right")

        ttk.Label(left, textvariable=self.status_var).pack(side="left")
        ttk.Label(left, textvariable=self.hint_var, style="Muted.TLabel").pack(side="left", padx=(10, 0))

        ttk.Button(right, text="Open Output", style="Tool.TButton", command=self.open_output_folder).pack(side="left", padx=4)
        ttk.Button(right, text="Open Project", style="Tool.TButton", command=self.open_project_folder).pack(side="left", padx=4)

    # ---------- Theme ----------

    def toggle_theme(self) -> None:
        if not self._sv_ttk_available:
            return
        try:
            import sv_ttk  # type: ignore
            new_mode = "dark" if self._theme_mode.get() == "light" else "light"
            self._theme_mode.set(new_mode)
            sv_ttk.set_theme(new_mode)
            if hasattr(self, "theme_btn"):
                self.theme_btn.configure(text="Light" if new_mode == "dark" else "Dark")
        except Exception:
            pass

    # ---------- Logs / helpers ----------

    def _log(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")

    def clear_logs(self) -> None:
        self.log.delete("1.0", "end")

    def copy_logs(self) -> None:
        txt = self.log.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(txt)
        self.status_var.set("Copied logs to clipboard")

    def save_logs_to_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save logs as",
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        txt = self.log.get("1.0", "end")
        Path(path).write_text(txt, encoding="utf-8", errors="ignore")
        self.status_var.set(f"Saved logs → {Path(path).name}")

    def open_output_folder(self) -> None:
        out_dir = (self.root_dir / "output").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(out_dir))  # type: ignore[attr-defined]
        except Exception as e:
            self._log(f"[GUI] open_output_folder error: {e}\n")

    def open_project_folder(self) -> None:
        try:
            os.startfile(str(self.root_dir))  # type: ignore[attr-defined]
        except Exception as e:
            self._log(f"[GUI] open_project_folder error: {e}\n")

    # ---------- Input / editor ----------

    def browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select homework input text",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.input_var.set(path)
            self.status_var.set(f"Selected: {Path(path).name}")

    def new_editor(self) -> None:
        self.editor.delete("1.0", "end")
        self.status_var.set("Editor cleared")

    def load_file_into_editor(self) -> None:
        p = Path(self.input_var.get().strip())
        if not p.exists():
            self.status_var.set("File not found (editor unchanged)")
            return
        txt = p.read_text(encoding="utf-8", errors="ignore")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", txt)
        self.status_var.set(f"Loaded into editor: {p.name}")

    def save_editor_to_current_file(self) -> None:
        p = Path(self.input_var.get().strip())
        if not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        txt = self.editor.get("1.0", "end").rstrip() + "\n"
        p.write_text(txt, encoding="utf-8")
        self.status_var.set(f"Saved: {p.name}")

    def save_editor_as(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save homework as",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        self.input_var.set(path)
        self.save_editor_to_current_file()

    # ---------- Audio controls (NEW) ----------

    def _on_rate_scale(self, _val: str) -> None:
        # ttk.Scale is float; snap to int percent
        v = int(round(float(self.rate_scale.get())))
        self.tts_rate_var.set(v)
        self.tts_rate_label_var.set(f"Speed: {_rate_string(v)}")

    def _reset_rate(self) -> None:
        self.rate_scale.set(-10)
        self.tts_rate_var.set(-10)
        self.tts_rate_label_var.set("Speed: -10%")

    def _persist_audio_settings(self) -> None:
        self.cfg.tts_rate_percent = int(self.tts_rate_var.get())
        self.cfg.voice_en = self.voice_en_var.get().strip()
        self.cfg.voice_zh = self.voice_zh_var.get().strip()
        try:
            save_config(self.cfg_path, self.cfg)
            self.status_var.set("Saved audio settings to gui_config.json")
            self._log(f"[Audio] saved: rate={self.cfg.tts_rate_percent}% en={self.cfg.voice_en} zh={self.cfg.voice_zh}\n")
        except Exception as e:
            self._log(f"[Audio] save_config error: {e}\n")

    def _load_voice_list_background(self) -> None:
        if self._voices_loaded or self._voices_loading:
            return
        self._voices_loading = True
        self.tts_hint_var.set("Loading voices… (edge-tts)")

        def worker():
            try:
                import asyncio
                import edge_tts  # type: ignore

                async def fetch():
                    return await edge_tts.list_voices()

                voices = asyncio.run(fetch())
                en = []
                zh = []
                for v in voices:
                    short = v.get("ShortName") or ""
                    locale = (v.get("Locale") or "").lower()
                    if short.startswith("en-") or locale.startswith("en-"):
                        en.append(short)
                    if short.startswith("zh-") or locale.startswith("zh-"):
                        zh.append(short)
                en = sorted(set([x for x in en if x]))
                zh = sorted(set([x for x in zh if x]))
                self._voices_en = en
                self._voices_zh = zh
                self._voices_loaded = True

                self.after(0, self._apply_voice_lists_to_ui)
            except Exception as e:
                self._voices_loaded = False
                self.after(0, lambda: self.tts_hint_var.set(f"Voice list not available (manual entry ok). ({e})"))
            finally:
                self._voices_loading = False

        threading.Thread(target=worker, daemon=True).start()

    def _apply_voice_lists_to_ui(self) -> None:
        # Populate comboboxes but keep them editable
        if self._voices_en:
            self.voice_en_combo.configure(values=self._voices_en)
        if self._voices_zh:
            self.voice_zh_combo.configure(values=self._voices_zh)

        # if current value empty, set first
        if not self.voice_en_var.get().strip() and self._voices_en:
            self.voice_en_var.set(self._voices_en[0])
        if not self.voice_zh_var.get().strip() and self._voices_zh:
            self.voice_zh_var.set(self._voices_zh[0])

        self.tts_hint_var.set("Voices loaded. (You can still type custom voice IDs.)")

    def _test_tts(self, lang: str) -> None:
        """
        Generates a short MP3 sample using edge-tts if installed.
        This is a dashboard sanity check, not part of the pipeline.
        """
        voice = self.voice_en_var.get().strip() if lang == "en" else self.voice_zh_var.get().strip()
        rate = int(self.tts_rate_var.get())
        rate_str = _rate_string(rate)

        sample_text = "Hello. This is Sky Education audio test. One, two, three." if lang == "en" else "你好。这是思恺教育音频测试。一二三。"

        out_dir = (self.root_dir / "output" / "_tts_preview").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"tts_test_{lang}_{int(time.time())}.mp3"

        def worker():
            try:
                import asyncio
                import edge_tts  # type: ignore

                async def synth():
                    communicate = edge_tts.Communicate(sample_text, voice=voice, rate=rate_str)
                    await communicate.save(str(out_path))

                asyncio.run(synth())
                self.after(0, lambda: self._log(f"[Audio] test generated: {out_path}\n"))
                try:
                    os.startfile(str(out_path))  # type: ignore[attr-defined]
                except Exception:
                    pass
                self.after(0, lambda: self.status_var.set(f"TTS test done: {out_path.name}"))
            except Exception as e:
                self.after(0, lambda: self._log(f"[Audio] test failed: {e}\n"))
                self.after(0, lambda: self.status_var.set("TTS test failed (see logs)"))

        threading.Thread(target=worker, daemon=True).start()

    # ---------- ComfyUI control ----------

    def comfy_http_reachable(self) -> bool:
        try:
            r = requests.get(self.cfg.comfy_url, timeout=1.2)
            return r.status_code == 200
        except Exception:
            return False

    def refresh_comfy_status(self) -> None:
        _, port = _extract_host_port(self.cfg.comfy_url)
        pids = _netstat_listening_pids(port)

        if pids:
            self.comfy_external_pid = pids[0]
            name = _tasklist_name(pids[0])
            self.comfy_state_var.set("LISTENING")
            self.comfy_pid_var.set(f"PID: {pids[0]} ({name})")
            self.comfy_http_var.set("HTTP: " + ("OK" if self.comfy_http_reachable() else "NO"))
            self.status_var.set(f"ComfyUI LISTENING on {port} (PID {pids[0]}: {name})")
            self._log(f"[ComfyUI] LISTENING on port {port} (PID {pids[0]}: {name}).\n")
            return

        if self.comfy_http_reachable():
            self.comfy_external_pid = None
            self.comfy_state_var.set("Reachable")
            self.comfy_pid_var.set("PID: unknown")
            self.comfy_http_var.set("HTTP: OK")
            self.status_var.set("ComfyUI reachable (PID unknown)")
            self._log("[ComfyUI] reachable (HTTP OK), PID unknown.\n")
        else:
            self.comfy_external_pid = None
            self.comfy_state_var.set("Not running")
            self.comfy_pid_var.set("PID: -")
            self.comfy_http_var.set("HTTP: NO")
            self.status_var.set("ComfyUI not reachable")
            self._log("[ComfyUI] not reachable.\n")

    def auto_start_comfy_if_needed(self) -> None:
        if self.comfy_http_reachable():
            self.status_var.set("ComfyUI reachable (no auto-start)")
            self._log("[ComfyUI] already reachable → no auto-start.\n")
            return

        _, port = _extract_host_port(self.cfg.comfy_url)
        if _netstat_listening_pids(port):
            self.status_var.set("ComfyUI LISTENING (no auto-start)")
            self._log("[ComfyUI] LISTENING detected → no auto-start.\n")
            return

        self._log("[ComfyUI] not running → auto start.\n")
        self.start_comfy()

    def start_comfy(self) -> None:
        if self.comfy_http_reachable():
            self._log("[ComfyUI] reachable → NOT starting a second instance.\n")
            self.status_var.set("ComfyUI already running")
            return

        _, port = _extract_host_port(self.cfg.comfy_url)
        pids = _netstat_listening_pids(port)
        if pids:
            self.comfy_external_pid = pids[0]
            name = _tasklist_name(pids[0])
            self._log(f"[ComfyUI] LISTENING detected (PID {pids[0]}: {name}) → NOT starting.\n")
            self.status_var.set(f"ComfyUI already running (PID {pids[0]})")
            return

        cfg = self.cfg
        cmd = [cfg.comfy_python] + cfg.comfy_args

        env = os.environ.copy()
        env["HF_ENDPOINT"] = cfg.hf_endpoint

        self.comfy.start(cmd=cmd, cwd=cfg.comfy_workdir, env=env)
        self.status_var.set("Starting ComfyUI…")
        self.after(700, self._poll_comfy_ready)

    def _poll_comfy_ready(self) -> None:
        if self.comfy_http_reachable():
            self.status_var.set("ComfyUI running (managed by GUI)")
            self.refresh_comfy_status()
            self._log("[ComfyUI] READY (HTTP OK)\n")
            return
        if not self.comfy.is_running():
            self._log("[ComfyUI] failed to start (process exited). Refreshing status...\n")
            self.refresh_comfy_status()
            return
        self.after(700, self._poll_comfy_ready)

    def stop_comfy(self) -> None:
        if self.comfy.is_running():
            self.comfy.stop()
            self.status_var.set("Stopping ComfyUI (managed)…")
            return

        if self.comfy_external_pid:
            self._log(f"[ComfyUI] External PID {self.comfy_external_pid} detected. Use Kill PID if needed.\n")
        else:
            self._log("[ComfyUI] No managed process to stop.\n")

    def open_comfy_ui(self) -> None:
        webbrowser.open(self.cfg.comfy_url)

    def find_external_pid(self) -> None:
        _, port = _extract_host_port(self.cfg.comfy_url)
        pids = _netstat_listening_pids(port)
        if not pids:
            self.comfy_external_pid = None
            self._log(f"[ComfyUI] No LISTENING PID found on port {port}. (TIME_WAIT is normal)\n")
            self.status_var.set("ComfyUI PID not found")
            self.refresh_comfy_status()
            return
        self.comfy_external_pid = pids[0]
        name = _tasklist_name(pids[0])
        self._log(f"[ComfyUI] LISTENING PID on port {port}: {pids[0]} ({name})\n")
        self.status_var.set(f"ComfyUI PID {pids[0]} ({name})")
        self.refresh_comfy_status()

    def kill_external_pid(self) -> None:
        pid = self.comfy_external_pid
        if not pid:
            self._log("[ComfyUI] No external PID set. Click Find PID first.\n")
            return
        name = _tasklist_name(pid)
        ok = messagebox.askyesno(
            "Kill process",
            f"Kill PID {pid} ({name})?\nThis stops whatever is listening on the ComfyUI port.",
        )
        if not ok:
            return
        try:
            subprocess.check_call(["taskkill", "/PID", str(pid), "/F"])
            self._log(f"[ComfyUI] Killed PID {pid}.\n")
            self.comfy_external_pid = None
            self.after(300, self.refresh_comfy_status)
        except Exception as e:
            self._log(f"[ComfyUI] Failed to kill PID {pid}: {e}\n")

    # ---------- Pipeline ----------

    def run_pipeline(self, publish: bool) -> None:
        # Save editor contents before running
        self.save_editor_to_current_file()
        # Persist audio settings (so GUI + pipeline stay consistent)
        self._persist_audio_settings()

        cfg = self.cfg
        input_path = self.input_var.get().strip()
        if not input_path:
            messagebox.showerror("Missing input", "No input file path set.")
            return

        pipeline_script = str((Path(cfg.project_workdir) / cfg.pipeline_script).resolve())
        if not Path(pipeline_script).exists():
            messagebox.showerror("Missing pipeline script", f"Not found:\n{pipeline_script}")
            return

        cmd = [cfg.project_python, pipeline_script, "--input", input_path]
        if publish:
            cmd.append("--publish")

        env = os.environ.copy()
        env["HF_ENDPOINT"] = cfg.hf_endpoint

        # Pass TTS settings to pipeline (no CLI breakage; pipeline can read env vars)
        env["SKYED_TTS_RATE"] = _rate_string(int(cfg.tts_rate_percent))
        env["SKYED_VOICE_EN"] = str(cfg.voice_en)
        env["SKYED_VOICE_ZH"] = str(cfg.voice_zh)

        self._log(
            f"[Audio] pipeline env: SKYED_TTS_RATE={env['SKYED_TTS_RATE']} "
            f"SKYED_VOICE_EN={env['SKYED_VOICE_EN']} SKYED_VOICE_ZH={env['SKYED_VOICE_ZH']}\n"
        )

        self._pipeline_started_at = time.time()
        self._pipeline_publish_flag = publish
        self.pipe.start(cmd=cmd, cwd=cfg.project_workdir, env=env)

        self.nb.select(self.tab_logs)
        self.status_var.set("Pipeline running…")
        self.pipeline_state_var.set("Running" + (" (Publish)" if publish else ""))
        self.pipeline_last_var.set(f"Input: {Path(input_path).name}")

    def stop_pipeline(self) -> None:
        self.pipe.stop()
        self.status_var.set("Stop requested")

    # ---------- Status ticker + log pump ----------

    def _tick_status(self) -> None:
        if self.pipe.is_running() and self._pipeline_started_at:
            elapsed = time.time() - self._pipeline_started_at
            self.hint_var.set(f"Pipeline: {_fmt_duration(elapsed)}" + (" • publish" if self._pipeline_publish_flag else ""))
        else:
            self.hint_var.set("")

        if (not self.pipe.is_running()) and self._pipeline_started_at:
            elapsed = time.time() - self._pipeline_started_at
            self.pipeline_state_var.set("Idle")
            self.pipeline_last_var.set(f"Last run: {_fmt_duration(elapsed)}" + (" (Publish)" if self._pipeline_publish_flag else ""))
            self._pipeline_started_at = None

        self.after(500, self._tick_status)

    def _poll_logs(self) -> None:
        for line in self.comfy.drain():
            self._log(line)
        for line in self.pipe.drain():
            self._log(line)
        self.after(150, self._poll_logs)


def main():
    app = SkyEdGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
