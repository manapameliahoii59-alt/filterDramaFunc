"""任务运行器：在后台线程里以 subprocess 方式调用业务脚本。

设计要点
- 业务代码零改动：跑的就是 `python xxx.py ...`，与 .bat 完全同一条路径；
- 实时可观测：子进程输出逐行回传，界面可即时刷新日志与进度；
- 可取消：Windows 下用 taskkill /T 连子进程树一起结束；
- 不弹黑窗：CREATE_NO_WINDOW，浏览器窗口由 Playwright 自己拉起，不受影响。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from desktop_app.common.constants import PROJECT_ROOT

# 退出码约定（非系统退出码，仅用于界面提示）
EXIT_CANCELLED = -2
EXIT_LAUNCH_FAILED = -1

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0


def build_child_env() -> dict[str, str]:
    """子进程环境变量：强制无缓冲 + UTF-8，保证日志能逐行实时到达界面。"""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class TaskRunner(QThread):
    """在独立线程中运行一条命令行任务，逐行回传输出。"""

    line = Signal(str)
    done = Signal(int)
    failed = Signal(str)

    def __init__(self, cmd: list[str], cwd: Path | str | None = None, parent=None):
        super().__init__(parent)
        self._cmd = [str(c) for c in cmd]
        self._cwd = str(cwd or PROJECT_ROOT)
        self._proc: subprocess.Popen | None = None
        self._cancelled = False

    @property
    def command_text(self) -> str:
        return " ".join(self._cmd)

    def is_running(self) -> bool:
        return self.isRunning()

    def run(self) -> None:  # noqa: D102 - QThread 入口
        try:
            self._proc = subprocess.Popen(
                self._cmd,
                cwd=self._cwd,
                env=build_child_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_NO_WINDOW,
            )
        except Exception as exc:  # 解释器缺失、脚本不存在等
            self.failed.emit(f"无法启动任务：{type(exc).__name__}: {exc}")
            self.done.emit(EXIT_LAUNCH_FAILED)
            return

        stdout = self._proc.stdout
        try:
            if stdout is not None:
                for raw in stdout:
                    if self._cancelled:
                        break
                    self.line.emit(raw.rstrip("\r\n"))
        except Exception as exc:
            self.failed.emit(f"读取子进程输出失败：{type(exc).__name__}: {exc}")
        finally:
            try:
                code = self._proc.wait(timeout=15)
            except Exception:
                code = EXIT_CANCELLED
            if stdout is not None:
                try:
                    stdout.close()
                except Exception:
                    pass
            self.done.emit(EXIT_CANCELLED if self._cancelled else code)

    def cancel(self) -> None:
        """请求取消：结束整个子进程树。"""
        self._cancelled = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    creationflags=_NO_WINDOW,
                )
            else:
                proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
