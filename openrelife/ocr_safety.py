"""Bound OCR retries and preserve disk headroom across application restarts."""
import json
import os
from pathlib import Path
import shutil
import threading
import time

OCR_MIN_FREE_BYTES = 10 * 2**30
CAPTURE_MIN_FREE_BYTES = 2 * 2**30
MAX_CONSECUTIVE_FAILURES = 2


class OcrSafetyError(RuntimeError):
    """Text recognition cannot safely start; leave pending frames untouched."""


class OcrSafetyGuard:
    def __init__(self, storage):
        self.storage = Path(storage)
        self.state_path = self.storage / "ocr-safety.json"
        self._lock = threading.RLock()

    def _read(self):
        try:
            state = json.loads(self.state_path.read_text())
            if (not isinstance(state, dict) or not isinstance(state.get("paused"), bool)
                    or type(state.get("consecutive_failures")) is not int
                    or state["consecutive_failures"] < 0):
                raise ValueError("Invalid OCR safety state")
            return state
        except FileNotFoundError:
            return {"paused": False, "consecutive_failures": 0}
        except (OSError, ValueError):
            return {"paused": True, "consecutive_failures": 0,
                    "reason": "Text recognition is paused because its safety state cannot be read. Resume it in settings."}

    def _write(self, state):
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state) + "\n")
        os.replace(temporary, self.state_path)

    def status(self):
        with self._lock:
            state = self._read()
        state["disk_free_bytes"] = shutil.disk_usage(self.storage).free
        state["minimum_free_bytes"] = OCR_MIN_FREE_BYTES
        return state

    def ensure_available(self):
        state = self.status()
        if state.get("paused"):
            raise OcrSafetyError(state.get("reason", "Text recognition is paused. Resume it in settings."))
        if state["disk_free_bytes"] < OCR_MIN_FREE_BYTES:
            raise OcrSafetyError("Text recognition is paused until at least 10 GiB of disk space is available.")

    def record_failure(self, reason):
        with self._lock:
            state = self._read()
            count = int(state.get("consecutive_failures", 0)) + 1
            state.update(consecutive_failures=count, updated_at=time.time(), last_failure=reason)
            if count >= MAX_CONSECUTIVE_FAILURES:
                state.update(paused=True, reason="Text recognition stopped after repeated worker failures. Resume it in settings.")
            self._write(state)
            return state

    def record_success(self):
        with self._lock:
            state = self._read()
            if state.get("consecutive_failures") and not state.get("paused"):
                self._write({"paused": False, "consecutive_failures": 0, "updated_at": time.time()})

    def reset(self):
        with self._lock:
            self._write({"paused": False, "consecutive_failures": 0, "updated_at": time.time()})

    def capture_available(self):
        return shutil.disk_usage(self.storage).free >= CAPTURE_MIN_FREE_BYTES
