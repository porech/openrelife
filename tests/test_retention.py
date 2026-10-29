"""Exercise destructive retention against a disposable real SQLite/FTS store."""
import json
import sqlite3
from unittest.mock import Mock

import pytest

from openrelife import database, retention

NOW = 1_793_260_800
DAY = 86400


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "db_path", str(tmp_path / "recall.db"))
    database.create_db()
    (tmp_path / "screenshots").mkdir()
    (tmp_path / "settings.json").write_text(json.dumps({"retention_days": 90}))
    return tmp_path, retention.RetentionManager(tmp_path)


def add(store, timestamp, text="expiredneedle", image=True, row=True):
    root, _ = store
    if row:
        with database._connect() as conn:
            conn.execute("INSERT INTO entries(timestamp, text) VALUES (?, ?)", (timestamp, text))
    if image:
        (root / "screenshots" / f"{timestamp}.webp").write_bytes(b"image")


def timestamps():
    with database._connect() as conn:
        return {row[0] for row in conn.execute("SELECT timestamp FROM entries")}


def test_expiration_boundary_legacy_orphans_and_fts(store, monkeypatch):
    root, manager = store
    cutoff = (NOW - 90 * DAY) * 1_000_000
    expired = cutoff - 1
    recent = cutoff + 1
    legacy = NOW - 91 * DAY
    orphan = cutoff - 2
    db_only = cutoff - 3
    for stamp in (expired, cutoff, recent, legacy):
        add(store, stamp)
    add(store, orphan, row=False)
    add(store, db_only, image=False)
    (root / "screenshots" / "notes.webp").write_bytes(b"unrecognized")
    outside = root / "outside.webp"
    outside.write_bytes(b"keep")
    (root / "screenshots" / f"{cutoff - 4}.webp").symlink_to(outside)
    monkeypatch.setattr(retention, "BATCH_SIZE", 2)
    notify = Mock(wraps=database._notify_deleted)
    monkeypatch.setattr(database, "_notify_deleted", notify)

    result = manager.run_once(now=NOW)
    assert result["state"] == "completed"
    assert result["deleted_entries"] == 3
    assert result["deleted_files"] == 3
    assert timestamps() == {cutoff, recent}
    assert (root / "screenshots" / f"{cutoff}.webp").exists()
    assert (root / "screenshots" / f"{recent}.webp").exists()
    assert not (root / "screenshots" / f"{orphan}.webp").exists()
    assert (root / "screenshots" / "notes.webp").exists()
    assert outside.read_bytes() == b"keep"
    assert (root / "screenshots" / f"{cutoff - 4}.webp").is_symlink()
    assert notify.call_count == 2
    with database._connect() as conn:
        assert conn.execute("SELECT count(*) FROM entries_fts WHERE entries_fts MATCH 'expiredneedle'").fetchone()[0] == 2
        conn.execute("INSERT INTO entries_fts(entries_fts, rank) VALUES ('integrity-check', 1)")
    again = manager.run_once(now=NOW)
    assert again["deleted_entries"] == again["deleted_files"] == 0
    assert manager.status()["state"] == "completed"


@pytest.mark.parametrize("policy", [-1, 0, -90, 1, 90.5, True, None, "invalid"])
def test_disabled_or_invalid_policy_never_deletes(store, policy):
    root, manager = store
    add(store, (NOW - 100 * DAY) * 1_000_000)
    (root / "settings.json").write_text(json.dumps({"retention_days": policy}))
    result = manager.run_once(now=NOW)
    assert result["state"] == ("disabled" if policy == -1 else "failed")
    assert len(timestamps()) == 1
    assert len(list((root / "screenshots").iterdir())) == 1


def test_corrupt_or_missing_settings_never_delete(store):
    root, manager = store
    add(store, (NOW - 100 * DAY) * 1_000_000)
    (root / "settings.json").write_text("{")
    assert manager.run_once(now=NOW)["state"] == "failed"
    (root / "settings.json").unlink()
    assert manager.run_once(now=NOW)["state"] == "disabled"
    assert len(timestamps()) == 1


def test_missing_search_index_is_rebuilt_from_actual_content(store):
    stamp = NOW * 1_000_000
    add(store, stamp, text='recoverableword')
    add(store, stamp + 1, text='')
    add(store, stamp + 2, text=None)
    with database._connect() as conn:
        conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('delete-all')")
        # External-content COUNT and rowid queries misleadingly see base rows.
        assert conn.execute('SELECT COUNT(*) FROM entries_fts').fetchone()[0] == 3
        assert conn.execute('SELECT COUNT(*) FROM entries_fts_docsize').fetchone()[0] == 0
    assert database.fts_backfill_if_needed()
    with database._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entries_fts WHERE entries_fts MATCH 'recoverableword'").fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM entries_fts_docsize').fetchone()[0] == 2
        # Empty processed entries must also be indexed so their later deletion
        # is consistent with the existing non-NULL DELETE trigger.
        conn.execute('DELETE FROM entries WHERE timestamp=?', (stamp + 1,))
        conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('integrity-check')")


