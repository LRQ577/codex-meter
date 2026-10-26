#!/usr/bin/env python3
"""Always-on-top Codex quota and NVIDIA GPU monitor."""

from __future__ import annotations

import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import time
import fcntl
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import psutil
from PySide6.QtCore import QObject, QPoint, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


APP_VERSION = "0.1.0"
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
TODO_FILE = DATA_HOME / "codex-meter" / "todos.json"
INSTANCE_LOCK_FILE = TODO_FILE.parent / "app.lock"
CODEX_DATABASE = CODEX_HOME / "sqlite" / "codex-dev.db"


def _run(command: list[str], timeout: float = 5.0) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"命令退出码 {result.returncode}")
    return result.stdout


def read_gpus() -> list[dict[str, Any]]:
    fields = "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"
    output = _run(
        ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
        timeout=4,
    )
    gpus = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 7:
            continue
        index, name, util, memory_used, memory_total, temp, power = parts
        gpus.append(
            {
                "index": int(index),
                "name": name.replace("NVIDIA ", ""),
                "util": int(float(util)),
                "memory_used": int(float(memory_used)),
                "memory_total": int(float(memory_total)),
                "temp": int(float(temp)),
                "power": float(power),
            }
        )
    return gpus


def read_system() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    return {
        "cpu_percent": round(psutil.cpu_percent(interval=0.15)),
        "cpu_count": psutil.cpu_count(logical=True) or 0,
        "memory_percent": round(memory.percent),
        "memory_used": memory.used,
        "memory_total": memory.total,
    }


def format_gib(value: int) -> str:
    return f"{value / (1024 ** 3):.1f} GiB"


