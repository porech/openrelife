"""Bounded, app-only context for deletion proposals; never resumes commands."""
import json
import os
from pathlib import Path
import threading
import time


class RunContexts:
    MAX_BYTES = 2 * 1024 * 1024
    MAX_DEPENDENCIES = 10000

    def __init__(self, storage):
        self.folder = Path(storage) / 'agent' / 'contexts'
        self.lock = threading.RLock()
        self.index = {}
        self.prune()
        for path in self.folder.glob('*.json'):
            try:
                record = self._read(path.stem)
                self.index[path.stem] = (set(record['seen']), record['dependencies_complete'])
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def _path(self, run_id):
        if len(run_id) != 32 or any(char not in '0123456789abcdef' for char in run_id):
            raise ValueError('Invalid run identifier')
        return self.folder / f'{run_id}.json'

    def _read(self, run_id):
        path = self._path(run_id)
        if path.stat().st_size > self.MAX_BYTES:
            raise ValueError('Saved context exceeds its size limit')
        record = json.loads(path.read_text())
        if record['run']['run_id'] != run_id or len(record['seen']) > self.MAX_DEPENDENCIES:
            raise ValueError('Invalid saved context')
        return record

    def prune(self):
        with self.lock:
            total = 0
            paths = sorted(self.folder.glob('*.json'), key=lambda path: path.stat().st_mtime, reverse=True)
            for index, path in enumerate(paths):
                total += path.stat().st_size
                if index >= 30 or total > 100 * 1024 * 1024 or time.time() - path.stat().st_mtime > 30 * 86400:
                    path.unlink(missing_ok=True)
                    self.index.pop(path.stem, None)

    def save(self, run, summary):
        with self.lock:
            if run['state'] == 'purged' or (not run['selections'] and run['run_id'] not in self.index):
                return
            seen = sorted(run['seen'])
            record = {'run': summary | {'events': summary['events'][-10:]},
                      'seen': seen[:self.MAX_DEPENDENCIES],
                      'dependencies_complete': len(seen) <= self.MAX_DEPENDENCIES}
            new = not self._path(run['run_id']).exists()
            self._write(run['run_id'], record)
            if new:
                self.prune()

    def _write(self, run_id, record):
        encoded = json.dumps(record, ensure_ascii=False).encode()
        if len(encoded) > self.MAX_BYTES:
            raise ValueError('Saved context exceeds its size limit')
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(run_id)
        temporary = path.with_suffix('.tmp')
        try:
            with temporary.open('wb') as stream:
                os.chmod(temporary, 0o600)
                stream.write(encoded)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        self.index[run_id] = (set(record['seen']), record['dependencies_complete'])

    def discard_reference(self, run_id, selection_id):
        with self.lock:
            record = self._read(run_id)
            record['run']['selections'] = [value for value in record['run']['selections'] if value != selection_id]
            self._write(run_id, record)

    def get(self, run_id):
        with self.lock:
            run = self._read(run_id)['run'] | {'restored': True}
            if run['state'] in ('starting', 'running'):
                run.update(state='interrupted', error='The app closed while this request was running. It has not been resumed.')
            return run

    def invalidate(self, timestamps):
        with self.lock:
            if not timestamps:
                return set()
            affected = {key for key, (seen, complete) in self.index.items() if not complete or seen & timestamps}
            for run_id in affected:
                self._path(run_id).unlink(missing_ok=True)
                self.index.pop(run_id, None)
            # Unreadable/unknown caches have no trustworthy dependency map.
            for path in self.folder.glob('*.json'):
                if path.stem not in self.index:
                    path.unlink(missing_ok=True)
            return affected
