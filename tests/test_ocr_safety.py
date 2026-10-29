"""Verify that low disk and repeated worker failures cannot cause endless retries."""
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openrelife import ocr_safety


@pytest.fixture
def guard(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_safety.shutil, "disk_usage", lambda path: SimpleNamespace(free=20 * 2**30))
    return ocr_safety.OcrSafetyGuard(tmp_path)


def test_low_space_blocks_ocr_without_clearing_pending_data(guard, monkeypatch):
    monkeypatch.setattr(ocr_safety.shutil, "disk_usage", lambda path: SimpleNamespace(free=9 * 2**30))
    with pytest.raises(ocr_safety.OcrSafetyError, match="10 GiB"):
        guard.ensure_available()
    assert not guard.state_path.exists()
    assert guard.capture_available()
    monkeypatch.setattr(ocr_safety.shutil, "disk_usage", lambda path: SimpleNamespace(free=2**30))
    assert not guard.capture_available()


def test_repeated_failures_survive_restart_and_require_explicit_reset(guard):
    guard.record_failure("timeout")
    guard.ensure_available()
    guard.record_failure("timeout")
    restarted = ocr_safety.OcrSafetyGuard(guard.storage)
    with pytest.raises(ocr_safety.OcrSafetyError, match="repeated worker failures"):
        restarted.ensure_available()
    restarted.reset()
    restarted.ensure_available()


def test_success_breaks_consecutive_failure_sequence(guard):
    guard.record_failure("timeout")
    guard.record_success()
    guard.record_failure("timeout")
    assert not guard.status()["paused"]


def test_corrupt_safety_state_does_not_resume_processing(guard):
    guard.state_path.write_text('{"consecutive_failures": "bad"}')
    with pytest.raises(ocr_safety.OcrSafetyError, match="cannot be read"):
        guard.ensure_available()
    guard.reset()
    guard.ensure_available()


