from __future__ import annotations

import threading
from enum import Enum


class TaskState(str, Enum):
    IDLE = "idle"
    READY = "ready"
    AUTHENTICATING = "authenticating"
    FETCHING = "fetching"
    EXPORTING = "exporting"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


_ALLOWED = {
    TaskState.IDLE: {TaskState.AUTHENTICATING, TaskState.FETCHING, TaskState.READY},
    TaskState.READY: {TaskState.AUTHENTICATING, TaskState.FETCHING, TaskState.IDLE},
    TaskState.AUTHENTICATING: {TaskState.READY, TaskState.ERROR, TaskState.CANCELLED},
    TaskState.FETCHING: {TaskState.EXPORTING, TaskState.ERROR, TaskState.CANCELLED},
    TaskState.EXPORTING: {TaskState.DONE, TaskState.ERROR, TaskState.CANCELLED},
    TaskState.DONE: {TaskState.READY, TaskState.IDLE},
    TaskState.ERROR: {TaskState.READY, TaskState.IDLE},
    TaskState.CANCELLED: {TaskState.READY, TaskState.IDLE, TaskState.AUTHENTICATING, TaskState.FETCHING},
}


class TaskManager:
    """
    Tiny state machine + generation guard.

    State answers "what is the accepted task doing?"
    Generation answers "does this late event belong to the accepted task?"
    """

    def __init__(self):
        self.state = TaskState.IDLE
        self.generation = 0
        self.cancel_event: threading.Event | None = None

    def transition(self, new_state: TaskState):
        if new_state == self.state:
            return
        if new_state not in _ALLOWED.get(self.state, set()):
            raise RuntimeError(f"非法任务状态转换：{self.state.value} -> {new_state.value}")
        self.state = new_state

    def start(self, state: TaskState) -> tuple[int, threading.Event]:
        if state not in (TaskState.AUTHENTICATING, TaskState.FETCHING):
            raise ValueError("只能启动登录或抓取任务")
        if self.state not in (TaskState.IDLE, TaskState.READY, TaskState.CANCELLED):
            raise RuntimeError("已有任务正在运行")
        self.generation += 1
        self.cancel_event = threading.Event()
        self.transition(state)
        return self.generation, self.cancel_event

    def accepts(self, generation: int) -> bool:
        return generation == self.generation

    def cancel(self):
        if self.cancel_event:
            self.cancel_event.set()
        self.generation += 1  # Late events from the old worker become stale immediately.
        self.cancel_event = None
        if self.state in (TaskState.AUTHENTICATING, TaskState.FETCHING, TaskState.EXPORTING):
            self.transition(TaskState.CANCELLED)

    def terminal(self, generation: int, state: TaskState) -> bool:
        if not self.accepts(generation):
            return False
        if state not in (TaskState.DONE, TaskState.ERROR, TaskState.CANCELLED, TaskState.READY):
            raise ValueError("terminal state expected")
        self.transition(state)
        self.cancel_event = None
        return True