def test_partially_missing_index_does_not_pass_external_rowid_probe(store):
    for offset in range(20):
        add(store, NOW * 1_000_000 + offset, text=f'unique{offset}')
    with database._connect() as conn:
        rid, text = conn.execute('SELECT id,text FROM entries ORDER BY id LIMIT 1').fetchone()
        conn.execute("INSERT INTO entries_fts(entries_fts,rowid,text) VALUES('delete',?,?)", (rid,text))
    assert database.fts_backfill_if_needed()
    with database._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entries_fts WHERE entries_fts MATCH 'unique0'").fetchone()[0] == 1
        conn.execute("INSERT INTO entries_fts(entries_fts,rank) VALUES('integrity-check',1)")


def test_db_failure_rolls_back_and_preserves_images(store):
    root, manager = store
    expired = (NOW - 100 * DAY) * 1_000_000
    add(store, expired)
    with database._connect() as conn:
        conn.execute("CREATE TRIGGER prevent_delete BEFORE DELETE ON entries BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    result = manager.run_once(now=NOW)
    assert result["state"] == "failed"
    assert timestamps() == {expired}
    assert (root / "screenshots" / f"{expired}.webp").exists()


def test_failed_unlink_is_recovered_as_an_orphan_on_next_run(store, monkeypatch):
    from pathlib import Path
    root, manager = store
    expired = (NOW - 100 * DAY) * 1_000_000
    add(store, expired)
    unlink = Path.unlink

    def denied(path, *args, **kwargs):
        if path.suffix == '.webp':
            raise PermissionError("injected denial")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", denied)
        assert manager.run_once(now=NOW)["state"] == "completed_with_errors"
    assert timestamps() == set()
    assert manager.run_once(now=NOW)["deleted_files"] == 1
    assert not (root / "screenshots" / f"{expired}.webp").exists()


def test_policy_change_during_cleanup_stops_before_next_batch(store, monkeypatch):
    root, manager = store
    for i in range(3):
        add(store, (NOW - 100 * DAY) * 1_000_000 + i)
    original = database.delete_expired_entries

    def change_policy(cutoff, batch_size):
        result = original(cutoff, 1)
        (root / "settings.json").write_text('{"retention_days": -1}')
        return result

    monkeypatch.setattr(database, "delete_expired_entries", change_policy)
    assert manager.run_once(now=NOW)["state"] == "policy_changed"
    assert len(timestamps()) == 2
    assert len(list((root / "screenshots").iterdir())) == 2
    assert manager._wake.is_set()


def test_scheduler_runs_immediately_then_daily_and_retries_errors(store, monkeypatch):
    _, manager = store
    class StopLoop(BaseException):
        pass
    wake = Mock()
    wake.wait.side_effect = StopLoop()
    manager._wake = wake
    run = Mock(return_value={"state": "completed"})
    monkeypatch.setattr(manager, "run_once", run)
    with pytest.raises(StopLoop):
        manager._loop()
    run.assert_called_once_with()
    wake.wait.assert_called_with(86400)
    run.return_value = {"state": "failed"}
    with pytest.raises(StopLoop):
        manager._loop()
    wake.wait.assert_called_with(300)


@pytest.mark.parametrize("endpoint,payload", [
    ('/api/settings/retention', {'days': '90'}),
    ('/api/settings', {'retention_days': '90'}),
])
def test_settings_save_validates_then_requests_cleanup(store, monkeypatch, endpoint, payload):
    from openrelife import app as module
    root, manager = store
    monkeypatch.setattr(module, "appdata_folder", str(root))
    monkeypatch.setattr(module, "retention_manager", manager)

    def requested():
        assert json.loads((root / 'settings.json').read_text())['retention_days'] == 90
    notify = Mock(side_effect=requested)
    monkeypatch.setattr(manager, 'request_cleanup', notify)
    with module.app.test_client() as client:
        assert client.post(endpoint, json=payload).status_code == 200
        notify.assert_called_once()
        bad = {key: 0 for key in payload}
        assert client.post(endpoint, json=bad).status_code == 400
        assert json.loads((root / 'settings.json').read_text())['retention_days'] == 90
        assert notify.call_count == 1
