import logging
import logging.handlers
import os
import queue
import time
from typing import List, Tuple

import mss
import numpy as np
from PIL import Image

from openrelife.config import appdata_folder, screenshots_path, args
from openrelife.database import insert_entry_stub, update_entry_ocr, get_pending_ocr_timestamps, get_pending_ocr_timestamps_in_set
from openrelife.nlp import get_embedding
from openrelife.ocr import extract_text_from_image
from openrelife.utils import (
    get_active_app_name,
    get_active_window_title,
    is_user_active,
    is_browser_incognito,
)

# File logger for capture/OCR diagnostics
_log_path = os.path.join(appdata_folder, "capture.log")
_logger = logging.getLogger("openrelife.capture")
_logger.setLevel(logging.DEBUG)
_file_handler = logging.handlers.RotatingFileHandler(
    _log_path, maxBytes=2 * 1024 * 1024, backupCount=3
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_logger.addHandler(_file_handler)

# Queue for pending OCR work: timestamps only (8 bytes each, no memory concern)
_ocr_queue: queue.Queue = queue.Queue(maxsize=50000)


def mean_structured_similarity_index(
    img1: np.ndarray, img2: np.ndarray, L: int = 255
) -> float:
    """Calculates the Mean Structural Similarity Index (MSSIM) between two images.

    Args:
        img1: The first image as a NumPy array (RGB).
        img2: The second image as a NumPy array (RGB).
        L: The dynamic range of the pixel values (default is 255).

    Returns:
        The MSSIM value between the two images (float between -1 and 1).
    """
    K1, K2 = 0.01, 0.03
    C1, C2 = (K1 * L) ** 2, (K2 * L) ** 2

    def rgb2gray(img: np.ndarray) -> np.ndarray:
        """Converts an RGB image to grayscale."""
        return 0.2989 * img[..., 0] + 0.5870 * img[..., 1] + 0.1140 * img[..., 2]

    img1_gray: np.ndarray = rgb2gray(img1)
    img2_gray: np.ndarray = rgb2gray(img2)
    mu1: float = np.mean(img1_gray)
    mu2: float = np.mean(img2_gray)
    sigma1_sq = np.var(img1_gray)
    sigma2_sq = np.var(img2_gray)
    sigma12 = np.mean((img1_gray - mu1) * (img2_gray - mu2))
    ssim_index = ((2 * mu1 * mu2 + C1) * (2 * sigma12 + C2)) / (
        (mu1**2 + mu2**2 + C1) * (sigma1_sq + sigma2_sq + C2)
    )
    return ssim_index


def _downscale_for_comparison(img: np.ndarray, max_height: int = 270) -> np.ndarray:
    """Downscale image for fast MSSIM comparison."""
    h, w = img.shape[:2]
    if h <= max_height:
        return img
    scale = max_height / h
    small = Image.fromarray(img).resize((int(w * scale), max_height), Image.NEAREST)
    return np.array(small)


def is_similar(
    img1: np.ndarray, img2: np.ndarray, similarity_threshold: float = 0.95
) -> bool:
    """Checks if two images are similar based on MSSIM."""
    similarity: float = mean_structured_similarity_index(img1, img2)
    return similarity >= similarity_threshold


def _take_screenshots_macos() -> List[np.ndarray]:
    """Fast screenshot capture using native macOS screencapture command.

    mss.grab() has a 30-second timeout bug on macOS Tahoe (26.x).
    Native screencapture takes ~0.2 seconds.
    """
    import subprocess
    import tempfile

    screenshots: List[np.ndarray] = []
    try:
        # screencapture -x = no sound, -D = display number (1-based)
        # Get number of displays
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True, timeout=5
        )
        import json
        displays_data = json.loads(result.stdout)
        num_displays = 0
        for gpu in displays_data.get("SPDisplaysDataType", []):
            num_displays += len(gpu.get("spdisplays_ndrvs", []))
        num_displays = max(1, num_displays)

        if args.primary_monitor_only:
            display_indices = [1]
        else:
            display_indices = range(1, num_displays + 1)

        for display_id in display_indices:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                subprocess.run(
                    ["screencapture", "-x", "-D", str(display_id), "-t", "png", tmp_path],
                    timeout=10, check=True
                )
                img = Image.open(tmp_path).convert("RGB")
                screenshots.append(np.array(img))
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    except Exception as e:
        _logger.error(f"macOS screencapture failed: {e}")

    return screenshots


def _take_screenshots_mss() -> List[np.ndarray]:
    """Fallback screenshot capture using mss (cross-platform)."""
    screenshots: List[np.ndarray] = []
    with mss.mss() as sct:
        monitor_indices = range(1, len(sct.monitors))
        if args.primary_monitor_only:
            monitor_indices = [1]
        for i in monitor_indices:
            if i < len(sct.monitors):
                sct_img = sct.grab(sct.monitors[i])
                screenshot = np.array(sct_img)[:, :, [2, 1, 0]]
                screenshots.append(screenshot)
    return screenshots


