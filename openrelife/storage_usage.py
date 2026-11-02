"""Bounded, lazy background accounting for the configured screenshot folder."""
import os
from pathlib import Path
import shutil
import stat
import threading
import time


class ScreenshotStorageUsage:
    def __init__(self, folder, refresh_seconds=300):
        self.folder = Path(folder)
        self.refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._running = False
        self._deadline = 0
        self._generation = 0
        self._cached = {'state': 'pending', 'screenshots_bytes': None,
                        'logical_bytes': None, 'screenshot_count': None, 'updated_at': None}

    def invalidate(self, _deleted=None):
        with self._lock:
            self._generation += 1
            self._deadline = 0

    def snapshot(self):
        with self._lock:
            if not self._running and time.monotonic() >= self._deadline:
                self._running = True
                worker = threading.Thread(target=self._scan, args=(self._generation,),
                                          name='screenshot-storage-usage', daemon=True)
                worker.start()
            result = dict(self._cached, scanning=self._running)
        try:
            disk = shutil.disk_usage(self.folder)
            result.update(disk_total_bytes=disk.total, disk_free_bytes=disk.free)
        except OSError:
            result.update(disk_total_bytes=None, disk_free_bytes=None)
        return result

    def _scan(self, generation):
        allocated = logical = count = errors = 0
        pending = [self.folder]
        hardlinks = set()
        try:
            while pending:
                directory = pending.pop()
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            try:
                                info = entry.stat(follow_symlinks=False)
                                if stat.S_ISDIR(info.st_mode):
                                    pending.append(Path(entry.path))
                                elif stat.S_ISREG(info.st_mode) and Path(entry.name).suffix.lower() in ('.webp', '.png', '.jpg', '.jpeg', '.tiff', '.bmp'):
                                    count += 1
                                    logical += info.st_size
                                    # Track only multiply linked files, keeping memory
                                    # constant for the normal one-file-per-frame archive.
                                    inode = (info.st_dev, info.st_ino)
                                    if info.st_nlink > 1:
                                        if inode in hardlinks:
                                            continue
                                        hardlinks.add(inode)
                                    allocated += info.st_blocks * 512 if hasattr(info, 'st_blocks') else info.st_size
                            except FileNotFoundError:
                                # Capture and deletion can proceed during accounting.
                                continue
                            except OSError:
                                errors += 1
                except OSError:
                    errors += 1
            result = {'state': 'partial' if errors else 'ready', 'screenshots_bytes': allocated,
                      'logical_bytes': logical, 'screenshot_count': count,
                      'updated_at': time.time(), 'unreadable_entries': errors}
        except Exception:
            result = {'state': 'error', 'screenshots_bytes': None, 'logical_bytes': None,
                      'screenshot_count': None, 'updated_at': time.time()}
        with self._lock:
            self._cached = result
            self._deadline = time.monotonic() + self.refresh_seconds if generation == self._generation else 0
            self._running = False