def read_automation_stats(account_id: str | None) -> dict[str, int]:
    if not CODEX_DATABASE.is_file():
        return {"total": 0, "active": 0}
    try:
        connection = sqlite3.connect(CODEX_DATABASE.as_uri() + "?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return {"total": 0, "active": 0}
    try:
        if account_id:
            row = connection.execute(
                """SELECT COUNT(*), SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END)
                   FROM automations WHERE account_id = ? OR account_id IS NULL""",
                (account_id,),
            ).fetchone()
        else:
            row = connection.execute(
                """SELECT COUNT(*), SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END)
                   FROM automations"""
            ).fetchone()
        return {"total": int(row[0] or 0), "active": int(row[1] or 0)}
    except sqlite3.Error:
        return {"total": 0, "active": 0}
    finally:
        connection.close()


def load_todos() -> list[dict[str, Any]]:
    try:
        data = json.loads(TODO_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            todos = []
            for item in data:
                if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                    continue
                todos.append(
                    {
                        "text": str(item["text"]).strip(),
                        "done": bool(item.get("done", False)),
                        "source": item.get("source"),
                        "source_id": item.get("source_id"),
                    }
                )
            return todos
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []


def save_todos(todos: list[dict[str, Any]]) -> None:
    TODO_FILE.parent.mkdir(parents=True, exist_ok=True)
    TODO_FILE.parent.chmod(0o700)
    temporary = TODO_FILE.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(todos, handle, ensure_ascii=False, indent=2)
    temporary.replace(TODO_FILE)


def acquire_instance_lock():
    """Keep desktop/autostart/manual launches from creating overlapping windows."""
    INSTANCE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    INSTANCE_LOCK_FILE.parent.chmod(0o700)
    lock = INSTANCE_LOCK_FILE.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    return lock


def read_codex_todo_sources(account_id: str | None) -> list[dict[str, str]]:
    if not CODEX_DATABASE.is_file():
        return []
    try:
        connection = sqlite3.connect(CODEX_DATABASE.as_uri() + "?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return []
    try:
        inbox = connection.execute(
            "SELECT id, COALESCE(title, description, 'Codex 待处理事项') FROM inbox_items WHERE read_at IS NULL"
        ).fetchall()
        return [
            {"source": "inbox", "source_id": str(item_id), "text": f"需处理 · {title}"}
            for item_id, title in inbox
        ]
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def read_codex_usage() -> dict[str, Any]:
    """Read the same account limit snapshot used by Codex `/status`."""
    process = subprocess.Popen(
        ["codex", "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdin and process.stdout and process.stderr
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "codex-meter", "title": "Codex Meter", "version": APP_VERSION},
                "capabilities": {},
            },
        },
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "account/rateLimits/read",
            "params": {"excludeResetCreditDetails": True},
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "thread/list",
            "params": {
                "limit": 1000,
                "archived": False,
                "useStateDbOnly": True,
                "sourceKinds": [
                    "cli", "vscode", "exec", "appServer", "subAgent",
                    "subAgentReview", "subAgentCompact", "subAgentThreadSpawn",
                    "subAgentOther", "unknown",
                ],
            },
        },
    ]
    try:
        for request in requests:
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            process.stdin.flush()
        deadline = time.monotonic() + 12
        errors: list[str] = []
        usage_result: dict[str, Any] | None = None
        thread_result: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout, process.stderr], [], [], 0.5)
            for stream in ready:
                line = stream.readline()
                if not line:
                    continue
                if stream is process.stderr:
                    errors.append(line.strip())
                    continue
                message = json.loads(line)
                if message.get("id") in (2, 3):
                    if "error" in message:
                        raise RuntimeError(message["error"].get("message", "读取 Codex 用量失败"))
                    if message["id"] == 2:
                        usage_result = message["result"]
                    else:
                        thread_result = message["result"]
                    if usage_result is not None and thread_result is not None:
                        threads = thread_result.get("data", [])
                        subagent_sources = {
                            "subAgent", "subAgentReview", "subAgentCompact",
                            "subAgentThreadSpawn", "subAgentOther",
                        }
                        usage_result["agentStats"] = {
                            "total": len(threads),
                            "subagents": sum(1 for thread in threads if thread.get("source") in subagent_sources),
                            "active": sum(
                                1 for thread in threads
                                if (thread.get("status") or {}).get("type") == "active"
                            ),
                            "scope": "local",
                        }
                        usage_result["automationStats"] = read_automation_stats(
                            usage_result.get("accountId")
                        )
                        usage_result["codexTodos"] = read_codex_todo_sources(
                            usage_result.get("accountId")
                        )
                        return usage_result
        raise TimeoutError("读取 Codex 用量超时" + ((": " + errors[-1]) if errors else ""))
    finally:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()


class WorkerSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.done.emit(self.fn())
        except Exception as exc:  # UI should show sensor failures instead of crashing.
            self.signals.failed.emit(str(exc))