import sys as _sys

def take_screenshots() -> List[np.ndarray]:
    """Takes screenshots of all connected monitors.

    Uses native screencapture on macOS (fast), falls back to mss on other platforms.
    """
    if _sys.platform == "darwin":
        return _take_screenshots_macos()
    return _take_screenshots_mss()

    return screenshots



# Global flag to control recording pause state
is_recording_paused = False
screenshot_interval = 3  # Default interval in seconds

def set_recording_paused(paused: bool):
    global is_recording_paused
    is_recording_paused = paused

def get_recording_paused() -> bool:
    global is_recording_paused
    return is_recording_paused

def set_screenshot_interval(interval: int):
    global screenshot_interval
    screenshot_interval = max(1, interval)

def get_screenshot_interval() -> int:
    global screenshot_interval
    return screenshot_interval

# Quality Settings
screenshot_quality = "medium"

def set_screenshot_quality(quality: str):
    global screenshot_quality
    if quality in ['low', 'medium', 'high']:
        screenshot_quality = quality

def get_screenshot_quality() -> str:
    global screenshot_quality
    return screenshot_quality

# Incognito Skip Settings (default: enabled - do not record incognito)
skip_incognito_recording = True

def set_skip_incognito_recording(skip: bool):
    global skip_incognito_recording
    skip_incognito_recording = skip

def get_skip_incognito_recording() -> bool:
    global skip_incognito_recording
    return skip_incognito_recording

def _wait_with_incognito_check(seconds: float) -> bool:
    """Wait for specified seconds. Incognito is checked once per capture cycle,
    not during the wait, to avoid expensive Accessibility API calls."""
    time.sleep(seconds)
    return False


def record_screenshots_thread():
    """Capture thread: takes screenshots every N seconds, saves image + DB stub.

    OCR/embedding processing happens in a separate worker thread.
    """
    _logger.info("Capture thread started")
    cycle_count = 0
    saved_count = 0

    # Keep only downscaled thumbnails for comparison (~270p instead of 4K)
    try:
        last_thumbs = [_downscale_for_comparison(s) for s in take_screenshots()]
        _logger.info(f"Initial screenshots taken, {len(last_thumbs)} monitor(s)")
    except Exception as e:
        _logger.error(f"Failed to take initial screenshots: {e}")
        last_thumbs = None

    while True:
        try:
            cycle_count += 1

            if is_recording_paused:
                if cycle_count % 60 == 0:
                    _logger.debug("Recording paused")
                time.sleep(1)
                continue

            active_title = get_active_window_title()
            if active_title and "OpenReLife" in active_title:
                time.sleep(1)
                continue

            if skip_incognito_recording and is_browser_incognito():
                if cycle_count % 60 == 0:
                    _logger.debug("Incognito mode detected, skipping")
                time.sleep(1)
                continue

            if not is_user_active():
                if cycle_count % 60 == 0:
                    _logger.debug("User inactive")
                time.sleep(3)
                continue

            screenshots = take_screenshots()

            if last_thumbs is None or len(last_thumbs) != len(screenshots):
                _logger.info(f"Reinitializing thumbnails ({len(screenshots)} monitors)")
                last_thumbs = [_downscale_for_comparison(s) for s in screenshots]

            for i, screenshot in enumerate(screenshots):
                thumb = _downscale_for_comparison(screenshot)

                if not is_similar(thumb, last_thumbs[i]):
                    last_thumbs[i] = thumb

                    image = Image.fromarray(screenshot)
                    width, height = image.size

                    if screenshot_quality == 'high':
                        save_kwargs = {'lossless': True}
                    elif screenshot_quality == 'medium':
                        image = image.resize((int(width * 0.95), int(height * 0.95)), Image.LANCZOS)
                        save_kwargs = {'lossless': False, 'quality': 95}
                    else:
                        image = image.resize((int(width * 0.8), int(height * 0.8)), Image.LANCZOS)
                        save_kwargs = {'lossless': False, 'quality': 80}

                    timestamp = int(time.time() * 1000000)
                    filename = f"{timestamp}.webp"
                    image.save(
                        os.path.join(screenshots_path, filename),
                        format="webp",
                        **save_kwargs
                    )

                    active_app_name = get_active_app_name() or "Unknown App"
                    active_window_title = get_active_window_title() or "Unknown Title"
                    insert_entry_stub(timestamp, active_app_name, active_window_title)

                    try:
                        _ocr_queue.put_nowait(timestamp)
                    except queue.Full:
                        try:
                            _ocr_queue.get_nowait()
                        except queue.Empty:
                            pass
                        _ocr_queue.put_nowait(timestamp)

                    saved_count += 1
                    if saved_count % 10 == 0:
                        _logger.info(f"Saved {saved_count} screenshots (queue: {_ocr_queue.qsize()})")

            _wait_with_incognito_check(screenshot_interval)

        except Exception as e:
            _logger.error(f"Capture thread error (retrying in 5s): {e}", exc_info=True)
            last_thumbs = None  # Force reinit on next cycle
            time.sleep(5)


