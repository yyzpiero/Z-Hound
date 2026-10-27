from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import queue
import threading
from typing import Callable

from ..fetcher import compact_message
from ..persistence.csv_repository import iso_now
from ..pipeline import CollectOptions


TERMINAL_EVENTS = frozenset({"run_completed", "run_failed", "run_cancelled"})
PROGRESS_EVENTS = frozenset(
    {
        "run_started",
        "source_started",
        "source_completed",
        "run_completed",
        "run_failed",
        "run_cancelled",
        "failure_recorded",
    }
)


class JobConflict(RuntimeError):
    pass


class NoActiveJob(RuntimeError):
    pass


class CollectionJobManager:
    def __init__(
        self,
        runner: Callable,
        max_events: int = 500,
        clock: Callable[[], datetime] | None = None,
    ):
        self._runner = runner
        self._max_events = max_events
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._event_lock = threading.Lock()
        self._current: dict | None = None
        self._events: deque[dict] = deque(maxlen=max_events)
        self._subscribers: list[queue.Queue] = []

    def start(self, options: CollectOptions) -> dict:
        with self._lock:
            if self._current is not None and not self._current["done"].is_set():
                raise JobConflict("a collection job is already running")
            handle = {
                "run_id": None,
                "state": "running",
                "done": threading.Event(),
                "cancel": threading.Event(),
                "options": options,
                "started_at": iso_now(self._clock()),
                "updated_at": iso_now(self._clock()),
                "completed": 0,
                "failed": 0,
                "reports": 0,
                "accepted": 0,
                "review": 0,
                "sources": {},
            }
            self._current = handle
            thread = threading.Thread(
                target=self._run,
                args=(handle, options),
                daemon=True,
                name="zhound-collection",
            )
            handle["thread"] = thread
            thread.start()
            return handle

    def current(self) -> dict | None:
        with self._lock:
            handle = self._current
            if handle is None:
                return None
            return {
                "run_id": handle.get("run_id"),
                "state": handle.get("state") or "running",
                "started_at": handle.get("started_at"),
                "updated_at": handle.get("updated_at"),
                "completed": int(handle.get("completed") or 0),
                "failed": int(handle.get("failed") or 0),
                "reports": int(handle.get("reports") or 0),
                "accepted": int(handle.get("accepted") or 0),
                "review": int(handle.get("review") or 0),
                "sources": [dict(row) for row in (handle.get("sources") or {}).values()],
            }

    def is_cancelled(self) -> bool:
        with self._lock:
            handle = self._current
            if handle is None:
                return False
            cancel = handle.get("cancel")
            return bool(cancel is not None and cancel.is_set())

    def request_stop(self) -> dict:
        with self._lock:
            handle = self._current
            if handle is None or handle["done"].is_set():
                raise NoActiveJob("no active collection job")
            handle["cancel"].set()
            handle["state"] = "stopping"
            handle["updated_at"] = iso_now(self._clock())
            return {
                "run_id": handle.get("run_id"),
                "state": handle.get("state"),
                "started_at": handle.get("started_at"),
                "updated_at": handle.get("updated_at"),
                "completed": int(handle.get("completed") or 0),
                "failed": int(handle.get("failed") or 0),
                "reports": int(handle.get("reports") or 0),
                "accepted": int(handle.get("accepted") or 0),
                "review": int(handle.get("review") or 0),
                "sources": [dict(row) for row in (handle.get("sources") or {}).values()],
            }

    def recent_events(self) -> list[dict]:
        with self._event_lock:
            return list(self._events)

    def subscribe(self, maxsize: int = 256) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._event_lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._event_lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def _stamp(self, event: dict) -> dict:
        payload = dict(event)
        payload.setdefault("ts", iso_now(self._clock()))
        return payload

    def _dispatch(self, payload: dict, handle: dict) -> None:
        with self._lock:
            if payload.get("run_id") and not handle.get("run_id"):
                handle["run_id"] = payload["run_id"]
            if payload.get("type") == "run_started":
                handle["state"] = "running"
            elif payload.get("type") == "run_cancelled":
                handle["state"] = "stopping"
            handle["updated_at"] = payload.get("ts") or iso_now(self._clock())
            event_type = payload.get("type")
            source_id = payload.get("source_id")
            if event_type == "source_started" and source_id is not None:
                handle["sources"][source_id] = {
                    "source_id": source_id,
                    "source_name": payload.get("source_name") or "",
                    "status": "running",
                    "reports": 0,
                }
            elif event_type == "source_completed" and source_id is not None:
                status = str(payload.get("status") or "failed")
                reports = int(payload.get("reports") or 0)
                handle["sources"][source_id] = {
                    "source_id": source_id,
                    "source_name": payload.get("source_name") or "",
                    "status": status,
                    "reports": reports,
                }
                handle["completed"] = int(handle.get("completed") or 0) + 1
                handle["reports"] = int(handle.get("reports") or 0) + reports
                if status in {"failed", "blocked"}:
                    handle["failed"] = int(handle.get("failed") or 0) + 1
            elif event_type == "indicator_accepted":
                handle["accepted"] = int(handle.get("accepted") or 0) + 1
            elif event_type == "indicator_reviewed":
                handle["review"] = int(handle.get("review") or 0) + 1
        with self._event_lock:
            if payload.get("type") in PROGRESS_EVENTS:
                self._events.append(payload)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(payload)
            except queue.Full:
                continue

    def _run(self, handle: dict, options: CollectOptions) -> None:
        terminal_sent = False

        def emit(event: dict) -> None:
            nonlocal terminal_sent
            payload = self._stamp(event)
            if payload.get("type") in TERMINAL_EVENTS:
                if terminal_sent:
                    return
                terminal_sent = True
            self._dispatch(payload, handle)

        try:
            summary = self._runner(options, emit)
            if not terminal_sent:
                emit(
                    {
                        "type": "run_completed",
                        "run_id": getattr(summary, "run_id", handle.get("run_id")),
                    }
                )
        except Exception as exc:
            message = compact_message(exc)
            code = (
                "writer_lock"
                if "writing this data directory" in message
                else "failed"
            )
            emit(
                {
                    "type": "run_failed",
                    "run_id": handle.get("run_id"),
                    "message": message,
                    "code": code,
                }
            )
        finally:
            handle["state"] = "complete"
            handle["done"].set()
            with self._lock:
                if self._current is handle:
                    self._current = None


def iter_job_events(manager: CollectionJobManager, run_id: str):
    subscriber = manager.subscribe()
    try:
        for event in manager.recent_events():
            if run_id not in {"current", event.get("run_id")}:
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if run_id != "current" and event.get("type") in TERMINAL_EVENTS:
                return
        current = manager.current()
        watching_live = run_id == "current" or (
            current is not None and current.get("run_id") in {run_id, None}
        )
        if not watching_live:
            yield ": heartbeat\n\n"
            yield (
                "data: "
                + json.dumps(
                    {"type": "run_completed", "run_id": run_id, "replay": True},
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            return
        yield ": heartbeat\n\n"
        while True:
            try:
                event = subscriber.get(timeout=15)
            except queue.Empty:
                yield ": heartbeat\n\n"
                continue
            if run_id not in {"current", event.get("run_id")}:
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if run_id != "current" and event.get("type") in TERMINAL_EVENTS:
                return
    finally:
        manager.unsubscribe(subscriber)
