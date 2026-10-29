"""Apply the saved retention policy at startup, on changes, and every day."""
import json
import logging
import os
from pathlib import Path
import stat
import threading
import time

from openrelife import database

ALLOWED_DAYS = (-1, 7, 30, 90, 365)
BATCH_SIZE = 250
DAY_SECONDS = 86400
_logger = logging.getLogger("openrelife.capture")


def validate_days(value):
    if isinstance(value, bool) or str(value) not in {str(day) for day in ALLOWED_DAYS}:
        raise ValueError("Retention must be -1, 7, 30, 90, or 365 days")
    return int(value)


def timestamp_us(value):
    """Recognize current microsecond names and the original second-based names."""
    try:
        value = int(value)
    except (ValueError, TypeError):
        return None
    if 1_000_000_000 <= value < 100_000_000_000:
        return value * 1_000_000
    if 100_000_000_000_000 <= value < 100_000_000_000_000_000:
        return value
    return None


class RetentionManager:
    def __init__(self, storage):
        self.storage = Path(storage)
        self._wake = threading.Event()
        self._run_lock = threading.Lock()
        self._thread = None

    def _policy(self):
        try:
            settings = json.loads((self.storage / "settings.json").read_text())
        except FileNotFoundError:
            return -1
        return validate_days(settings.get("retention_days", -1))

    def status(self):
        try:
            return json.loads((self.storage / "retention-status.json").read_text())
        except (OSError, ValueError):
            return {"state": "not_run"}

    def _save_status(self, result):
        target = self.storage / "retention-status.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(result) + "\n")
        os.replace(temporary, target)

    def request_cleanup(self):
        self._wake.set()

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="retention", daemon=True)
            self._thread.start()

    def _loop(self):
        while True:
            self._wake.clear()
            result = self.run_once()
            # A setting change during a run must also trigger the next run.
            self._wake.wait(300 if result["state"] in ("failed", "completed_with_errors") else DAY_SECONDS)

    def run_once(self, now=None):
        if not self._run_lock.acquire(blocking=False):
            return {"state": "already_running"}
        result = {"state": "running", "started_at": time.time(),
                  "deleted_entries": 0, "deleted_files": 0, "deleted_file_bytes": 0,
                  "file_errors": 0}
        try:
            days = self._policy()
            result["days"] = days
            if days == -1:
                result["state"] = "disabled"
                return result
            cutoff = int(((time.time() if now is None else now) - days * DAY_SECONDS) * 1_000_000)
            result["cutoff_us"] = cutoff
            self._save_status(result)
            _logger.info("Retention cleanup started: keeping %s days, cutoff_us=%s", days, cutoff)

            def check_policy():
                if self._policy() != days:
                    self.request_cleanup()
                    raise InterruptedError("Retention policy changed; restarting with the saved policy")

            def remove_image(path):
                try:
                    info = path.lstat()
                    # Never follow links or touch directories/unrecognized files.
                    if not stat.S_ISREG(info.st_mode):
                        return
                    path.unlink()
                    result["deleted_files"] += 1
                    result["deleted_file_bytes"] += info.st_size
                except FileNotFoundError:
                    pass
                except OSError:
                    result["file_errors"] += 1
                    if result["file_errors"] <= 3:
                        _logger.exception("Retention could not remove %s", path)

            screenshots = self.storage / "screenshots"
            while True:
                check_policy()
                # Commit rows first. If interrupted before unlinking, the orphan
                # sweep below (or the next run) still finds the expired files.
                timestamps = database.delete_expired_entries(cutoff, BATCH_SIZE)
                if not timestamps:
                    break
                result["deleted_entries"] += len(timestamps)
                for timestamp in timestamps:
                    remove_image(screenshots / f"{timestamp}.webp")
                if result["deleted_entries"] % 5000 == 0:
                    self._save_status(result)
                    _logger.info("Retention progress: %s entries, %s files removed",
                                 result["deleted_entries"], result["deleted_files"])

            # Include files with no DB row, e.g. from an interrupted prior run.
            if screenshots.exists():
                with os.scandir(screenshots) as entries:
                    for index, entry in enumerate(entries):
                        if index % BATCH_SIZE == 0:
                            check_policy()
                        if not entry.name.endswith(".webp") or not entry.name[:-5].isascii() or not entry.name[:-5].isdigit():
                            continue
                        timestamp = timestamp_us(entry.name[:-5])
                        if timestamp is not None and timestamp < cutoff:
                            remove_image(Path(entry.path))
            result["state"] = "completed" if not result["file_errors"] else "completed_with_errors"
        except InterruptedError as error:
            result.update(state="policy_changed", error=str(error))
        except Exception as error:
            result.update(state="failed", error=str(error))
            _logger.exception("Retention cleanup failed; remaining files will be retried")
        finally:
            result["finished_at"] = time.time()
            try:
                self._save_status(result)
            except OSError:
                _logger.exception("Could not save retention status")
            self._run_lock.release()
            _logger.info("Retention cleanup result: %s", result)
        return result