ocr_cooldown = 90  # seconds between OCR batches

def set_ocr_cooldown(seconds: int):
    global ocr_cooldown
    ocr_cooldown = max(10, seconds)

def get_ocr_cooldown() -> int:
    global ocr_cooldown
    return ocr_cooldown


# OCR compute mode: aggressive, smart (default), on_charge_only
ocr_compute_mode = "smart"

def set_ocr_compute_mode(mode: str):
    global ocr_compute_mode
    if mode in ("aggressive", "smart", "on_charge_only"):
        ocr_compute_mode = mode

def get_ocr_compute_mode() -> str:
    global ocr_compute_mode
    return ocr_compute_mode


# OCR engine selection: True -> Apple Vision (M-series only); False -> doctr
_use_apple_vision: bool = False


def set_use_apple_vision(enabled) -> None:
    """Enable/disable Apple Vision backend. Coerces truthy/falsy values to bool."""
    global _use_apple_vision
    _use_apple_vision = bool(enabled)


def get_use_apple_vision() -> bool:
    global _use_apple_vision
    return _use_apple_vision


def _is_on_ac_power() -> bool:
    """Check if the Mac is plugged in."""
    try:
        import subprocess
        output = subprocess.check_output(["pmset", "-g", "batt"], timeout=2).decode()
        return "AC Power" in output
    except Exception:
        return False


def _has_battery() -> bool:
    """Check if this device has a battery (laptop vs desktop)."""
    try:
        import subprocess
        output = subprocess.check_output(["pmset", "-g", "batt"], timeout=2).decode()
        return "InternalBattery" in output
    except Exception:
        return False


def _get_battery_level() -> int:
    """Get battery percentage (0-100). Returns 100 if no battery or on error."""
    try:
        import subprocess
        output = subprocess.check_output(["pmset", "-g", "batt"], timeout=2).decode()
        for line in output.splitlines():
            if "InternalBattery" in line:
                # Format: "-InternalBattery-0 (id=...)	57%; ..."
                pct = int(line.split("\t")[1].split("%")[0].strip())
                return pct
    except Exception:
        pass
    return 100


def _process_ocr_batch(timestamps_list, num_threads=4, use_apple_vision=False):
    """Run OCR on a batch of timestamps in a subprocess.

    Runs in a separate process so that all memory (PyTorch, numpy arrays,
    OCR models) is fully reclaimed by the OS when the process exits.
    """
    import torch
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    torch.set_num_threads(num_threads)

    for ts in timestamps_list:
        try:
            image_path = os.path.join(screenshots_path, f"{ts}.webp")
            if not os.path.exists(image_path):
                continue
            img = Image.open(image_path).convert("RGB")
            # Downscale to 1080p for OCR — same text quality, ~3x faster
            w, h = img.size
            if h > 1080:
                scale = 1080 / h
                img = img.resize((int(w * scale), 1080), Image.LANCZOS)
            screenshot = np.array(img)
            del img

            text, words_coords = extract_text_from_image(screenshot, use_apple_vision=use_apple_vision)
            del screenshot

            embedding = get_embedding(text) if text.strip() else np.zeros(384, dtype=np.float32)
            update_entry_ocr(ts, text, embedding, words_coords)
        except Exception as e:
            print(f"OCR worker error for {ts}: {e}")


def _get_batch_params(pending_count: int) -> tuple:
    """Determine batch size, thread count, and cooldown multiplier.

    Returns (max_batch_size, num_threads, cooldown_multiplier).
    cooldown_multiplier: 1.0 = use configured cooldown, 0.3 = aggressive short cooldown.
    """
    on_ac = _is_on_ac_power()
    has_bat = _has_battery()
    user_active = is_user_active()
    on_battery = has_bat and not on_ac
    battery_full = on_ac and _get_battery_level() >= 100

    mode = ocr_compute_mode

    if mode == "aggressive":
        return (20, 4, 0.3)

    if mode == "on_charge_only":
        if on_battery:
            return (0, 0, 1.0)
        if battery_full:
            return (20, 4, 0.5)
        if not user_active:
            return (min(pending_count, 50), 2, 1.0)
        return (10, 4, 1.0)

    # smart (default)
    # When user is active: 2 threads to keep CPU ~200% instead of 500%
    if not has_bat:
        return (10, 2, 1.0)
    if on_battery:
        return (5, 2, 1.0)
    if not user_active:
        # Idle + AC: larger batches for recovery, capped at 20
        if battery_full:
            return (min(pending_count, 20), 4, 0.15)
        return (min(pending_count, 20), 2, 1.0)
    if battery_full:
        return (10, 2, 0.5)
    # AC + active
    return (10, 2, 1.0)


