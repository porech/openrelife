"""Preference patches must not mutate unrelated settings or apply invalid data."""
import json
from pathlib import Path
import tomllib
from unittest.mock import Mock

import pytest


@pytest.fixture
def preferences(tmp_path, monkeypatch):
    from openrelife import app as module
    monkeypatch.setattr(module, 'appdata_folder', str(tmp_path))
    monkeypatch.setattr(module.agent_service, 'control_token', None)
    monkeypatch.setattr(module.retention_manager, 'request_cleanup', Mock())
    old_interval = module.get_screenshot_interval()
    old_quality = module.get_screenshot_quality()
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'retention_days': -1, 'server_port': 8082, 'future_setting': 'preserve'}))
    yield module, module.app.test_client(), path
    module.set_screenshot_interval(old_interval)
    module.set_screenshot_quality(old_quality)


def test_snapshot_and_patch_preserve_other_preferences(preferences):
    module, client, path = preferences
    before = client.get('/api/settings').get_json()
    assert before['retention_days'] == -1
    assert before['storage_path'] == str(path.parent)
    manifest = Path(__file__).resolve().parent.parent / 'pyproject.toml'
    assert before['version'] == tomllib.loads(manifest.read_text())['project']['version']
    assert 'api_key' not in before
    assert client.post('/api/settings', json={'interval': 15}).status_code == 200
    after = client.get('/api/settings').get_json()
    assert after['interval'] == 15
    assert after['retention_days'] == -1
    assert json.loads(path.read_text())['future_setting'] == 'preserve'
    module.retention_manager.request_cleanup.assert_not_called()


@pytest.mark.parametrize('patch', [
    {'port': 'bad'}, {'port': 65536}, {'interval': 0}, {'interval': 1.5},
    {'ocr_cooldown': 1}, {'quality': 'bogus'}, {'ocr_compute_mode': 'bogus'},
    {'skip_incognito': 'false'}, {'retention_days': 0}, {'unknown': True},
])
def test_invalid_patch_has_no_partial_write_or_cleanup(preferences, patch):
    module, client, path = preferences
    original = path.read_bytes()
    interval = module.get_screenshot_interval()
    payload = {'interval': 12, 'retention_days': 90, **patch}
    assert client.post('/api/settings', json=payload).status_code == 400
    assert path.read_bytes() == original
    assert module.get_screenshot_interval() == interval
    module.retention_manager.request_cleanup.assert_not_called()


def test_failed_persistence_does_not_change_live_capture(preferences, monkeypatch):
    module, client, path = preferences
    original = path.read_bytes()
    interval = module.get_screenshot_interval()
    monkeypatch.setattr(module, '_save_settings_atomically', Mock(side_effect=OSError('fixture disk full')))
    response = client.post('/api/settings', json={'interval': 14, 'retention_days': 90})
    assert response.status_code == 500
    assert module.get_screenshot_interval() == interval
    assert path.read_bytes() == original
    module.retention_manager.request_cleanup.assert_not_called()


def test_corrupt_preferences_are_not_replaced_with_defaults(preferences):
    _, client, path = preferences
    path.write_text('{broken')
    assert client.get('/api/settings').status_code == 500
    assert client.post('/api/settings', json={'quality': 'low'}).status_code == 500
    assert path.read_text() == '{broken'


def test_ai_ocr_key_is_masked_and_invalid_edits_preserve_saved_key(preferences):
    _, client, path = preferences
    endpoint = '/api/config'
    assert client.post(endpoint, json={'provider': 'gemini', 'api_key': 'fixture-key'}).status_code == 200
    assert client.get(endpoint).get_json() == {'provider': 'gemini', 'api_key': '***'}
    saved = (path.parent / 'ai_config.json').read_bytes()
    for edit in [{'provider': 'claude', 'api_key': ''}, {'provider': 'unsupported', 'api_key': 'other'},
                 {'provider': 'gemini', 'api_key': '***'}]:
        assert client.post(endpoint, json=edit).status_code == 400
        assert (path.parent / 'ai_config.json').read_bytes() == saved
    assert (path.parent / 'ai_config.json').stat().st_mode & 0o777 == 0o600
