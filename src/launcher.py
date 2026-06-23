from __future__ import annotations

import multiprocessing
import signal
import sys

multiprocessing.freeze_support()

_WORKER_FLAG = "--bot-worker"
if _WORKER_FLAG in sys.argv:
    import asyncio
    from src import bot

    bot._install_windows_selector_event_loop_policy()

    try:
        sys.exit(asyncio.run(bot.main()))
    except KeyboardInterrupt:
        sys.exit(0)

_SMOKE_TEST_FLAG = "--smoke-test"
if _SMOKE_TEST_FLAG in sys.argv:
    from types import SimpleNamespace
    from src import bot

    bot.LimpiBot(SimpleNamespace(command_guild_id=None))
    sys.exit(0)

import datetime
import os
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext

from PIL import Image, ImageDraw
import psutil
import pystray


def _fmt_dt(dt: datetime.datetime) -> str:
    ampm = "오전" if dt.hour < 12 else "오후"
    h = dt.hour % 12 or 12
    return f"{dt.year}년 {dt.month}월 {dt.day}일 {ampm} {h}시 {dt.minute:02d}분 {dt.second:02d}초"
if getattr(sys, "frozen", False):
    _BASE = os.path.dirname(sys.executable)
    PYTHON = sys.executable
    BOT_SCRIPT = None
    _MEIPASS = getattr(sys, "_MEIPASS", _BASE)
    ICON_PATH = os.path.join(_MEIPASS, "logo.ico")
else:
    _BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    PYTHON = sys.executable
    BOT_SCRIPT = None
    ICON_PATH = os.path.join(_BASE, "logo.ico")
MAX_LOG_LINES = 2000

C_BG       = "#0b0d12"
C_PANEL    = "#181b22"
C_SURFACE  = "#111318"
C_BORDER   = "#252a33"
C_TEXT     = "#f2f3f5"
C_SUBTEXT  = "#949ba4"
C_MUTED    = "#6d737d"
C_GREEN    = "#23a559"
C_RED      = "#f23f43"
C_YELLOW   = "#f0b232"
C_BLUE     = "#5865f2"
C_PEACH    = "#eb9f3a"
C_IDLE     = "#80848e"


_BELOW_NORMAL_PRIORITY = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_CPU_LOGICAL_COUNT = psutil.cpu_count(logical=True) or 1


