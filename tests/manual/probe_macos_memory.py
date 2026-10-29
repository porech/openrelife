"""Measure native memory retention on a persistent Python worker thread.

Run with the app's Python/PyObjC environment on macOS, for example:
    python tests/manual/probe_macos_memory.py vision --pool none --count 60
    python tests/manual/probe_macos_memory.py vision --pool call --count 60
    python tests/manual/probe_macos_memory.py buffer --pool outer --count 60

Each invocation starts a fresh process. Pipeline mode calls the Flask test client
with synthetic images and a temporary database; it never contacts the installed
server. Other modes do not call app endpoints. User data and settings are never
changed. Vision uses a synthetic repository fixture. Metadata
queries read window information but never print or save titles. OS permissions
can make those queries return empty results, limiting their diagnostic value.
"""
import argparse
import contextlib
import ctypes
import gc
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import tempfile


class _Usage(ctypes.Structure):
    # rusage_info_v0 from the macOS SDK's sys/resource.h.
    _fields_ = [("uuid", ctypes.c_ubyte * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user", "system", "pkg", "interrupt", "pageins", "wired",
            "resident", "footprint", "start", "exit",
        )
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("vision", "buffer", "metadata", "embeddings", "pipeline"))
    parser.add_argument("--pool", choices=("none", "call", "outer"), default="none")
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--vmmap", type=Path, help="Save a native summary before the outer pool drains")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2],
                        help="Import backend code from this root, including a packaged app's Resources")
    options = parser.parse_args()
    if sys.platform != "darwin" or options.count < 1:
        parser.error("Requires macOS and a positive count")

    sys.path.insert(0, str(options.source_root))
    import objc
    import Foundation  # noqa: F401: register NSAutoreleasePool before the worker starts

    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.proc_pid_rusage.restype = ctypes.c_int

    def sample(stage):
        # Python collection alone does not drain native autorelease pools.
        gc.collect()
        usage = _Usage()
        if lib.proc_pid_rusage(os.getpid(), 0, ctypes.byref(usage)):
            raise OSError(ctypes.get_errno(), "proc_pid_rusage failed")
        print(json.dumps(dict(
            kind=options.kind, pool=options.pool, stage=stage,
            footprint_MiB=round(usage.footprint / 2**20, 2),
            resident_MiB=round(usage.resident / 2**20, 2),
        )), flush=True)

    storage = None
    if options.kind in ("vision", "buffer", "pipeline"):
        import numpy as np
        from PIL import Image
        from openrelife.apple_vision_ocr import extract_text_with_vision, _np_image_to_cgimage
        fixture = Path(__file__).resolve().parents[1] / "fixtures/apple_vision/english_simple.png"
        with Image.open(fixture) as image:
            arr = np.array(image.convert("RGB").resize((1920, 1080)))

        if options.kind == "pipeline":
            storage = tempfile.TemporaryDirectory(prefix="openrelife-memory-pipeline-")
            # Configuration must be set before importing the Flask app. No
            # capture threads or server are started by importing the module.
            sys.argv = [sys.argv[0], "--storage-path", storage.name]
            from openrelife import app as app_module, screenshot, database, config
            database.create_db()
            screenshot.set_use_apple_vision(True)
            case = 0

        def operation():
            nonlocal case
            if options.kind == "buffer":
                _np_image_to_cgimage(arr)
            elif options.kind == "pipeline":
                case += 1
                timestamp = 1_000_000 + case
                path = Path(config.screenshots_path) / f"{timestamp}.webp"
                Image.fromarray(arr).save(path, lossless=True)
                database.insert_entry_stub(timestamp, "Synthetic", "Memory probe")
                with app_module.app.test_client() as client:
                    response = client.post(f"/api/ocr-now/{timestamp}")
                    data = response.get_json()
                entry = database.get_entry_by_timestamp(timestamp)
                if (response.status_code != 200 or not data.get("success")
                        or "hello" not in data.get("text", "").lower()
                        or entry is None or not np.linalg.norm(entry.embedding)):
                    raise RuntimeError("Synthetic on-demand OCR pipeline failed")
                path.unlink()
            else:
                text, words = extract_text_with_vision(arr)
                if "hello" not in text.lower() or not words:
                    raise RuntimeError("Synthetic OCR did not produce the expected text")
    elif options.kind == "embeddings":
        from openrelife.nlp import get_embedding, get_model
        import numpy as np

        model = get_model()
        if model is None:
            raise RuntimeError("Embedding model failed to load")
        print(json.dumps(dict(embedding_device=str(model.device))), flush=True)
        case = 0

        def operation():
            nonlocal case
            # Vary both batch and token dimensions, as real captured text does.
            case += 1
            lines = ["hello world " * (4 + (case * 7 + row) % 55)
                     for row in range(1 + case % 32)]
            vector = get_embedding("\n".join(lines))
            if vector.shape != (384,) or not np.linalg.norm(vector):
                raise RuntimeError("Embedding failed")
    else:
        from openrelife.utils import (
            get_active_app_name_osx, get_active_window_title_osx,
            _get_all_visible_browser_windows_osx,
        )

        def operation():
            get_active_app_name_osx()
            get_active_window_title_osx()
            _get_all_visible_browser_windows_osx()

    print(json.dumps(dict(python=platform.python_version(), macOS=platform.mac_ver()[0],
                          pyobjc=objc.__version__, pid=os.getpid())), flush=True)
    errors = []

    def run():
        try:
            outer = objc.autorelease_pool() if options.pool == "outer" else contextlib.nullcontext()
            with outer:
                sample("start")
                for index in range(options.count):
                    inner = objc.autorelease_pool() if options.pool == "call" else contextlib.nullcontext()
                    with inner:
                        operation()
                    if (index + 1) % max(1, options.count // 4) == 0:
                        sample(index + 1)
                sample("before_outer_drain")
                if options.vmmap:
                    with options.vmmap.open("w") as output:
                        subprocess.run(["vmmap", "-summary", str(os.getpid())],
                                       stdout=output, check=True, timeout=30)
            sample("after_outer_drain")
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join()
    if errors:
        raise errors[0]
    sample("thread_exited")
    if storage is not None:
        storage.cleanup()


if __name__ == "__main__":
    main()
