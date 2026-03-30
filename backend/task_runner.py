"""Background task runner — executes scans/workflows without blocking the API."""

import logging
import threading
import traceback
from datetime import datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


class TaskRunner:
    """Runs functions in background threads, tracks status."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    def run_task(self, name: str, func, *args, **kwargs) -> str:
        """Start a function in a background thread. Returns task_id."""
        # Prevent duplicate runs
        with self._lock:
            for tid, t in self._tasks.items():
                if t["name"] == name and t["status"] == "running":
                    logger.info("Task '%s' already running (id=%s)", name, tid)
                    return tid

        task_id = uuid4().hex[:8]
        task = {
            "id": task_id,
            "name": name,
            "status": "running",
            "result": None,
            "error": None,
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
        }

        with self._lock:
            self._tasks[task_id] = task

        def _run():
            try:
                result = func(*args, **kwargs)
                with self._lock:
                    self._tasks[task_id]["status"] = "completed"
                    self._tasks[task_id]["result"] = result
                    self._tasks[task_id]["finished_at"] = datetime.now().isoformat()
                logger.info("Task '%s' completed (id=%s)", name, task_id)
            except Exception as e:
                with self._lock:
                    self._tasks[task_id]["status"] = "failed"
                    self._tasks[task_id]["error"] = str(e)
                    self._tasks[task_id]["finished_at"] = datetime.now().isoformat()
                logger.error("Task '%s' failed: %s\n%s", name, e, traceback.format_exc())

        thread = threading.Thread(target=_run, daemon=True, name=f"task-{name}-{task_id}")
        thread.start()
        logger.info("Task '%s' started (id=%s)", name, task_id)
        return task_id

    def get_status(self, task_id: str) -> dict | None:
        with self._lock:
            return self._tasks.get(task_id)

    def get_all(self) -> list[dict]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t["started_at"], reverse=True)[:20]


# Global singleton
runner = TaskRunner()
