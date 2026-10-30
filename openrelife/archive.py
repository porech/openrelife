"""Bounded archive operations shared by MCP, the agent runner and app previews."""
from datetime import datetime, timezone
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time

from PIL import Image

from openrelife import database


def instant(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Use epoch microseconds or an ISO timestamp with timezone")
    if isinstance(value, int) or str(value).isdigit():
        return int(value)
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError("ISO timestamps require a timezone")
    return int(parsed.timestamp() * 1_000_000)


def iso(timestamp):
    return datetime.fromtimestamp(timestamp / 1_000_000, timezone.utc).isoformat()


def bounded(value, default, maximum):
    value = default if value is None else int(value)
    if value < 1 or value > maximum:
        raise ValueError(f"Value must be between 1 and {maximum}")
    return value


class Archive:
    def __init__(self, storage):
        self.storage = Path(storage).expanduser().resolve()
        self.images = self.storage / 'screenshots'
        self.selections = self.storage / 'agent' / 'selections'
        self._delete_lock = threading.Lock()

    def _where(self, since=None, until=None, apps=None, title_contains=None,
               text_contains=None, terms_any=None, ocr_state=None, frame_ids=None):
        clauses, args = [], []
        start, end = instant(since), instant(until)
        if start is not None:
            clauses.append('timestamp >= ?'); args.append(start)
        if end is not None:
            clauses.append('timestamp <= ?'); args.append(end)
        if start is not None and end is not None and start > end:
            raise ValueError('since must be earlier than until')
        if apps:
            if not isinstance(apps, list) or len(apps) > 30:
                raise ValueError('apps must contain at most 30 exact application names')
            clauses.append('app IN (' + ','.join('?' for _ in apps) + ')'); args.extend(apps)
        for field, value in [('title', title_contains), ("COALESCE(text,'') || ' ' || COALESCE(ai_text,'')", text_contains)]:
            if value:
                if len(str(value)) > 500:
                    raise ValueError('Filter text is too long')
                clauses.append(f'instr(lower({field}),lower(?)) > 0'); args.append(value)
        if terms_any:
            if not isinstance(terms_any, list) or len(terms_any) > 20 or any(not isinstance(v, str) or not v or len(v) > 200 for v in terms_any):
                raise ValueError('terms_any requires at most 20 nonempty strings')
            clauses.append('(' + ' OR '.join("instr(lower(COALESCE(text,'') || ' ' || COALESCE(ai_text,'') || ' ' || COALESCE(title,'')),lower(?)) > 0" for _ in terms_any) + ')')
            args.extend(terms_any)
        if ocr_state:
            conditions = {'pending': 'text IS NULL', 'empty': "text = ''", 'ready': "text IS NOT NULL AND text != ''"}
            if ocr_state not in conditions:
                raise ValueError('ocr_state must be pending, empty, or ready')
            clauses.append(conditions[ocr_state])
        if frame_ids is not None:
            if len(frame_ids) > 200:
                raise ValueError('At most 200 frame identifiers per read')
            clauses.append('timestamp IN (' + ','.join('?' for _ in frame_ids) + ')')
            args.extend(int(value) for value in frame_ids)
        return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), args

    def _metadata(self, row):
        return {'frame_id': str(row['timestamp']), 'timestamp': row['timestamp'],
                'captured_at': iso(row['timestamp']), 'app': row['app'], 'title': row['title'],
                'ocr_state': 'pending' if row['text'] is None else ('ready' if row['text'] else 'empty'),
                'snippet': (row['ai_text'] or row['text'] or '')[:400],
                'image_available': (self.images / f"{row['timestamp']}.webp").is_file(),
                'citation': f"openrelife://frame/{row['timestamp']}"}

    def status(self):
        with database._connect() as conn:
            total, first, last, pending = conn.execute('SELECT COUNT(*),MIN(timestamp),MAX(timestamp),SUM(text IS NULL) FROM entries').fetchone()
            apps = [{'app': name, 'frames': count} for name, count in conn.execute('SELECT app,COUNT(*) FROM entries GROUP BY app ORDER BY COUNT(*) DESC')]
        return {'frames': total, 'first': iso(first) if first else None, 'last': iso(last) if last else None,
                'pending_ocr': pending or 0, 'apps': apps, 'now': datetime.now(timezone.utc).isoformat(),
                'local_timezone': str(datetime.now().astimezone().tzinfo),
                'note': 'Capture gaps and pending OCR are not proof that an event did not happen.'}

    def find_frames(self, limit=50, offset=0, **filters):
        limit = bounded(limit, 50, 200); offset = int(offset)
        if offset < 0 or offset > 1_000_000:
            raise ValueError('Invalid pagination offset')
        where, args = self._where(**filters)
        with database._connect() as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute('SELECT COUNT(*) FROM entries' + where, args).fetchone()[0]
            rows = conn.execute('SELECT timestamp,app,title,substr(text,1,400) AS text,substr(ai_text,1,400) AS ai_text FROM entries' + where + ' ORDER BY timestamp DESC LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
        return {'frames': [self._metadata(row) for row in rows], 'total': total, 'offset': offset,
                'next_offset': offset + len(rows) if offset + len(rows) < total else None,
                'filters': filters}

    def read_frames(self, frame_ids, text_offset=0, text_limit=12000):
        if not frame_ids or len(frame_ids) > 10:
            raise ValueError('Read between 1 and 10 frames at a time')
        text_limit = bounded(text_limit, 12000, 20000); text_offset = int(text_offset)
        if text_offset < 0:
            raise ValueError('text_offset cannot be negative')
        where, args = self._where(frame_ids=frame_ids)
        with database._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT timestamp,app,title,text,ai_text FROM entries' + where + ' ORDER BY timestamp', args).fetchall()
        results = []
        for row in rows:
            item = self._metadata(row)
            text = row['ai_text'] or row['text'] or ''
            item.update(ocr=text[text_offset:text_offset + text_limit], text_offset=text_offset,
                        text_characters=len(text), next_text_offset=text_offset + text_limit if len(text) > text_offset + text_limit else None)
            results.append(item)
        return {'frames': results, 'missing': [str(value) for value in frame_ids if str(value) not in {row['frame_id'] for row in results}]}

    def semantic_search(self, query, since=None, until=None, app=None, limit=30, offset=0):
        from openrelife.nlp import get_embedding
        limit = bounded(limit, 30, 100); offset = int(offset)
        if not query or len(query) > 2000 or offset < 0:
            raise ValueError('Provide a query and valid pagination')
        result = database.search_entries_streaming(get_embedding(query), query_text=query,
            since=instant(since), until=instant(until), app=app, limit=limit, offset=offset)
        identifiers = [row['timestamp'] for row in result['results']]
        metadata = self.find_frames(frame_ids=identifiers, limit=200)
        by_id = {row['timestamp']: row for row in metadata['frames']}
        return {'frames': [by_id[value] for value in identifiers if value in by_id],
                'total': result['total'], 'next_offset': offset + limit if result['has_more'] else None}

    def image(self, frame_id, max_dimension=1600):
        max_dimension = bounded(max_dimension, 1600, 2400)
        timestamp = int(frame_id)
        with database._connect() as conn:
            if not conn.execute('SELECT 1 FROM entries WHERE timestamp=?', (timestamp,)).fetchone():
                raise ValueError('Frame no longer exists')
        with Image.open(self.images / f'{timestamp}.webp') as original:
            image = original.convert('RGB'); image.thumbnail((max_dimension, max_dimension))
            output = io.BytesIO(); image.save(output, 'JPEG', quality=85)
        return {'frame_id': str(timestamp), 'mime_type': 'image/jpeg',
                'data': base64.b64encode(output.getvalue()).decode(), 'width': image.width, 'height': image.height}

    def neighbors(self, frame_id, before=5, after=5, window_seconds=1800):
        frame = int(frame_id); before = bounded(before, 5, 50); after = bounded(after, 5, 50)
        window = bounded(window_seconds, 1800, 86400) * 1_000_000
        with database._connect() as conn:
            prior = [r[0] for r in conn.execute('SELECT timestamp FROM entries WHERE timestamp < ? AND timestamp >= ? ORDER BY timestamp DESC LIMIT ?', (frame, frame - window, before))]
            later = [r[0] for r in conn.execute('SELECT timestamp FROM entries WHERE timestamp > ? AND timestamp <= ? ORDER BY timestamp LIMIT ?', (frame, frame + window, after))]
        result = self.find_frames(frame_ids=[*prior, frame, *later], limit=200)
        result['frames'].sort(key=lambda item: item['timestamp'])
        result['gaps_seconds'] = [round((b['timestamp'] - a['timestamp']) / 1e6, 3) for a, b in zip(result['frames'], result['frames'][1:])]
        result['note'] = 'These are observed frames, not a guaranteed event start or duration.'
        return result

    def intervals(self, gap_seconds=180, limit=50, offset=0, **filters):
        gap = bounded(gap_seconds, 180, 3600) * 1_000_000
        limit = bounded(limit, 50, 100); offset = int(offset)
        if offset < 0:
            raise ValueError('Invalid offset')
        where, args = self._where(**filters)
        sql = ('WITH filtered AS (SELECT timestamp,app FROM entries' + where + '), '
               'marked AS (SELECT *,CASE WHEN timestamp-LAG(timestamp) OVER(ORDER BY timestamp)>? '
               'OR app IS NOT LAG(app) OVER(ORDER BY timestamp) THEN 1 ELSE 0 END AS boundary FROM filtered), '
               'grouped AS (SELECT *,SUM(boundary) OVER(ORDER BY timestamp) AS group_id FROM marked) '
               'SELECT MIN(timestamp),MAX(timestamp),app,COUNT(*) FROM grouped GROUP BY group_id '
               'ORDER BY MIN(timestamp) DESC LIMIT ? OFFSET ?')
        with database._connect() as conn:
            rows = conn.execute(sql, [*args, gap, limit + 1, offset]).fetchall()
        return {'intervals': [{'first_frame_id': str(first), 'last_frame_id': str(last), 'first_observed': iso(first), 'last_observed': iso(last), 'app': app, 'frame_count': count} for first, last, app, count in rows[:limit]],
                'next_offset': offset + limit if len(rows) > limit else None,
                'note': 'Groups are based on matching captured frames; inspect neighbors before inferring event boundaries.'}

    def propose_deletion(self, frame_ids=None, intervals=None, reason='', selection_id=None, _run_id=None):
        with self._delete_lock:
            return self._propose_deletion(frame_ids, intervals, reason, selection_id, _run_id)

    def _propose_deletion(self, frame_ids=None, intervals=None, reason='', selection_id=None, _run_id=None):
        frame_ids, intervals = frame_ids or [], intervals or []
        if len(frame_ids) > 5000 or len(intervals) > 50 or not (frame_ids or intervals):
            raise ValueError('Provide up to 5000 instants or 50 disjoint intervals')
        clauses, args = [], []
        for period in intervals:
            start, end = instant(period['since']), instant(period['until'])
            if start is None or end is None or start > end:
                raise ValueError('Each interval needs valid since/until bounds')
            clauses.append('(timestamp >= ? AND timestamp <= ?)'); args.extend([start, end])
        with database._connect() as conn:
            resolved = {}
            for offset in range(0, len(frame_ids), 500):
                part = [int(value) for value in frame_ids[offset:offset + 500]]
                for row in conn.execute('SELECT id,timestamp FROM entries WHERE timestamp IN (' + ','.join('?' for _ in part) + ')', part):
                    resolved[row[1]] = row
            if clauses:
                for row in conn.execute('SELECT id,timestamp FROM entries WHERE ' + ' OR '.join(clauses) + ' ORDER BY timestamp DESC LIMIT 100001', args):
                    resolved[row[1]] = row
            rows = sorted(resolved.values(), key=lambda row: row[1], reverse=True)
        if len(rows) > 100000:
            raise ValueError('Selection too large; narrow the time intervals')
        frames = []
        for rid, stamp in rows:
            try:
                info = (self.images / f'{stamp}.webp').lstat()
                identity = [info.st_size, info.st_mtime_ns, info.st_ino]
            except FileNotFoundError:
                identity = None
            frames.append({'id': rid, 'timestamp': stamp, 'identity': identity})
        if selection_id:
            manifest = self._manifest(selection_id)
            if manifest.get('run_id') and manifest['run_id'] != _run_id:
                raise ValueError('This proposal belongs to another request; create a new proposal')
            if manifest.get('deleted_at'):
                raise ValueError('This selection was already confirmed; create a new proposal')
            combined = {row['timestamp']: row for row in frames}
            combined.update({row['timestamp']: row for row in manifest['frames']})
            if len(combined) > 100000:
                raise ValueError('Selection too large')
            frames = sorted(combined.values(), key=lambda row: row['timestamp'], reverse=True)
            manifest.update(frames=frames, reason=str(reason or manifest['reason'])[:2000], updated_at=time.time())
        else:
            manifest = {'selection_id': secrets.token_hex(16), 'created_at': time.time(), 'reason': str(reason)[:2000], 'frames': frames}
        if _run_id:
            manifest['run_id'] = _run_id
        manifest['digest'] = hashlib.sha256(json.dumps(frames, sort_keys=True).encode()).hexdigest()
        self.selections.mkdir(parents=True, exist_ok=True)
        path = self.selections / f"{manifest['selection_id']}.json"
        temporary = path.with_suffix('.tmp')
        with temporary.open('w') as stream:
            os.chmod(temporary, 0o600); json.dump(manifest, stream)
        os.replace(temporary, path)
        total = 0
        previous = sorted(self.selections.glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True)
        for index, saved in enumerate(previous):
            total += saved.stat().st_size
            if index >= 20 or total > 100 * 1024 * 1024 or time.time() - saved.stat().st_mtime > 7 * 86400:
                saved.unlink(missing_ok=True)
        return self.selection(manifest['selection_id'])

    def _manifest(self, selection_id):
        if len(selection_id) != 32 or any(c not in '0123456789abcdef' for c in selection_id):
            raise ValueError('Invalid selection identifier')
        return json.loads((self.selections / f'{selection_id}.json').read_text())

    def selection(self, selection_id):
        manifest = self._manifest(selection_id)
        return {key: value for key, value in manifest.items() if key != 'frames'} | {
            'frame_ids': [str(row['timestamp']) for row in manifest['frames']],
            'frame_bytes': {str(row['timestamp']): row['identity'][0] if row['identity'] else 0 for row in manifest['frames']},
            'count': len(manifest['frames']), 'bytes': sum(row['identity'][0] for row in manifest['frames'] if row['identity']),
            'requires_app_confirmation': not bool(manifest.get('deleted_at'))}

    def discard_selection(self, selection_id):
        """Remove only the proposal manifest, never screenshots or database rows."""
        with self._delete_lock:
            manifest = self._manifest(selection_id)
            (self.selections / f'{selection_id}.json').unlink(missing_ok=True)
            return manifest.get('run_id')

    def delete_reviewed(self, selection_id, digest, frame_ids):
        """App-only operation. Never register this method as an agent/MCP tool."""
        with self._delete_lock:
            manifest = self._manifest(selection_id)
            if not secrets.compare_digest(manifest['digest'], str(digest)):
                raise ValueError('Selection changed; reload the preview')
            requested = {int(value) for value in frame_ids}
            approved = {row['timestamp']: row for row in manifest['frames']}
            if not requested or not requested.issubset(approved):
                raise ValueError('Confirmation must be a nonempty subset of the preview')
            deleted, errors, changed = [], [], []
            for stamp in sorted(requested):
                item = approved[stamp]; image = self.images / f'{stamp}.webp'
                try:
                    try:
                        info = image.lstat()
                        if image.is_symlink() or [info.st_size, info.st_mtime_ns, info.st_ino] != item['identity']:
                            changed.append(str(stamp)); continue
                    except FileNotFoundError:
                        pass
                    with database._connect() as conn:
                        row = conn.execute('SELECT id FROM entries WHERE timestamp=?', (stamp,)).fetchone()
                        if row is not None and row[0] != item['id']:
                            changed.append(str(stamp)); continue
                        conn.execute('DELETE FROM entries WHERE id=? AND timestamp=?', (item['id'], stamp))
                    database._notify_deleted([stamp])
                    image.unlink(missing_ok=True)
                    deleted.append(str(stamp))
                except (OSError, sqlite3.Error) as error:
                    errors.append({'frame_id': str(stamp), 'error': str(error)})
            result = {'deleted': deleted, 'errors': errors, 'changed': changed, 'complete': not (errors or changed)}
            if result['complete']:
                manifest.update(deleted_at=time.time(), frames=[], reason='')
                target = self.selections / f'{selection_id}.json'
                temporary = target.with_suffix('.tmp')
                with temporary.open('w') as stream:
                    os.chmod(temporary, 0o600); json.dump(manifest, stream)
                os.replace(temporary, target)
            return result
