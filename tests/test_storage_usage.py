"""Lazy accounting must stay off the request thread and inside the archive."""
import os
from pathlib import Path
import threading
import time
from unittest.mock import Mock

import pytest

from openrelife.storage_usage import ScreenshotStorageUsage


def ready(service):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = service.snapshot()
        if not result['scanning'] and result['state'] != 'pending':
            return result
        time.sleep(.01)
    raise AssertionError('Background accounting did not complete')


def test_accounting_counts_images_and_skips_links_and_other_files(tmp_path):
    screenshots = tmp_path / 'screenshots'; screenshots.mkdir()
    first = screenshots / 'frame.webp'; first.write_bytes(b'x' * 1234)
    nested = screenshots / '2026'; nested.mkdir()
    second = nested / 'frame.png'; second.write_bytes(b'y' * 2345)
    (screenshots / 'notes.txt').write_bytes(b'n' * 4000)
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'private.webp').write_bytes(b'z' * 4000)
    (screenshots / 'linked').symlink_to(outside, target_is_directory=True)
    (screenshots / 'linked.webp').symlink_to(outside / 'private.webp')
    result = ready(ScreenshotStorageUsage(screenshots))
    assert result['state'] == 'ready'
    assert result['screenshot_count'] == 2
    assert result['logical_bytes'] == 1234 + 2345
    assert result['screenshots_bytes'] == sum(p.stat().st_blocks * 512 for p in (first, second))
    assert result['disk_total_bytes'] > 0
    assert 0 <= result['disk_free_bytes'] <= result['disk_total_bytes']


def test_hardlinks_do_not_double_count_allocated_blocks(tmp_path):
    first = tmp_path / 'first.webp'; first.write_bytes(b'x' * 4096)
    os.link(first, tmp_path / 'second.webp')
    result = ready(ScreenshotStorageUsage(tmp_path))
    assert result['screenshot_count'] == 2
    assert result['screenshots_bytes'] == first.stat().st_blocks * 512


def test_requests_do_not_wait_for_or_duplicate_an_existing_scan(tmp_path, monkeypatch):
    service = ScreenshotStorageUsage(tmp_path)
    entered = threading.Event(); release = threading.Event()
    original = service._scan

    def blocked(generation):
        entered.set(); release.wait(5); original(generation)

    spy = Mock(side_effect=blocked)
    monkeypatch.setattr(service, '_scan', spy)
    try:
        start = time.monotonic()
        assert service.snapshot()['scanning']
        assert time.monotonic() - start < .5
        assert entered.wait(2)
        for _ in range(10):
            assert service.snapshot()['scanning']
        assert spy.call_count == 1
    finally:
        release.set()
    assert ready(service)['state'] == 'ready'


def test_cache_expires_after_invalidation_not_on_every_request(tmp_path, monkeypatch):
    service = ScreenshotStorageUsage(tmp_path)
    first = tmp_path / 'first.webp'; first.write_bytes(b'x')
    assert ready(service)['screenshot_count'] == 1
    spy = Mock(wraps=service._scan); monkeypatch.setattr(service, '_scan', spy)
    (tmp_path / 'second.webp').write_bytes(b'y')
    for _ in range(10):
        assert service.snapshot()['screenshot_count'] == 1
    spy.assert_not_called()
    service.invalidate([1])
    assert ready(service)['screenshot_count'] == 2
    assert spy.call_count == 1


def test_unreadable_folder_is_reported_as_incomplete(tmp_path, monkeypatch):
    import openrelife.storage_usage as module
    original = module.os.scandir

    def scandir(path):
        if Path(path).name == 'unreadable':
            raise PermissionError('synthetic permission failure')
        return original(path)

    (tmp_path / 'first.webp').write_bytes(b'x')
    (tmp_path / 'unreadable').mkdir()
    monkeypatch.setattr(module.os, 'scandir', scandir)
    result = ready(ScreenshotStorageUsage(tmp_path))
    assert result['state'] == 'partial'
    assert result['screenshot_count'] == 1
    assert result['unreadable_entries'] == 1


def test_storage_endpoint_uses_its_configured_folder(tmp_path, monkeypatch):
    from openrelife import app as module
    (tmp_path / 'fixture.webp').write_bytes(b'fixture')
    service = ScreenshotStorageUsage(tmp_path)
    ready(service)
    monkeypatch.setattr(module, 'storage_usage', service)
    monkeypatch.setattr(module.agent_service, 'control_token', None)
    response = module.app.test_client().get('/api/storage/usage')
    assert response.status_code == 200
    assert response.json['screenshot_count'] == 1
    assert response.json['logical_bytes'] == 7