def test_timed_out_workers_are_killed_and_not_restarted_forever(guard, monkeypatch):
    import multiprocessing
    from openrelife import screenshot as sc
    monkeypatch.setattr(sc, "_ocr_guard", guard)
    monkeypatch.setattr(sc, "_ocr_run_lock", threading.Lock())
    processes = []

    class HungProcess:
        def __init__(self, **kwargs):
            self.alive = True
            self.exitcode = None
            self.closed = False
            processes.append(self)

        def start(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return self.alive

        def kill(self):
            self.alive = False
            self.exitcode = -9

        def close(self):
            self.closed = True

    monkeypatch.setattr(multiprocessing, "get_context", lambda method: SimpleNamespace(Process=HungProcess))
    assert sc._run_ocr_subprocess([1], 2, True, 120) == (True, -9)
    assert sc._run_ocr_subprocess([1], 2, True, 120) == (True, -9)
    with pytest.raises(ocr_safety.OcrSafetyError):
        sc._run_ocr_subprocess([1], 2, True, 120)
    assert len(processes) == 2
    assert all(process.closed and not process.alive for process in processes)


def test_background_batch_does_not_overlap_an_existing_ocr(guard, monkeypatch):
    from openrelife import screenshot as sc
    monkeypatch.setattr(sc, "_ocr_guard", guard)
    lock = threading.Lock()
    lock.acquire()
    monkeypatch.setattr(sc, "_ocr_run_lock", lock)
    with pytest.raises(ocr_safety.OcrSafetyError, match="already running"):
        sc._run_ocr_subprocess([1], 2, True, 120)
    lock.release()


def test_frame_does_not_enter_ocr_when_disk_is_low(guard, monkeypatch, tmp_path):
    from openrelife import screenshot as sc
    (tmp_path / "123.webp").touch()
    recognize = Mock()
    monkeypatch.setattr(sc, "screenshots_path", str(tmp_path))
    monkeypatch.setattr(sc, "_ocr_guard", guard)
    monkeypatch.setattr(sc, "_ocr_run_lock", threading.Lock())
    monkeypatch.setattr(sc, "_ocr_one_frame", recognize)
    monkeypatch.setattr(ocr_safety.shutil, "disk_usage", lambda path: SimpleNamespace(free=9 * 2**30))
    with pytest.raises(ocr_safety.OcrSafetyError):
        sc.ocr_one_frame(123, use_apple_vision=True)
    recognize.assert_not_called()
    assert not sc._ocr_run_lock.locked()


def test_capture_loop_does_not_create_images_with_critical_disk_space(guard, monkeypatch):
    from openrelife import screenshot as sc

    class StopLoop(BaseException):
        pass

    capture = Mock()
    monkeypatch.setattr(sc, "_ocr_guard", guard)
    monkeypatch.setattr(ocr_safety.shutil, "disk_usage", lambda path: SimpleNamespace(free=2**30))
    monkeypatch.setattr(sc, "has_screen_permission", lambda: True)
    monkeypatch.setattr(sc, "is_recording_paused", False)
    monkeypatch.setattr(sc, "viewer_open", False)
    monkeypatch.setattr(sc, "skip_incognito_recording", False)
    monkeypatch.setattr(sc, "get_active_window_title", lambda: "Test window")
    monkeypatch.setattr(sc, "get_active_app_name", lambda: "Test app")
    monkeypatch.setattr(sc, "is_user_active", lambda: True)
    monkeypatch.setattr(sc, "take_screenshots", capture)

    def sleep(seconds):
        assert seconds == 30
        raise StopLoop()

    monkeypatch.setattr(sc.time, "sleep", sleep)
    with pytest.raises(StopLoop):
        sc.record_screenshots_thread()
    capture.assert_not_called()


def test_settings_load_or_unrelated_save_cannot_clear_safety_pause(guard, monkeypatch, tmp_path):
    from openrelife import app as module, screenshot as sc
    guard.record_failure("timeout")
    guard.record_failure("timeout")
    monkeypatch.setattr(sc, "_ocr_guard", guard)
    monkeypatch.setattr(module, "appdata_folder", str(tmp_path))
    (tmp_path / "settings.json").write_text(json.dumps({"ocr_compute_mode": "smart", "use_apple_vision": False}))
    module.load_settings()
    assert guard.status()["paused"]
    with module.app.test_client() as client:
        response = client.post('/api/settings', json={'ocr_compute_mode': 'smart'})
        assert response.status_code == 200
        assert guard.status()["paused"]
        response = client.get('/api/settings/ocr-compute-mode')
        assert response.get_json()['ocr_safety']['paused']
        response = client.post('/api/settings/ocr-compute-mode', json={'ocr_compute_mode': 'smart'})
        assert response.status_code == 200
        assert not guard.status()["paused"]


def test_on_demand_safety_pause_returns_503(monkeypatch):
    from openrelife import app as module
    monkeypatch.setattr(module, "get_entry_by_timestamp", lambda ts: None)
    def blocked(*args, **kwargs):
        raise ocr_safety.OcrSafetyError("Paused for safety")
    monkeypatch.setattr(module, "ocr_one_frame", blocked)
    with module.app.test_client() as client:
        response = client.post('/api/ocr-now/123')
        assert response.status_code == 503
        assert response.get_json()['error'] == 'Paused for safety'


def test_deferring_batch_preserves_pending_tasks_without_inflating_queue(monkeypatch):
    import queue
    from openrelife import screenshot as sc

    class StopLoop(Exception):
        pass

    pending = queue.Queue()
    for timestamp in (1, 2, 3):
        pending.put(timestamp)
    monkeypatch.setattr(sc, "_ocr_queue", pending)
    monkeypatch.setattr(sc, "ocr_cooldown", 0)
    monkeypatch.setattr(sc, "get_pending_ocr_timestamps", lambda: [])
    monkeypatch.setattr(sc, "_get_batch_params", lambda count: (1, 1, 1))

    def deferred(*args):
        raise ocr_safety.OcrSafetyError("Disk space is low")

    def sleep(seconds):
        if seconds == 60:
            raise StopLoop()

    monkeypatch.setattr(sc, "_run_ocr_subprocess", deferred)
    monkeypatch.setattr(sc.time, "sleep", sleep)
    with pytest.raises(StopLoop):
        sc.ocr_worker_thread()
    assert pending.qsize() == 3
    assert pending.unfinished_tasks == 3
    assert sorted(pending.get_nowait() for _ in range(3)) == [1, 2, 3]
