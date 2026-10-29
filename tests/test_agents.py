"""Public HTTP contract tests for archive tools and app-owned deletion."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from flask import Flask
from PIL import Image
import pytest

from openrelife import database
from openrelife.agent import register_agent_routes


@pytest.fixture
def agent_api(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'db_path', str(tmp_path / 'recall.db'))
    database.create_db()
    (tmp_path / 'screenshots').mkdir()
    app = Flask('agent-contract')
    service = register_agent_routes(app, tmp_path, 'fixture-control')
    monkeypatch.setattr(database, '_delete_observers', [service.invalidate])
    client = app.test_client()
    control = {'X-OpenReLife-Control': 'fixture-control'}
    agent = {'Authorization': 'Bearer ' + service.read_token, 'X-OpenReLife-Client': 'fixture-client'}

    def insert(stamp, app='Chrome', title='Meeting', text='Leave call'):
        with database._connect() as conn:
            conn.execute('INSERT INTO entries(timestamp,app,title,text) VALUES(?,?,?,?)', (stamp, app, title, text))
        Image.new('RGB', (100, 70), 'white').save(tmp_path / 'screenshots' / f'{stamp}.webp')

    return client, service, control, agent, insert, tmp_path


def tool(api, name, **arguments):
    client, _, _, headers, _, _ = api
    response = client.post('/api/agent/tools', headers=headers, json={'tool': name, 'arguments': arguments})
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    if name == 'propose_deletion':
        return client.get('/api/agent/selections/' + result['selection_id'], headers=api[2]).get_json()
    return result


def test_scoped_agent_cannot_read_app_configuration_or_mutate(agent_api):
    client, service, control, agent, _, _ = agent_api
    assert client.get('/api/agent/settings', headers=agent).status_code == 403
    assert client.post('/api/delete', headers=agent, json={'timestamps': [1]}).status_code == 403
    assert client.post('/api/agent/settings', headers=agent, json={}).status_code == 403
    assert client.get('/api/agent/settings', headers=control).status_code == 200
    response = client.post('/api/agent/tools', headers=agent, json={'tool': 'delete_reviewed', 'arguments': {}})
    assert response.status_code == 400
    assert client.post('/api/agent/tools', json={'tool': 'archive_status'}).status_code == 403


def test_filters_pagination_ocr_and_temporal_neighbors(agent_api):
    _, _, _, _, insert, _ = agent_api
    base = 1_793_000_000_000_000
    insert(base, title='Telegram documentation', text='How to install Telegram')
    insert(base + 10_000_000, app='Telegram', title='Alice', text='Alice: hello')
    insert(base + 20_000_000, title='Telegram Web', text='Alice: hello')
    insert(base + 30_000_000, title='Meet', text=None)
    status = tool(agent_api, 'archive_status')
    assert status['frames'] == 4 and status['pending_ocr'] == 1
    first = tool(agent_api, 'find_frames', terms_any=['Telegram', 'Alice'], limit=1)
    assert first['total'] == 3 and first['next_offset'] == 1
    second = tool(agent_api, 'find_frames', apps=['Telegram'])
    assert second['total'] == 1 and second['frames'][0]['app'] == 'Telegram'
    title = tool(agent_api, 'find_frames', title_contains='documentation')
    assert title['total'] == 1
    pending = tool(agent_api, 'find_frames', ocr_state='pending')
    assert pending['total'] == 1 and pending['frames'][0]['ocr_state'] == 'pending'
    read = tool(agent_api, 'read_frames', frame_ids=[str(base + 10_000_000)], text_limit=3)
    assert read['frames'][0]['ocr'] == 'Ali' and read['frames'][0]['next_text_offset'] == 3
    neighbors = tool(agent_api, 'neighboring_frames', frame_id=str(base + 10_000_000), before=1, after=1)
    assert [frame['timestamp'] for frame in neighbors['frames']] == [base, base + 10_000_000, base + 20_000_000]
    assert neighbors['gaps_seconds'] == [10, 10]
    image = tool(agent_api, 'view_frame', frame_id=str(base), max_dimension=50)
    assert image['width'] == 50 and image['mime_type'] == 'image/jpeg'
    intervals = tool(agent_api, 'find_intervals', apps=['Chrome'], gap_seconds=15)
    assert len(intervals['intervals']) == 2


def test_selection_is_disjoint_frozen_adjustable_and_app_only(agent_api):
    client, service, control, agent, insert, root = agent_api
    base = 1_793_000_000_000_000
    for offset in [0, 10, 20, 30]:
        insert(base + offset * 1_000_000)
    selection = tool(agent_api, 'propose_deletion', intervals=[
        {'since': str(base), 'until': str(base + 5_000_000)},
        {'since': str(base + 20_000_000), 'until': str(base + 25_000_000)}], reason='Fixture chat frames')
    assert set(selection['frame_ids']) == {str(base), str(base + 20_000_000)}
    insert(base + 1_000_000)  # A later capture inside the original range is not added.
    payload = {'digest': selection['digest'], 'frame_ids': selection['frame_ids']}
    endpoint = f"/api/agent/selections/{selection['selection_id']}/delete"
    assert client.post(endpoint, headers=agent, json=payload).status_code == 403
    assert client.post(endpoint, headers=control, json=payload | {'frame_ids': [str(base + 10_000_000)]}).status_code == 400
    result = client.post(endpoint, headers=control, json=payload | {'frame_ids': [str(base)]})
    assert result.status_code == 200 and result.get_json()['deleted'] == [str(base)]
    with database._connect() as conn:
        stamps = {row[0] for row in conn.execute('SELECT timestamp FROM entries')}
    assert stamps == {base + 1_000_000, base + 10_000_000, base + 20_000_000, base + 30_000_000}
    assert not (root / 'screenshots' / f'{base}.webp').exists()
    assert (root / 'screenshots' / f'{base + 20_000_000}.webp').exists()


def test_replaced_image_is_preserved_and_error_is_visible(agent_api):
    client, _, control, _, insert, root = agent_api
    stamp = 1_793_000_000_000_000; insert(stamp)
    selection = tool(agent_api, 'propose_deletion', frame_ids=[str(stamp)])
    path = root / 'screenshots' / f'{stamp}.webp'
    path.write_bytes(b'replacement image')
    result = client.post(f"/api/agent/selections/{selection['selection_id']}/delete", headers=control,
        json={'digest': selection['digest'], 'frame_ids': [str(stamp)]}).get_json()
    assert not result['complete'] and result['changed'] == [str(stamp)]
    assert path.read_bytes() == b'replacement image'


def test_appending_selection_requires_preview_to_be_refreshed(agent_api):
    client, _, control, _, insert, _ = agent_api
    first = 1_793_000_000_000_000; second = first + 10_000_000
    insert(first); insert(second)
    initial = tool(agent_api, 'propose_deletion', frame_ids=[str(first)])
    expanded = tool(agent_api, 'propose_deletion', frame_ids=[str(second)], selection_id=initial['selection_id'])
    assert set(expanded['frame_ids']) == {str(first), str(second)}
    assert initial['digest'] != expanded['digest']
    response = client.post(f"/api/agent/selections/{initial['selection_id']}/delete", headers=control,
        json={'digest': initial['digest'], 'frame_ids': initial['frame_ids']})
    assert response.status_code == 400


def test_database_failure_keeps_image_and_reports_incomplete(agent_api):
    client, _, control, _, insert, root = agent_api
    stamp = 1_793_000_000_000_000; insert(stamp)
    selection = tool(agent_api, 'propose_deletion', frame_ids=[str(stamp)])
    with database._connect() as conn:
        conn.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON entries BEGIN SELECT RAISE(ABORT,'fixture error'); END")
    result = client.post(f"/api/agent/selections/{selection['selection_id']}/delete", headers=control,
        json={'digest': selection['digest'], 'frame_ids': [str(stamp)]}).get_json()
    assert not result['complete'] and result['errors']
    assert (root / 'screenshots' / f'{stamp}.webp').exists()


def test_trace_records_public_tool_filters_outputs_and_failures(agent_api):
    client, service, control, agent, insert, root = agent_api
    stamp = 1_793_000_000_000_000; insert(stamp)
    result = tool(agent_api, 'find_frames', apps=['Chrome'], limit=5)
    client.post('/api/agent/tools', headers=agent, json={'tool': 'read_frames', 'arguments': {'frame_ids': []}})
    run = next(iter(service.runs.values()))
    response = client.get(f"/api/agent/runs/{run['run_id']}/trace", headers=control)
    assert response.status_code == 200
    events = [json.loads(line) for line in response.data.splitlines()]
    assert any(event['type'] == 'tool_call' and event['arguments'].get('apps') == ['Chrome'] for event in events)
    assert any(event['type'] == 'tool_result' and event['frame_ids'] == [str(stamp)] for event in events)
    output = next(event for event in events if event['type'] == 'tool_result')
    encoded = json.dumps(result, sort_keys=True).encode()
    assert output['bytes'] == len(encoded)
    assert output['output_sha256'] == hashlib.sha256(encoded).hexdigest()
    assert any(event['type'] == 'tool_error' for event in events)
    assert service.read_token.encode() not in response.data


def test_settings_validate_custom_command_and_timeout(agent_api):
    client, _, control, _, _, _ = agent_api
    assert client.post('/api/agent/settings', headers=control, json={'preset': 'custom', 'command': '', 'timeout_seconds': 20}).status_code == 400
    assert client.post('/api/agent/settings', headers=control, json={'preset': 'custom', 'command': '/bin/cat', 'timeout_seconds': 0}).status_code == 400
    result = client.post('/api/agent/settings', headers=control, json={'preset': 'custom', 'command': '/bin/cat', 'timeout_seconds': 20})
    assert result.status_code == 200 and result.get_json()['command'] == '/bin/cat'


def test_trace_redacts_credentials_and_does_not_copy_images(agent_api):
    _, service, _, _, _, root = agent_api
    run = service._new_run('Synthetic trace redaction')
    service.trace(run, {'type': 'fixture', 'argv': ['command', '--api-key', 'fixture-credential'],
                        'authorization': 'Bearer fixture-sensitive-value',
                        'payload': {'type': 'image', 'data': 'fixture-image-payload'},
                        'text': service.control_token})
    text = (root / 'agent/traces' / (run['run_id'] + '.jsonl')).read_text()
    assert 'fixture-credential' not in text and 'fixture-sensitive-value' not in text
    assert 'fixture-image-payload' not in text and service.control_token not in text
    assert 'encoded_image_sha256' in text


def test_gui_launch_path_discovers_user_cli(agent_api, monkeypatch):
    _, service, _, _, _, root = agent_api
    command = root / '.local/bin/codex'; command.parent.mkdir(parents=True)
    command.write_text('#!/bin/sh\nexit 0\n'); command.chmod(0o755)
    monkeypatch.setattr(Path, 'home', lambda: root)
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    assert service.settings()['available']['codex']
