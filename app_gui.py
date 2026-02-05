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
    )


class ProcessRunner:
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
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                break
        return out


def _extract_host_port(url: str) -> Tuple[str, int]:
    u = urlparse(url)
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    return host, port


def _netstat_listening_pids(port: int) -> List[int]:
    """
    Windows netstat parser. Returns PIDs that are LISTENING on given local port.
    If command fails, returns [].
    """
    try:
        # netstat output can be localized; still contains LISTENING most often.
        out = subprocess.check_output(["netstat", "-ano", "-p", "TCP"], text=True, errors="ignore")
    except Exception:
        try:
            out = subprocess.check_output(["netstat", "-ano"], text=True, errors="ignore")
        except Exception:
            return []

    pids: List[int] = []
    target = f":{port}"

    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # Typical: TCP  127.0.0.1:8188  0.0.0.0:0  LISTENING  1234
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
            pid = int(pid_str)
            pids.append(pid)
        except Exception:
            pass

    return sorted(set(pids))


def _tasklist_name(pid: int) -> str:
    try:
        out = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}"], text=True, errors="ignore")
        # Second line usually contains the image name.
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if len(lines) >= 3:
            # e.g.: python.exe   1234 ...
            return lines[2].split()[0]
    except Exception:
        pass
    return "unknown"


class SkyEdGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SkyEd Automation")
        self.geometry("1320x820")

        self.root_dir = Path(__file__).resolve().parent
        cfg_path = self.root_dir / CONFIG_NAME
        if not cfg_path.exists():
            messagebox.showerror("Missing config", f"{CONFIG_NAME} not found in:\n{self.root_dir}")
            self.destroy()
            return

        self.cfg = load_config(cfg_path)

        self.comfy = ProcessRunner("ComfyUI")
        self.pipe = ProcessRunner("Pipeline")

        # Track external ComfyUI (not started by GUI)
        self.comfy_external_pid: Optional[int] = None

        self._style()
        self._build_ui()
        self._poll_logs()

        # Auto check on start
        self.after(300, self.refresh_comfy_status)
        self.after(600, self.auto_start_comfy_if_needed)

    def _style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TButton", padding=(10, 6))
        style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Small.TLabel", font=("Segoe UI", 9))

    def _build_ui(self) -> None:
        vpan = ttk.Panedwindow(self, orient="vertical")
        vpan.pack(fill="both", expand=True, padx=10, pady=10)

        top = ttk.Frame(vpan)
        bottom = ttk.Frame(vpan)
        vpan.add(top, weight=3)
        vpan.add(bottom, weight=2)

        hpan = ttk.Panedwindow(top, orient="horizontal")
        hpan.pack(fill="both", expand=True)

        left = ttk.Frame(hpan)
        right = ttk.Frame(hpan)
        hpan.add(left, weight=1)
        hpan.add(right, weight=2)

        # LEFT controls
        ttk.Label(left, text="Controls", style="Header.TLabel").pack(anchor="w", pady=(0, 6))

        host, port = _extract_host_port(self.cfg.comfy_url)
        ttk.Label(left, text=f"ComfyUI endpoint: {host}:{port}", style="Small.TLabel").pack(anchor="w")

        btn_row = ttk.Frame(left)
        btn_row.pack(fill="x", pady=(8, 10))
        ttk.Button(btn_row, text="Refresh Status", command=self.refresh_comfy_status).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Start ComfyUI", command=self.start_comfy).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Stop ComfyUI", command=self.stop_comfy).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Open UI", command=self.open_comfy_ui).pack(side="left", padx=4)

        pid_row = ttk.Frame(left)
        pid_row.pack(fill="x", pady=(0, 8))
        ttk.Button(pid_row, text="Find PID (8188)", command=self.find_external_pid).pack(side="left", padx=4)
        ttk.Button(pid_row, text="Kill PID", command=self.kill_external_pid).pack(side="left", padx=4)

        ttk.Separator(left).pack(fill="x", pady=10)

        ttk.Label(left, text="Input file path").pack(anchor="w")
        self.input_var = tk.StringVar(value=str(self.root_dir / "homework.txt"))
        ttk.Entry(left, textvariable=self.input_var).pack(fill="x", pady=(2, 6))

        file_btns = ttk.Frame(left)
        file_btns.pack(fill="x", pady=(0, 10))
        ttk.Button(file_btns, text="Browse", command=self.browse_input).pack(side="left", padx=4)
        ttk.Button(file_btns, text="Load → Editor", command=self.load_file_into_editor).pack(side="left", padx=4)
        ttk.Button(file_btns, text="Save Editor → File", command=self.save_editor_to_current_file).pack(side="left", padx=4)

        ttk.Separator(left).pack(fill="x", pady=10)

        run_btns = ttk.Frame(left)
        run_btns.pack(fill="x", pady=(0, 8))
        ttk.Button(run_btns, text="Run (generate)", command=lambda: self.run_pipeline(publish=False)).pack(fill="x", pady=3)
        ttk.Button(run_btns, text="Run + Publish", command=lambda: self.run_pipeline(publish=True)).pack(fill="x", pady=3)
        ttk.Button(run_btns, text="Stop Pipeline", command=self.stop_pipeline).pack(fill="x", pady=3)

        ttk.Separator(left).pack(fill="x", pady=10)

        self.status_var = tk.StringVar(value="Status: idle")
        ttk.Label(left, textvariable=self.status_var).pack(anchor="w")

        # RIGHT editor
        header = ttk.Frame(right)
        header.pack(fill="x")
        ttk.Label(header, text="Homework Editor", style="Header.TLabel").pack(side="left")

        edit_btns = ttk.Frame(right)
        edit_btns.pack(fill="x", pady=(6, 6))
        ttk.Button(edit_btns, text="New", command=self.new_editor).pack(side="left", padx=4)
        ttk.Button(edit_btns, text="Load...", command=self.load_file_into_editor).pack(side="left", padx=4)
        ttk.Button(edit_btns, text="Save", command=self.save_editor_to_current_file).pack(side="left", padx=4)
        ttk.Button(edit_btns, text="Save As...", command=self.save_editor_as).pack(side="left", padx=4)

        self.editor = ScrolledText(right, wrap="word", font=("Consolas", 11))
        self.editor.pack(fill="both", expand=True)

        # BOTTOM logs
        ttk.Label(bottom, text="Logs", style="Header.TLabel").pack(anchor="w", pady=(0, 6))
        self.log = ScrolledText(bottom, wrap="word", font=("Consolas", 10), height=12)
        self.log.pack(fill="both", expand=True)

        self._log("GUI ready.\n")

        self.after(200, self.load_file_into_editor)

    def _log(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")

    def browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select homework input text",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.input_var.set(path)

    def new_editor(self) -> None:
        self.editor.delete("1.0", "end")
        self.status_var.set("Status: editor cleared")

    def load_file_into_editor(self) -> None:
        p = Path(self.input_var.get().strip())
        if not p.exists():
            self.status_var.set("Status: file not found (editor unchanged)")
            return
        txt = p.read_text(encoding="utf-8", errors="ignore")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", txt)
        self.status_var.set(f"Status: loaded {p.name} into editor")

    def save_editor_to_current_file(self) -> None:
        p = Path(self.input_var.get().strip())
        if not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        txt = self.editor.get("1.0", "end").rstrip() + "\n"
        p.write_text(txt, encoding="utf-8")
        self.status_var.set(f"Status: saved editor → {p.name}")

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

    # ---------- ComfyUI status / process control ----------

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
            self.status_var.set(f"Status: ComfyUI LISTENING on {port} (PID {pids[0]}: {name})")
            self._log(f"[ComfyUI] LISTENING detected on port {port} (PID {pids[0]}: {name}).\n")
            return

        # No LISTENING found: fall back to HTTP check (sometimes netstat misses briefly)
        if self.comfy_http_reachable():
            self.comfy_external_pid = None
            self.status_var.set("Status: ComfyUI reachable (PID unknown)")
            self._log("[ComfyUI] reachable (HTTP OK), PID unknown.\n")
        else:
            self.comfy_external_pid = None
            self.status_var.set("Status: ComfyUI not reachable")
            self._log("[ComfyUI] not reachable.\n")

    def auto_start_comfy_if_needed(self) -> None:
        # If already reachable or LISTENING, do nothing.
        if self.comfy_http_reachable():
            self.status_var.set("Status: ComfyUI reachable (no auto-start)")
            self._log("[ComfyUI] already reachable → no auto-start.\n")
            return

        _, port = _extract_host_port(self.cfg.comfy_url)
        if _netstat_listening_pids(port):
            self.status_var.set("Status: ComfyUI LISTENING (no auto-start)")
            self._log("[ComfyUI] LISTENING detected → no auto-start.\n")
            return

        self._log("[ComfyUI] not running → auto start.\n")
        self.start_comfy()

    def start_comfy(self) -> None:
        # Hard guard: never start if already reachable/listening
        if self.comfy_http_reachable():
            self._log("[ComfyUI] reachable → NOT starting a second instance.\n")
            self.status_var.set("Status: ComfyUI already running")
            return

        _, port = _extract_host_port(self.cfg.comfy_url)
        pids = _netstat_listening_pids(port)
        if pids:
            self.comfy_external_pid = pids[0]
            name = _tasklist_name(pids[0])
            self._log(f"[ComfyUI] LISTENING detected (PID {pids[0]}: {name}) → NOT starting.\n")
            self.status_var.set(f"Status: ComfyUI already running (PID {pids[0]})")
            return

        cfg = self.cfg
        cmd = [cfg.comfy_python] + cfg.comfy_args

        env = os.environ.copy()
        env["HF_ENDPOINT"] = cfg.hf_endpoint

        self.comfy.start(cmd=cmd, cwd=cfg.comfy_workdir, env=env)
        self.status_var.set("Status: starting ComfyUI...")
        self.after(700, self._poll_comfy_ready)

    def _poll_comfy_ready(self) -> None:
        if self.comfy_http_reachable():
            self.status_var.set("Status: ComfyUI running (managed by GUI)")
            self._log("[ComfyUI] READY (HTTP OK)\n")
            return
        if not self.comfy.is_running():
            # if it failed, refresh status to see if external instance exists
            self._log("[ComfyUI] failed to start (process exited). Refreshing status...\n")
            self.refresh_comfy_status()
            return
        self.after(700, self._poll_comfy_ready)

    def stop_comfy(self) -> None:
        # Only stop if GUI started it
        if self.comfy.is_running():
            self.comfy.stop()
            return

        # If external PID is known, we don't auto-kill here.
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
            self._log(f"[ComfyUI] No LISTENING PID found on port {port}.\n")
            self.status_var.set("Status: ComfyUI PID not found")
            return
        self.comfy_external_pid = pids[0]
        name = _tasklist_name(pids[0])
        self._log(f"[ComfyUI] LISTENING PID on port {port}: {pids[0]} ({name})\n")
        self.status_var.set(f"Status: ComfyUI PID {pids[0]} ({name})")

    def kill_external_pid(self) -> None:
        pid = self.comfy_external_pid
        if not pid:
            self._log("[ComfyUI] No external PID set. Click Find PID first.\n")
            return
        name = _tasklist_name(pid)
        ok = messagebox.askyesno("Kill process", f"Kill PID {pid} ({name})?\nThis will stop whatever is on the ComfyUI port.")
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
        # Always save editor to file before running
        self.save_editor_to_current_file()

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

        self.pipe.start(cmd=cmd, cwd=cfg.project_workdir, env=env)
        self.status_var.set("Status: pipeline running")

    def stop_pipeline(self) -> None:
        self.pipe.stop()

    # ---------- Log pump ----------

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