def ocr_worker_thread():
    """Background worker: processes OCR in batches with cooldown periods.

    Spawns a subprocess for each batch so memory is fully reclaimed
    by the OS after processing (avoids Python heap fragmentation).

    Compute modes:
    - aggressive: large batches (20), short cooldown, always runs
    - smart (default): adapts to battery/AC/idle state
    - on_charge_only: skips OCR on battery, recovers when plugged in
    """
    from multiprocessing import Process
    _logger.info("OCR worker thread started")

    # On startup, re-queue any orphaned stub entries (text='') from previous sessions
    orphans = get_pending_ocr_timestamps()
    if orphans:
        _logger.info(f"Found {len(orphans)} orphaned entries without OCR, re-queuing")
        for ts in orphans:
            try:
                _ocr_queue.put_nowait(ts)
            except queue.Full:
                _logger.warning(f"Queue full, {len(orphans) - orphans.index(ts)} orphans not queued")
                break

    while True:
        # Block until at least one item arrives
        first_ts = _ocr_queue.get()

        # Check power mode to determine accumulation wait
        _, _, cooldown_mult_pre = _get_batch_params(_ocr_queue.qsize() + 1)
        accumulate_time = ocr_cooldown * cooldown_mult_pre
        _logger.debug(f"OCR worker: first item received, waiting {accumulate_time:.0f}s for batch (mult={cooldown_mult_pre})")
        time.sleep(accumulate_time)

        # Collect all pending timestamps
        pending = [first_ts]
        while not _ocr_queue.empty():
            try:
                pending.append(_ocr_queue.get_nowait())
            except queue.Empty:
                break

        max_batch, threads, cooldown_mult = _get_batch_params(len(pending))
        _logger.info(f"OCR batch: {len(pending)} pending, max_batch={max_batch}, threads={threads}, cooldown_mult={cooldown_mult}, mode={ocr_compute_mode}")

        if max_batch == 0:
            # on_charge_only + battery: put everything back, wait
            for ts in pending:
                try:
                    _ocr_queue.put_nowait(ts)
                except queue.Full:
                    break
            _ocr_queue.task_done()  # for first_ts
            continue

        batch = pending[:max_batch]

        # Put back unprocessed timestamps for next cycle
        overflow = pending[max_batch:]
        for ts in overflow:
            try:
                _ocr_queue.put_nowait(ts)
            except queue.Full:
                break

        # Process batch in a subprocess — all memory freed on exit
        # Timeout: 10s per frame max, kill if hung
        batch_timeout = max(120, len(batch) * 10)
        use_av = get_use_apple_vision()  # snapshot at batch start; setting changes apply to the next batch
        engine = "vision" if use_av else "doctr"
        _logger.info(f"OCR subprocess starting: {len(batch)} frames, {threads} threads, engine={engine} (timeout={batch_timeout}s)")
        batch_start = time.time()
        proc = Process(target=_process_ocr_batch, args=(batch, threads, use_av))
        proc.start()
        proc.join(timeout=batch_timeout)
        was_hung = proc.is_alive()
        if was_hung:
            _logger.error(f"OCR subprocess hung after {batch_timeout}s, killing it")
            proc.kill()
            proc.join()
        batch_duration = time.time() - batch_start
        _logger.info(f"OCR subprocess done: {len(batch)} frames in {batch_duration:.0f}s ({batch_duration/len(batch):.1f}s/frame), overflow={len(overflow)}, hung={was_hung}")

        # Mark all batch tasks as done in the queue
        for _ in batch:
            _ocr_queue.task_done()

        # Re-queue any batch frames that still have text=NULL (subprocess didn't finish them)
        # This catches both hung subprocesses and partial completions.
        unprocessed = get_pending_ocr_timestamps_in_set(batch)
        if unprocessed:
            _logger.warning(f"Re-queuing {len(unprocessed)} unprocessed frames from batch")
            for ts in unprocessed:
                try:
                    _ocr_queue.put_nowait(ts)
                except queue.Full:
                    break

        # Cooldown between batches.
        # In boost mode (cooldown_mult < 1.0): use short fixed cooldown
        # In normal mode: rest at least as long as the batch took
        if cooldown_mult < 1.0:
            effective_cooldown = ocr_cooldown * cooldown_mult
        else:
            effective_cooldown = max(ocr_cooldown, batch_duration)
        _logger.info(f"OCR cooldown: {effective_cooldown:.0f}s (mult={cooldown_mult})")
        time.sleep(effective_cooldown)

