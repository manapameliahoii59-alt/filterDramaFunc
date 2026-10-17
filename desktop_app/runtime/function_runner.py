"""在后台线程执行纯函数，避免读文件 / 解析 JSON 阻塞 UI 线程。

Qt 要求界面对象只能在主线程创建，因此本模块只用于「算数据」：
后台算完通过信号回到主线程，再更新界面。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal


class FunctionRunner(QThread):
    """把一次性的耗时计算（读文件、解析 JSON、统计行数）放到后台线程。"""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args, parent=None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._runner_ref = None  # 由调用方持有，防止被 GC

    def run(self) -> None:  # noqa: D102 - QThread 入口
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # 后台线程异常不能逃逸，否则会终止进程
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.succeeded.emit(result)


def run_in_background(
    parent,
    fn: Callable[..., Any],
    *args,
    on_success: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    **kwargs,
) -> FunctionRunner:
    """便捷入口：在后台跑 fn，并在主线程回调结果。

    parent 上的 _bg_runners 列表会持有线程引用，避免被垃圾回收。
    """
    runner = FunctionRunner(fn, *args, parent=parent, **kwargs)
    holders = getattr(parent, "_bg_runners", None)
    if holders is None:
        holders = []
        setattr(parent, "_bg_runners", holders)
    holders.append(runner)

    def _cleanup(*_args) -> None:
        if runner in holders:
            holders.remove(runner)

    runner.finished.connect(_cleanup)
    if on_success is not None:
        runner.succeeded.connect(on_success)
    if on_error is not None:
        runner.failed.connect(on_error)
    runner.start()
    return runner
