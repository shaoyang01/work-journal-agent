from __future__ import annotations

import threading
import time
import uuid
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator

from .config import AppConfig
from .sqlite_store import is_sqlite_storage, now_iso, store_for


CURRENT_AI_RUN_ID: ContextVar[str | None] = ContextVar("CURRENT_AI_RUN_ID", default=None)
TRACKER_LOCK = threading.RLock()


@dataclass(frozen=True)
class TaskRunConflict:
    active_run: dict[str, Any]

    @property
    def message(self) -> str:
        run_id = str(self.active_run.get("id") or "")
        run_kind = str(self.active_run.get("run_kind") or "task")
        return f"已有任务正在执行：{run_kind} {run_id}".strip()


class TaskRunAlreadyActive(RuntimeError):
    def __init__(self, active_run: dict[str, Any]):
        self.active_run = active_run
        super().__init__(TaskRunConflict(active_run).message)


@contextmanager
def track_ai_task_run(config: AppConfig, *, day: date, run_kind: str, metadata: dict[str, Any] | None = None, fail_if_active: bool = False) -> Iterator[str | None]:
    if not is_sqlite_storage(config.storage):
        yield None
        return

    run_id = str(uuid.uuid4())
    with TRACKER_LOCK:
        store = store_for(config.storage)
        with store.connect() as conn:
            cleanup_stale_task_runs(conn, store)
            active = store.active_ai_task_run(conn)
            if fail_if_active and active:
                raise TaskRunAlreadyActive(active)
            run_metadata = {**(metadata or {}), "pid": os.getpid()}
            store.create_ai_task_run(
                conn,
                {
                    "id": run_id,
                    "day": day.isoformat(),
                    "run_kind": run_kind,
                    "model": config.ai.model,
                    "status": "running",
                    "started_at": now_iso(),
                    "metadata": run_metadata,
                },
            )
    token = CURRENT_AI_RUN_ID.set(run_id)
    try:
        yield run_id
    except Exception as exc:
        finish_ai_task_run(config, run_id, status="failed", error_message=str(exc))
        raise
    else:
        finish_ai_task_run(config, run_id, status="succeeded")
    finally:
        CURRENT_AI_RUN_ID.reset(token)


def current_ai_run_id() -> str | None:
    return CURRENT_AI_RUN_ID.get()


def start_ai_task_item(
    config: AppConfig,
    *,
    phase: str,
    stage: str,
    run_id: str | None = None,
    batch_index: int | None = None,
    input_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[str | None, float]:
    run_id = run_id or current_ai_run_id()
    started = time.monotonic()
    if not run_id or not is_sqlite_storage(config.storage):
        return None, started

    item_id = str(uuid.uuid4())
    with TRACKER_LOCK:
        store = store_for(config.storage)
        with store.connect() as conn:
            store.create_ai_task_run_item(
                conn,
                {
                    "id": item_id,
                    "run_id": run_id,
                    "phase": phase,
                    "stage": stage,
                    "batch_index": batch_index,
                    "input_hash": input_hash,
                    "status": "running",
                    "started_at": now_iso(),
                    "metadata": metadata or {},
                },
            )
    return item_id, started


def finish_ai_task_item(
    config: AppConfig,
    item_id: str | None,
    started: float,
    *,
    status: str,
    error_message: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    if not item_id or not is_sqlite_storage(config.storage):
        return
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    with TRACKER_LOCK:
        store = store_for(config.storage)
        with store.connect() as conn:
            store.finish_ai_task_run_item(conn, item_id, status=status, error_message=error_message, duration_ms=duration_ms, result=result)


def finish_ai_task_run(config: AppConfig, run_id: str, *, status: str, error_message: str | None = None) -> None:
    if not is_sqlite_storage(config.storage):
        return
    with TRACKER_LOCK:
        store = store_for(config.storage)
        with store.connect() as conn:
            store.finish_ai_task_run(conn, run_id, status=status, error_message=error_message)


def active_ai_task_run(config: AppConfig, *, day: date | None = None) -> dict[str, Any]:
    if not is_sqlite_storage(config.storage):
        return {}
    store = store_for(config.storage)
    with store.connect() as conn:
        return store.active_ai_task_run(conn, day=day)


def latest_ai_task_run(config: AppConfig, *, day: date | None = None) -> dict[str, Any]:
    if not is_sqlite_storage(config.storage):
        return {}
    store = store_for(config.storage)
    with store.connect() as conn:
        return store.latest_ai_task_run(conn, day=day)


def ensure_no_active_task_run(config: AppConfig) -> None:
    if not is_sqlite_storage(config.storage):
        return
    with TRACKER_LOCK:
        store = store_for(config.storage)
        with store.connect() as conn:
            cleanup_stale_task_runs(conn, store)
            active = store.active_ai_task_run(conn)
            if active:
                raise TaskRunAlreadyActive(active)


def cleanup_stale_task_runs(conn: Any, store: Any) -> None:
    for run in store.running_ai_task_runs(conn):
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        pid = metadata.get("pid")
        if isinstance(pid, int) and process_is_running(pid):
            continue
        store.finish_ai_task_run(conn, str(run.get("id") or ""), status="failed", error_message="stale running task")


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
