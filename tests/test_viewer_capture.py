"""Exercise capture races with synthetic pixels and the public viewer-state API."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest


class StopCapture(BaseException):
    pass


def test_viewer_api_rejects_out_of_order_updates(monkeypatch):
    from openrelife import app as module, screenshot as sc
    monkeypatch.setattr(sc, 'viewer_open', False)
    monkeypatch.setattr(sc, '_viewer_revision', -1)
    monkeypatch.setattr(sc, '_viewer_generation', 0)
    client = module.app.test_client()
    assert client.post('/api/viewer-open', json={'open': True, 'revision': 3}).get_json()['viewer_open']
    assert client.post('/api/viewer-open', json={'open': False, 'revision': 2}).get_json()['viewer_open']
    assert client.post('/api/viewer-open', json={'open': False, 'revision': 3}).get_json()['viewer_open']
    assert not client.post('/api/viewer-open', json={'open': False, 'revision': 4}).get_json()['viewer_open']
    assert client.post('/api/viewer-open', json={'open': False, 'revision': True}).status_code == 400


@pytest.mark.parametrize('transition', ['open', 'open-close', 'self-focus', 'processing', 'already-open', 'resume'])
def test_viewer_transitions_never_persist_app_pixels(tmp_path, monkeypatch, transition):
    from openrelife import app as module, screenshot as sc

    monkeypatch.setattr(sc, 'viewer_open', transition in ('already-open', 'resume'))
    monkeypatch.setattr(sc, '_viewer_generation', 0)
    monkeypatch.setattr(sc, 'screenshots_path', str(tmp_path))
    monkeypatch.setattr(sc, 'has_screen_permission', lambda: True)
    monkeypatch.setattr(sc, '_ocr_guard', SimpleNamespace(capture_available=lambda: True))
    monkeypatch.setattr(sc, 'is_recording_paused', False)
    monkeypatch.setattr(sc, 'skip_incognito_recording', False)
    monkeypatch.setattr(sc, 'is_user_active', lambda: True)
    monkeypatch.setattr(sc, 'screenshot_quality', 'high')
    state = {'app': 'Notes', 'title': 'Synthetic document'}
    monkeypatch.setattr(sc, 'get_active_app_name', lambda: state['app'])
    monkeypatch.setattr(sc, 'get_active_window_title', lambda: state['title'])
    saved = Mock()
    monkeypatch.setattr(sc, 'insert_entry_stub', saved)
    monkeypatch.setattr(sc, '_ocr_queue', Mock())
    client = module.app.test_client()

    def viewer(open_):
        response = client.post('/api/viewer-open', json={'open': open_})
        assert response.status_code == 200
        assert client.get('/api/recording-status').get_json()['viewer_open'] is open_

    def capture():
        if transition in ('open', 'open-close'):
            viewer(True)
            if transition == 'open-close':
                viewer(False)
        if transition == 'self-focus':
            state['app'], state['title'] = 'OpenReLife', 'OpenReLife'
        return [np.zeros((32, 32, 3), dtype=np.uint8)]

    take = Mock(side_effect=capture)
    monkeypatch.setattr(sc, 'take_screenshots', take)

    def similar(*_):
        if transition == 'processing':
            viewer(True)
        return False

    monkeypatch.setattr(sc, 'is_similar', similar)
    permission_checks = 0

    def permission():
        nonlocal permission_checks
        permission_checks += 1
        if permission_checks > (2 if transition == 'resume' else 1):
            raise StopCapture()
        return True

    monkeypatch.setattr(sc, 'has_screen_permission', permission)

    def sleep(seconds):
        if transition == 'resume' and sc.get_viewer_open():
            viewer(False)
            return
        raise StopCapture()

    monkeypatch.setattr(sc.time, 'sleep', sleep)
    with pytest.raises(StopCapture):
        sc.record_screenshots_thread()
    if transition == 'resume':
        assert len(list(tmp_path.glob('*.webp'))) == 1
        saved.assert_called_once()
    else:
        assert not list(tmp_path.glob('*.webp'))
        saved.assert_not_called()
        sc._ocr_queue.put_nowait.assert_not_called()
    if transition == 'already-open':
        take.assert_not_called()