class QuotaBar(QWidget):
    def __init__(self, title: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        row = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setObjectName("muted")
        self.value = QLabel("—")
        self.value.setObjectName("metric")
        row.addWidget(self.title)
        row.addStretch()
        row.addWidget(self.value)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(7)
        self.reset = QLabel("正在读取…")
        self.reset.setObjectName("caption")
        layout.addLayout(row)
        layout.addWidget(self.bar)
        layout.addWidget(self.reset)

    def update_value(self, window: dict[str, Any] | None) -> None:
        if not window:
            self.value.setText("不可用")
            self.bar.setValue(0)
            self.reset.setText("当前账户未返回此周期")
            return
        used = max(0, min(100, int(window.get("usedPercent", 0))))
        self.value.setText(f"{100 - used}% 剩余")
        self.bar.setValue(used)
        reset_at = window.get("resetsAt")
        duration = window.get("windowDurationMins")
        prefix = "5 小时窗口" if duration == 300 else "7 天窗口" if duration == 10080 else "额度窗口"
        if reset_at:
            reset_text = datetime.fromtimestamp(reset_at).strftime("%m/%d %H:%M")
            self.reset.setText(f"{prefix} · {reset_text} 重置")
        else:
            self.reset.setText(prefix)


class GpuCard(QFrame):
    def __init__(self, gpu: dict[str, Any]):
        super().__init__()
        self.setObjectName("gpuCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        top = QHBoxLayout()
        name = QLabel(f"GPU {gpu['index']}")
        name.setObjectName("gpuTitle")
        model = QLabel(gpu["name"].replace("GeForce ", ""))
        model.setObjectName("caption")
        top.addWidget(name)
        top.addStretch()
        top.addWidget(model)
        layout.addLayout(top)
        self.metric_label = QLabel()
        self.metric_label.setObjectName("gpuMetric")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.details = QLabel()
        self.details.setObjectName("caption")
        layout.addWidget(self.metric_label)
        layout.addWidget(self.bar)
        layout.addWidget(self.details)
        self.update_gpu(gpu)

    def update_gpu(self, gpu: dict[str, Any]) -> None:
        util = gpu["util"]
        memory_percent = round(gpu["memory_used"] / max(gpu["memory_total"], 1) * 100)
        self.metric_label.setText(f"{util}% · 显存 {memory_percent}%")
        self.bar.setValue(util)
        self.details.setText(
            f"{gpu['memory_used'] / 1024:.1f}/{gpu['memory_total'] / 1024:.1f} GiB · "
            f"{gpu['temp']}° · {gpu['power']:.0f}W"
        )


class SystemCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("systemCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(16)
        self.cpu_value, self.cpu_bar, cpu_box = self._metric("CPU", "—")
        self.memory_value, self.memory_bar, memory_box = self._metric("内存", "—")
        layout.addLayout(cpu_box, 1)
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(divider)
        layout.addLayout(memory_box, 1)

    @staticmethod
    def _metric(title: str, value: str):
        box = QVBoxLayout()
        box.setSpacing(5)
        label = QLabel(title)
        label.setObjectName("muted")
        value_label = QLabel(value)
        value_label.setObjectName("systemMetric")
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        box.addWidget(label)
        box.addWidget(value_label)
        box.addWidget(bar)
        return value_label, bar, box

    def update_system(self, data: dict[str, Any]) -> None:
        cpu = data["cpu_percent"]
        memory = data["memory_percent"]
        self.cpu_value.setText(f"{cpu}%  ·  {data['cpu_count']} 线程")
        self.cpu_bar.setValue(cpu)
        self.memory_value.setText(f"{memory}%  ·  {format_gib(data['memory_used'])}")
        self.memory_bar.setValue(memory)
        self.memory_value.setToolTip(
            f"已用 {format_gib(data['memory_used'])} / 共 {format_gib(data['memory_total'])}"
        )


class TodoRow(QFrame):
    changed = Signal()
    remove_requested = Signal(object)

    def __init__(
        self,
        text: str,
        done: bool = False,
        source: str | None = None,
        source_id: str | None = None,
    ):
        super().__init__()
        self.source = source
        self.source_id = source_id
        self.setObjectName("todoRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(7)
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(done)
        self.label = QLabel(text)
        self.label.setObjectName("todoDone" if done else "todoText")
        self.label.setWordWrap(True)
        if source:
            self.label.setToolTip("由 Codex/ChatGPT 桌面端自动同步")
        remove = QPushButton("×")
        remove.setObjectName("todoRemove")
        remove.setToolTip("删除待办")
        remove.clicked.connect(lambda: self.remove_requested.emit(self))
        self.checkbox.toggled.connect(self._on_toggled)
        layout.addWidget(self.checkbox)
        layout.addWidget(self.label, 1)
        layout.addWidget(remove)

    def _on_toggled(self, done: bool) -> None:
        self.label.setObjectName("todoDone" if done else "todoText")
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)
        self.changed.emit()

    def data(self) -> dict[str, Any]:
        return {
            "text": self.label.text(),
            "done": self.checkbox.isChecked(),
            "source": self.source,
            "source_id": self.source_id,
        }


class MeterWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Codex Meter")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(390)
        self._drag_offset: QPoint | None = None
        self._codex_busy = False
        self._gpu_busy = False
        self._system_busy = False
        self._gpu_cards: dict[int, GpuCard] = {}
        self.pool = QThreadPool.globalInstance()

        self.panel = QFrame(self)
        self.panel.setObjectName("panel")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 125))
        self.panel.setGraphicsEffect(shadow)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.addWidget(self.panel)
        root = QVBoxLayout(self.panel)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        mark = QLabel("C")
        mark.setObjectName("mark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("CODEX METER")
        title.setObjectName("title")
        self.status = QLabel("正在同步")
        self.status.setObjectName("caption")
        title_box.addWidget(title)
        title_box.addWidget(self.status)
        self.refresh_button = QPushButton("↻")
        self.refresh_button.setToolTip("立即刷新")
        self.refresh_button.clicked.connect(self.refresh_all)
        close = QPushButton("×")
        close.setToolTip("关闭")
        close.clicked.connect(self.close)
        header.addWidget(mark)
        header.addSpacing(4)
        header.addLayout(title_box)
        header.addStretch()
        header.addWidget(self.refresh_button)
        header.addWidget(close)
        root.addLayout(header)

        codex_header = QHBoxLayout()
        codex_label = QLabel("CODEX 用量")
        codex_label.setObjectName("section")
        self.plan = QLabel("—")
        self.plan.setObjectName("pill")
        self.tasks = QLabel("任务 —")
        self.tasks.setObjectName("pill")
        codex_header.addWidget(codex_label)
        codex_header.addStretch()
        codex_header.addWidget(self.tasks)
        codex_header.addWidget(self.plan)
        root.addLayout(codex_header)
        self.primary = QuotaBar("短周期额度")
        self.secondary = QuotaBar("每周额度")
        root.addWidget(self.primary)
        root.addWidget(self.secondary)
        self.codex_error = QLabel("")
        self.codex_error.setObjectName("error")
        self.codex_error.setWordWrap(True)
        self.codex_error.hide()
        root.addWidget(self.codex_error)

        system_header = QHBoxLayout()
        system_label = QLabel("系统资源")
        system_label.setObjectName("section")
        self.system_summary = QLabel("检测中")
        self.system_summary.setObjectName("caption")
        system_header.addWidget(system_label)
        system_header.addStretch()
        system_header.addWidget(self.system_summary)
        root.addLayout(system_header)
        self.system_card = SystemCard()
        root.addWidget(self.system_card)

        todo_header = QHBoxLayout()
        todo_label = QLabel("待办清单")
        todo_label.setObjectName("section")
        self.todo_summary = QLabel("0 项")
        self.todo_summary.setObjectName("caption")
        self.todo_summary.setToolTip("每分钟同步未读的 Codex 待处理结果")
        todo_header.addWidget(todo_label)
        todo_header.addStretch()
        todo_header.addWidget(self.todo_summary)
        root.addLayout(todo_header)
        todo_input_row = QHBoxLayout()
        self.todo_input = QLineEdit()
        self.todo_input.setPlaceholderText("添加待办，按 Enter 保存")
        self.todo_input.returnPressed.connect(self.add_todo)
        add_todo = QPushButton("+")
        add_todo.setObjectName("todoAdd")
        add_todo.setToolTip("添加待办")
        add_todo.clicked.connect(self.add_todo)
        todo_input_row.addWidget(self.todo_input, 1)
        todo_input_row.addWidget(add_todo)
        root.addLayout(todo_input_row)
        self.todo_scroll = QScrollArea()
        self.todo_scroll.setObjectName("todoScroll")
        self.todo_scroll.setWidgetResizable(True)
        self.todo_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.todo_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.todo_scroll.setFixedHeight(42)
        self.todo_container = QWidget()
        self.todo_layout = QVBoxLayout(self.todo_container)
        self.todo_layout.setContentsMargins(0, 0, 0, 0)
        self.todo_layout.setSpacing(4)
        self.todo_layout.addStretch()
        self.todo_scroll.setWidget(self.todo_container)
        root.addWidget(self.todo_scroll)
        self.todo_rows: list[TodoRow] = []
        for todo in load_todos():
            self._append_todo(
                todo["text"], todo["done"], todo.get("source"), todo.get("source_id")
            )
        self._update_todo_summary()
        self._todo_mtime_ns = self._todo_file_mtime()

        gpu_header = QHBoxLayout()
        gpu_label = QLabel("NVIDIA GPU")
        gpu_label.setObjectName("section")
        self.gpu_summary = QLabel("检测中")
        self.gpu_summary.setObjectName("caption")
        gpu_header.addWidget(gpu_label)
        gpu_header.addStretch()
        gpu_header.addWidget(self.gpu_summary)
        root.addLayout(gpu_header)
        self.gpu_box = QGridLayout()
        self.gpu_box.setContentsMargins(0, 0, 0, 0)
        self.gpu_box.setSpacing(8)
        root.addLayout(self.gpu_box)
        self.gpu_error = QLabel("")
        self.gpu_error.setObjectName("error")
        self.gpu_error.setWordWrap(True)
        self.gpu_error.hide()
        root.addWidget(self.gpu_error)

        self._apply_style()
        self.gpu_timer = QTimer(self)
        self.gpu_timer.timeout.connect(self.refresh_gpu)
        self.gpu_timer.timeout.connect(self.refresh_system)
        self.gpu_timer.start(2000)
        self.codex_timer = QTimer(self)
        self.codex_timer.timeout.connect(self.refresh_codex)
        self.codex_timer.start(60_000)
        self.todo_timer = QTimer(self)
        self.todo_timer.timeout.connect(self.reload_external_todos)
        self.todo_timer.start(1500)
        QTimer.singleShot(0, self.refresh_all)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { color: #f4f4f0; font-family: Inter, "Noto Sans CJK SC", sans-serif; }
            #panel { background: rgba(16, 18, 17, 245); border: 1px solid #343835; border-radius: 18px; }
            #mark { min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
                    background: #d7ff45; color: #141613; border-radius: 9px; font-size: 17px; font-weight: 800; }
            #title { color: #f8f8f3; font-size: 14px; font-weight: 800; letter-spacing: 1.6px; }
            #section { color: #d7ff45; font-size: 11px; font-weight: 800; letter-spacing: 1.2px; }
            #muted { color: #a5aaa5; font-size: 12px; }
            #caption { color: #737a74; font-size: 10px; }
            #metric { color: #f7f7f2; font-size: 12px; font-weight: 700; }
            #gpuTitle { color: #e9e9e4; font-size: 11px; font-weight: 800; }
            #gpuMetric { color: #f7f7f2; font-size: 14px; font-weight: 800; }
            #pill { color: #d7ff45; background: #29301e; border: 1px solid #424d2a;
                    border-radius: 8px; padding: 3px 8px; font-size: 10px; font-weight: 700; }
            #gpuCard, #systemCard { background: #1d201e; border: 1px solid #303431; border-radius: 11px; }
            #systemMetric { color: #f7f7f2; font-size: 14px; font-weight: 800; }
            #divider { color: #303431; background: #303431; max-width: 1px; border: none; }
            #todoScroll { background: transparent; border: none; }
            #todoScroll > QWidget > QWidget { background: transparent; }
            #todoRow { background: #1d201e; border: 1px solid #2d312e; border-radius: 8px; }
            #todoText { color: #f4f4ef; font-size: 12px; }
            #todoDone { color: #737a74; font-size: 12px; text-decoration: line-through; }
            #todoRemove { min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; font-size: 15px; }
            #todoAdd { background: #d7ff45; color: #141613; min-width: 30px; max-width: 30px;
                       min-height: 30px; max-height: 30px; font-size: 18px; font-weight: 800; }
            #todoAdd:hover { background: #e3ff7a; color: #141613; }
            QLineEdit { background: #1d201e; color: #f4f4f0; border: 1px solid #343835;
                        border-radius: 8px; padding: 7px 10px; font-size: 11px; }
            QLineEdit:focus { border-color: #788d35; }
            QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #59605a;
                                   border-radius: 4px; background: #151715; }
            QCheckBox::indicator:checked { background: #d7ff45; border-color: #d7ff45; }
            #error { color: #ff8b78; background: #34221f; border-radius: 7px; padding: 6px; font-size: 10px; }
            QPushButton { background: transparent; color: #838a84; border: none; border-radius: 8px;
                          min-width: 28px; min-height: 28px; font-size: 18px; }
            QPushButton:hover { background: #2a2e2b; color: #f4f4f0; }
            QProgressBar { border: none; background: #2b2f2c; border-radius: 3px; }
            QProgressBar::chunk { background: #d7ff45; border-radius: 3px; }
            """
        )

    def add_todo(self) -> None:
        text = self.todo_input.text().strip()
        if not text:
            return
        self._append_todo(text, False)
        self.todo_input.clear()
        self._save_todos()

    def _append_todo(
        self,
        text: str,
        done: bool,
        source: str | None = None,
        source_id: str | None = None,
    ) -> None:
        row = TodoRow(text, done, source, source_id)
        row.changed.connect(self._save_todos)
        row.remove_requested.connect(self._remove_todo)
        self.todo_rows.append(row)
        self.todo_layout.insertWidget(self.todo_layout.count() - 1, row)
        self._update_todo_summary()

    def _sync_codex_todos(self, items: list[dict[str, str]]) -> None:
        # Recurring automations are work performed by Codex, not user-owned todos.
        stale_automations = [row for row in self.todo_rows if row.source == "automation"]
        for row in stale_automations:
            self.todo_rows.remove(row)
            self.todo_layout.removeWidget(row)
            row.deleteLater()
        known = {
            (row.source, row.source_id)
            for row in self.todo_rows
            if row.source and row.source_id
        }
        changed = bool(stale_automations)
        for item in items:
            identity = (item.get("source"), item.get("source_id"))
            if identity in known:
                continue
            self._append_todo(
                item["text"], False, item.get("source"), item.get("source_id")
            )
            known.add(identity)
            changed = True
        if changed:
            self._save_todos()

    def _remove_todo(self, row: TodoRow) -> None:
        if row not in self.todo_rows:
            return
        self.todo_rows.remove(row)
        self.todo_layout.removeWidget(row)
        row.deleteLater()
        self._save_todos()

    def _save_todos(self) -> None:
        save_todos([row.data() for row in self.todo_rows])
        self._todo_mtime_ns = self._todo_file_mtime()
        self._update_todo_summary()

    @staticmethod
    def _todo_file_mtime() -> int:
        try:
            return TODO_FILE.stat().st_mtime_ns
        except OSError:
            return 0

    def reload_external_todos(self) -> None:
        modified = self._todo_file_mtime()
        if modified == self._todo_mtime_ns:
            return
        todos = load_todos()
        for row in self.todo_rows:
            self.todo_layout.removeWidget(row)
            row.deleteLater()
        self.todo_rows.clear()
        for todo in todos:
            self._append_todo(
                todo["text"], todo["done"], todo.get("source"), todo.get("source_id")
            )
        self._todo_mtime_ns = modified
        self._update_todo_summary()

    def _update_todo_summary(self) -> None:
        total = len(self.todo_rows)
        remaining = sum(not row.checkbox.isChecked() for row in self.todo_rows)
        self.todo_summary.setText(f"{remaining} 待完成 · {total} 项" if total else "0 项")
        # Show up to five rows in full; longer lists stay compact and scrollable.
        self.todo_scroll.setFixedHeight(42 if total == 0 else min(198, 8 + total * 38))

    def refresh_all(self) -> None:
        self.refresh_codex()
        self.refresh_gpu()
        self.refresh_system()

    def refresh_codex(self) -> None:
        if self._codex_busy:
            return
        self._codex_busy = True
        self.status.setText("正在同步 Codex…")
        worker = Worker(read_codex_usage)
        worker.signals.done.connect(self._on_codex)
        worker.signals.failed.connect(self._on_codex_error)
        self.pool.start(worker)

    def _on_codex(self, data: dict[str, Any]) -> None:
        self._codex_busy = False
        limits = data.get("rateLimitsByLimitId") or {}
        snapshot = limits.get("codex") or data.get("rateLimits") or {}
        self.primary.update_value(snapshot.get("primary"))
        self.secondary.update_value(snapshot.get("secondary"))
        plan = str(snapshot.get("planType") or "未知").replace("_", " ").upper()
        self.plan.setText(plan)
        agents = data.get("agentStats") or {}
        automations = data.get("automationStats") or {}
        total = agents.get("total")
        automation_total = automations.get("total", 0)
        automation_active = automations.get("active", 0)
        if total is None:
            self.tasks.setText("本机任务 —")
        else:
            self.tasks.setText(f"任务 {total} · 自动化 {automation_total}")
        self.tasks.setToolTip(
            f"本机非归档任务/窗口 {total} 个；自动化 {automation_total} 个（启用 {automation_active} 个）。"
            "任务窗口不等于同时运行的 Agent。"
        )
        self._sync_codex_todos(data.get("codexTodos") or [])
        self.codex_error.hide()
        self.status.setText("已同步 · " + datetime.now().strftime("%H:%M:%S"))

    def _on_codex_error(self, message: str) -> None:
        self._codex_busy = False
        self.status.setText("Codex 同步失败")
        self.codex_error.setText(message)
        self.codex_error.show()

    def refresh_gpu(self) -> None:
        if self._gpu_busy:
            return
        self._gpu_busy = True
        worker = Worker(read_gpus)
        worker.signals.done.connect(self._on_gpus)
        worker.signals.failed.connect(self._on_gpu_error)
        self.pool.start(worker)

    def refresh_system(self) -> None:
        if self._system_busy:
            return
        self._system_busy = True
        worker = Worker(read_system)
        worker.signals.done.connect(self._on_system)
        worker.signals.failed.connect(self._on_system_error)
        self.pool.start(worker)

    def _on_system(self, data: dict[str, Any]) -> None:
        self._system_busy = False
        self.system_card.update_system(data)
        self.system_summary.setText("每 2 秒刷新")

    def _on_system_error(self, message: str) -> None:
        self._system_busy = False
        self.system_summary.setText("读取失败 · " + message)

    def _on_gpus(self, gpus: list[dict[str, Any]]) -> None:
        self._gpu_busy = False
        seen = set()
        for gpu in gpus:
            index = gpu["index"]
            seen.add(index)
            if index not in self._gpu_cards:
                card = GpuCard(gpu)
                self._gpu_cards[index] = card
                position = len(self._gpu_cards) - 1
                self.gpu_box.addWidget(card, position // 2, position % 2)
            else:
                self._gpu_cards[index].update_gpu(gpu)
        for index in set(self._gpu_cards) - seen:
            card = self._gpu_cards.pop(index)
            card.deleteLater()
        active = sum(1 for gpu in gpus if gpu["util"] > 0)
        self.gpu_summary.setText(f"{len(gpus)} 张 · {active} 张活跃")
        self.gpu_error.hide()
        self.adjustSize()

    def _on_gpu_error(self, message: str) -> None:
        self._gpu_busy = False
        self.gpu_summary.setText("不可用")
        self.gpu_error.setText("nvidia-smi: " + message)
        self.gpu_error.show()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        event.accept()

    def paintEvent(self, event) -> None:
        # Ensures clean antialiased translucent corners on compositors.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(QPainterPath())
        super().paintEvent(event)


def main() -> int:
    instance_lock = acquire_instance_lock()
    if instance_lock is None:
        return 0
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setApplicationName("Codex Meter")
    app.setQuitOnLastWindowClosed(True)
    font = QFont()
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)
    window = MeterWindow()
    screen = app.primaryScreen().availableGeometry()
    window.adjustSize()
    window.move(screen.right() - window.width() - 24, screen.top() + 24)
    window.show()
    result = app.exec()
    instance_lock.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