class BotProcess:
    def __init__(self, on_log: callable) -> None:
        self.proc: subprocess.Popen | None = None
        self.start_time: datetime.datetime | None = None
        self.on_log = on_log
        self.test_mode = False
        self._lock = threading.Lock()
        self._psutil: psutil.Process | None = None

    def start(self, *, test_mode: bool = False) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return
            self.test_mode = test_mode
            self.start_time = datetime.datetime.now()
            mode_text = "테스트 모드" if self.test_mode else "일반 모드"
            self.on_log(
                f"[시스템] 봇 시작 중… ({mode_text}, {_fmt_dt(self.start_time)})"
            )
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            if getattr(sys, "frozen", False):
                cmd = [PYTHON, _WORKER_FLAG]
                cwd = _BASE
            else:
                cmd = [PYTHON, "-m", "src.bot"]
                cwd = _BASE
            if self.test_mode:
                cmd.append("--test")
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=cwd,
                env=env,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    | _CREATE_NEW_PROCESS_GROUP
                    | _BELOW_NORMAL_PRIORITY
                ),
            )
            try:
                self._psutil = psutil.Process(self.proc.pid)
                self._psutil.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._psutil = None
        thread = threading.Thread(target=self._read_output, daemon=True)
        thread.start()

    def _read_output(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.on_log(line.rstrip())
        code = self.proc.wait()
        self.on_log(f"[시스템] 프로세스 종료 (코드 {code})")

    def stop(self) -> None:
        with self._lock:
            if not (self.proc and self.proc.poll() is None):
                return
            self.on_log("[시스템] 봇 중지 중…")
            try:
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            except (ValueError, OSError):
                self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.on_log("[시스템] 정상 종료가 지연되어 프로세스를 종료합니다.")
            self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.on_log("[시스템] 프로세스가 응답하지 않아 강제 종료합니다.")
            self.proc.kill()
        self._psutil = None

    def restart(self, *, test_mode: bool | None = None) -> None:
        if test_mode is None:
            test_mode = self.test_mode
        self.stop()
        time.sleep(1)
        self.start(test_mode=test_mode)

    def is_running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def uptime_str(self) -> str:
        if not self.start_time or not self.is_running():
            return "–"
        delta = datetime.datetime.now() - self.start_time
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def start_time_str(self) -> str:
        if not self.start_time:
            return ""
        return _fmt_dt(self.start_time)

    def metrics(self) -> tuple[float | None, float | None]:
        proc = self._psutil
        if proc is None or not self.is_running():
            return None, None
        try:
            cpu = proc.cpu_percent(interval=None) / _CPU_LOGICAL_COUNT
            ram = proc.memory_info().rss / (1024 * 1024)
            return cpu, ram
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None, None


class LimpiLauncher:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Limpi Console")
        self.root.geometry("980x640")
        self.root.minsize(780, 500)
        self.root.configure(bg=C_BG)

        self.bot = BotProcess(on_log=self._queue_log)
        self._build_ui()
        self._build_tray()

        if os.path.exists(ICON_PATH):
            self.root.iconbitmap(ICON_PATH)
        self.root.protocol("WM_DELETE_WINDOW", self._confirm_close)
        self._update_status()
        self._queue_log("[시스템] 대기 중입니다. 시작 또는 Test 실행을 눌러 봇을 시작하세요.")

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=C_BG, padx=16, pady=14)
        header.pack(fill=tk.X)
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        title_frame = tk.Frame(header, bg=C_BG)
        title_frame.grid(row=0, column=0, sticky="w")

        tk.Label(
            title_frame,
            text="림피봇 터미널",
            fg=C_TEXT,
            bg=C_BG,
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_frame,
            text="Discord bot console",
            fg=C_SUBTEXT,
            bg=C_BG,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(1, 0))

        status_frame = tk.Frame(header, bg=C_BG)
        status_frame.grid(row=0, column=1, sticky="e")

        dot = tk.Label(status_frame, text="●", fg=C_IDLE, bg=C_BG, font=("Segoe UI", 14))
        dot.pack(side=tk.LEFT)
        self._dot = dot

        self._status_label = tk.Label(
            status_frame,
            text=" 대기 중",
            fg=C_SUBTEXT,
            bg=C_BG,
            font=("Segoe UI", 12, "bold"),
        )
        self._status_label.pack(side=tk.LEFT)

        stats = tk.Frame(header, bg=C_BG)
        stats.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        stats.grid_columnconfigure(0, weight=1)
        stats.grid_columnconfigure(1, weight=1)
        stats.grid_columnconfigure(2, weight=1)

        self._uptime_label = tk.Label(
            stats,
            text="가동 시간: -",
            fg=C_TEXT,
            bg=C_PANEL,
            font=("Segoe UI", 10),
            padx=12,
            pady=8,
            anchor="w",
        )
        self._uptime_label.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self._start_time_label = tk.Label(
            stats,
            text="시작: -",
            fg=C_SUBTEXT,
            bg=C_PANEL,
            font=("Segoe UI", 10),
            padx=12,
            pady=8,
            anchor="w",
        )
        self._start_time_label.grid(row=0, column=1, sticky="ew", padx=4)

        self._metric_label = tk.Label(
            stats,
            text="CPU: - / RAM: -",
            fg=C_BLUE,
            bg=C_PANEL,
            font=("Segoe UI", 10),
            padx=12,
            pady=8,
            anchor="w",
        )
        self._metric_label.grid(row=0, column=2, sticky="ew", padx=(8, 0))

        btn_frame = tk.Frame(self.root, bg=C_BG, padx=16, pady=0)
        btn_frame.pack(fill=tk.X, pady=(0, 12))
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)
        btn_frame.grid_columnconfigure(2, weight=1)
        btn_frame.grid_columnconfigure(3, weight=1)

        for index, (label, color, cmd) in enumerate([
            ("시작",   C_GREEN,  self._on_start),
            ("Test 실행", C_BLUE, self._on_start_test),
            ("재시작", C_PEACH,  self._on_restart),
            ("중지",   C_RED,    self._on_stop),
        ]):
            self._make_button(btn_frame, label, color, cmd).grid(
                row=0,
                column=index,
                sticky="ew",
                padx=(0 if index == 0 else 6, 0 if index == 3 else 6),
            )

        tk.Frame(self.root, bg=C_BORDER, height=1).pack(fill=tk.X)

        tk.Label(
            self.root,
            text="터미널",
            fg=C_SUBTEXT,
            bg=C_BG,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            padx=16,
            pady=0,
        ).pack(fill=tk.X, pady=(10, 6))

        log_frame = tk.Frame(self.root, bg=C_BORDER)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        self._log = scrolledtext.ScrolledText(
            log_frame,
            bg=C_SURFACE, fg=C_TEXT,
            font=("Cascadia Mono", 9),
            insertbackground=C_TEXT,
            state=tk.DISABLED,
            wrap=tk.WORD,
            relief=tk.FLAT, borderwidth=0,
            padx=10,
            pady=10,
            selectbackground="#2b2d31",
        )
        self._log.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        self._log.tag_config("error",   foreground=C_RED)
        self._log.tag_config("warning", foreground=C_YELLOW)
        self._log.tag_config("info",    foreground=C_BLUE)
        self._log.tag_config("success", foreground=C_GREEN)
        self._log.tag_config("system",  foreground=C_SUBTEXT, font=("Cascadia Mono", 9, "italic"))

    def _make_button(
        self,
        parent: tk.Misc,
        label: str,
        color: str,
        command: callable,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=label,
            command=command,
            bg=color,
            fg="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=14,
            pady=9,
            cursor="hand2",
            activebackground=color,
            activeforeground="#ffffff",
            borderwidth=0,
            highlightthickness=0,
        )

    def _build_tray(self) -> None:
        icon_img = Image.open(ICON_PATH).convert("RGBA") if os.path.exists(ICON_PATH) else self._make_fallback_icon()
        menu = pystray.Menu(
            pystray.MenuItem("관리 창 열기", self._tray_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("재시작", lambda _icon, _item: self._on_restart()),
            pystray.MenuItem("종료",   lambda _icon, _item: self._on_quit()),
        )
        self._tray = pystray.Icon("limpi", icon_img, "Limpi Bot", menu)
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _make_fallback_icon(self) -> Image.Image:
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([2, 2, size - 2, size - 2], fill=C_BLUE)
        draw.rectangle([18, 16, 28, 48], fill=C_BG)
        draw.rectangle([18, 40, 46, 48], fill=C_BG)
        return img

    def _tray_show(self, _icon=None, _item=None) -> None:
        self.root.after(0, self._show_window)

    def _queue_log(self, line: str) -> None:
        self.root.after(0, self._flush_log, line)

    def _flush_log(self, line: str) -> None:
        self._log.config(state=tk.NORMAL)

        lo = line.lower()
        if "error" in lo or "traceback" in lo or "exception" in lo:
            tag = "error"
        elif "warning" in lo or "warn" in lo:
            tag = "warning"
        elif "ready" in lo or "완료" in lo or "로그인" in lo:
            tag = "success"
        elif " info " in line or line.startswith("INFO"):
            tag = "info"
        elif line.startswith("[시스템]"):
            tag = "system"
        else:
            tag = ""

        self._log.insert(tk.END, line + "\n", tag or ())
        self._log.see(tk.END)

        total = int(self._log.index("end-1c").split(".")[0])
        if total > MAX_LOG_LINES:
            self._log.delete("1.0", f"{total - MAX_LOG_LINES}.0")

        self._log.config(state=tk.DISABLED)

    def _update_status(self) -> None:
        running = self.bot.is_running()
        if running:
            self._dot.config(fg=C_GREEN)
            status_text = " Test 실행 중" if self.bot.test_mode else " 실행 중"
            self._status_label.config(text=status_text, fg=C_GREEN)
            self._uptime_label.config(text=f"  가동 시간: {self.bot.uptime_str()}")
            self._start_time_label.config(text=f"시작: {self.bot.start_time_str()}")
            cpu, ram = self.bot.metrics()
            if cpu is None or ram is None:
                self._metric_label.config(text="  CPU: – / RAM: –", fg=C_SUBTEXT)
            else:
                color = C_RED if cpu > 50 or ram > 400 else (
                    C_YELLOW if cpu > 20 or ram > 200 else C_BLUE
                )
                self._metric_label.config(
                    text=f"  CPU: {cpu:4.1f}%   RAM: {ram:5.1f} MB",
                    fg=color,
                )
        else:
            self._dot.config(fg=C_IDLE)
            self._status_label.config(text=" 대기 중", fg=C_SUBTEXT)
            self._uptime_label.config(text="가동 시간: -")
            self._start_time_label.config(text="시작: -")
            self._metric_label.config(text="CPU: - / RAM: -", fg=C_SUBTEXT)
        self.root.after(1000, self._update_status)

    def _clear_log(self) -> None:
        self._log.config(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.config(state=tk.DISABLED)

    def _on_start(self) -> None:
        self._run_requested_mode(test_mode=False)

    def _on_start_test(self) -> None:
        self._run_requested_mode(test_mode=True)

    def _on_stop(self) -> None:
        threading.Thread(target=self.bot.stop, daemon=True).start()

    def _on_restart(self) -> None:
        if not self.bot.is_running():
            self._queue_log("[시스템] 재시작은 봇이 실행 중일 때만 사용할 수 있습니다.")
            return
        self._clear_log()
        threading.Thread(target=self.bot.restart, daemon=True).start()

    def _run_requested_mode(self, *, test_mode: bool) -> None:
        if self.bot.is_running() and self.bot.test_mode == test_mode:
            mode_text = "테스트 모드" if test_mode else "일반 모드"
            self._queue_log(f"[시스템] 이미 {mode_text}로 실행 중입니다.")
            return
        self._clear_log()
        target = self.bot.restart if self.bot.is_running() else self.bot.start
        threading.Thread(
            target=target,
            kwargs={"test_mode": test_mode},
            daemon=True,
        ).start()

    def _hide_window(self) -> None:
        self.root.withdraw()

    def _confirm_close(self) -> None:
        choice = messagebox.askyesnocancel(
            "림피 종료",
            "림피를 시스템 트레이에 남겨둘까요?\n\n"
            "예: 창만 닫고 백그라운드에서 계속 실행\n"
            "아니요: 봇과 앱 완전 종료\n"
            "취소: 돌아가기",
            parent=self.root,
        )
        if choice is None:
            return
        if choice:
            self._hide_window()
            return
        self._on_quit()

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_quit(self) -> None:
        self.bot.stop()
        self._tray.stop()
        self.root.after(0, self.root.destroy)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    LimpiLauncher().run()
