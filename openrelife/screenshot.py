import os
import queue
import time
from typing import List, Tuple

import mss
import numpy as np
from PIL import Image

from openrelife.config import screenshots_path, args
from openrelife.database import insert_entry_stub, update_entry_ocr
from openrelife.nlp import get_embedding
from openrelife.ocr import extract_text_from_image
from openrelife.utils import (
    get_active_app_name,
    get_active_window_title,
    is_user_active,
    is_browser_incognito,
)

# Queue for pending OCR work: timestamps only (images read from disk on demand)
_ocr_queue: queue.Queue = queue.Queue(maxsize=1000)


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
    img1: np.ndarray, img2: np.ndarray, similarity_threshold: float = 0.9
) -> bool:
    """Checks if two images are similar based on MSSIM."""
    similarity: float = mean_structured_similarity_index(img1, img2)
    return similarity >= similarity_threshold


def take_screenshots() -> List[np.ndarray]:
    """Takes screenshots of all connected monitors or just the primary one.

    Depending on the `args.primary_monitor_only` flag, captures either
    all monitors or only the primary monitor (index 1 in mss.monitors).

    Returns:
        A list of screenshots, where each screenshot is a NumPy array (RGB).
    """
    screenshots: List[np.ndarray] = []
    with mss.mss() as sct:
        # sct.monitors[0] is the combined view of all monitors
        # sct.monitors[1] is the primary monitor
        # sct.monitors[2:] are other monitors
        monitor_indices = range(1, len(sct.monitors))  # Skip the 'all monitors' entry

        if args.primary_monitor_only:
            monitor_indices = [1]  # Only index 1 corresponds to the primary monitor

        for i in monitor_indices:
            # Ensure the index is valid before attempting to grab
            if i < len(sct.monitors):
                monitor_info = sct.monitors[i]
                # Grab the screen
                sct_img = sct.grab(monitor_info)
                # Convert to numpy array and change BGRA to RGB
                screenshot = np.array(sct_img)[:, :, [2, 1, 0]]
                screenshots.append(screenshot)
            else:
                # Handle case where primary_monitor_only is True but only one monitor exists (all monitors view)
                # This case might need specific handling depending on desired behavior.
                # For now, we just skip if the index is out of bounds.
                print(f"Warning: Monitor index {i} out of bounds. Skipping.")

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
    """Wait for specified seconds, checking for incognito mode periodically.

    Args:
        seconds: Total time to wait in seconds.

    Returns:
        True if incognito was detected during wait, False otherwise.
    """
    if not skip_incognito_recording:
        time.sleep(seconds)
        return False

    # Check every min(seconds, 3) seconds normally
    # If incognito detected, check every 1 second until it's gone
    check_interval = min(seconds, 3.0)
    elapsed = 0.0

    while elapsed < seconds:
        if is_browser_incognito():
            # Incognito detected - wait here with frequent checks until it's gone
            while is_browser_incognito():
                time.sleep(1)
            # Incognito gone - restart the wait
            elapsed = 0.0
            continue

        sleep_time = min(check_interval, seconds - elapsed)
        time.sleep(sleep_time)
        elapsed += sleep_time

    return False


def record_screenshots_thread():
    """Capture thread: takes screenshots every N seconds, saves image + DB stub.

    OCR/embedding processing happens in a separate worker thread.
    """
    # Keep only downscaled thumbnails for comparison (~270p instead of 4K)
    last_thumbs = [_downscale_for_comparison(s) for s in take_screenshots()]

    while True:
        if is_recording_paused:
            time.sleep(1)
            continue

        active_title = get_active_window_title()
        if active_title and "OpenReLife" in active_title:
            time.sleep(1)
            continue

        if skip_incognito_recording and is_browser_incognito():
            time.sleep(1)
            continue

        if not is_user_active():
            time.sleep(3)
            continue

        screenshots = take_screenshots()

        for i, screenshot in enumerate(screenshots):
            thumb = _downscale_for_comparison(screenshot)

            if not is_similar(thumb, last_thumbs[i]):
                last_thumbs[i] = thumb

                # Save image to disk
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

                # Insert stub DB entry (no text/embedding yet)
                active_app_name = get_active_app_name() or "Unknown App"
                active_window_title = get_active_window_title() or "Unknown Title"
                insert_entry_stub(timestamp, active_app_name, active_window_title)

                # Queue timestamp for OCR processing (image read from disk later)
                try:
                    _ocr_queue.put_nowait(timestamp)
                except queue.Full:
                    try:
                        _ocr_queue.get_nowait()
                    except queue.Empty:
                        pass
                    _ocr_queue.put_nowait(timestamp)

        _wait_with_incognito_check(screenshot_interval)


ocr_cooldown = 90  # seconds between OCR batches

def set_ocr_cooldown(seconds: int):
    global ocr_cooldown
    ocr_cooldown = max(10, seconds)

def get_ocr_cooldown() -> int:
    global ocr_cooldown
    return ocr_cooldown


def _is_on_ac_power() -> bool:
    """Check if the Mac is plugged in."""
    try:
        import subprocess
        output = subprocess.check_output(["pmset", "-g", "batt"], timeout=2).decode()
        return "AC Power" in output
    except Exception:
        return False


def _process_ocr_batch(timestamps_list, num_threads=4):
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
            screenshot = np.array(img)
            del img

            text, words_coords = extract_text_from_image(screenshot)
            del screenshot

            embedding = get_embedding(text) if text.strip() else np.zeros(384, dtype=np.float32)
            update_entry_ocr(ts, text, embedding, words_coords)
        except Exception as e:
            print(f"OCR worker error for {ts}: {e}")


# Batch size limits per power mode
BATCH_SIZE_BATTERY = 5
BATCH_SIZE_AC_ACTIVE = 10
THREADS_NORMAL = 4
THREADS_IDLE_AC = 2


def ocr_worker_thread():
    """Background worker: processes OCR in batches with cooldown periods.

    Adapts behavior based on power state:
    - Battery: small batches (5), normal threads, adaptive cooldown
    - AC + active: medium batches (10), normal threads, adaptive cooldown
    - AC + idle: full backlog, fewer threads (2) to avoid overheating
    """
    from multiprocessing import Process

    while True:
        # Block until at least one item arrives
        first_ts = _ocr_queue.get()

        # Wait for more frames to accumulate
        time.sleep(ocr_cooldown)

        # Collect all pending timestamps
        pending = [first_ts]
        while not _ocr_queue.empty():
            try:
                pending.append(_ocr_queue.get_nowait())
            except queue.Empty:
                break

        # Determine power mode
        on_ac = _is_on_ac_power()
        user_active = is_user_active()

        if on_ac and not user_active:
            # Idle + charging: process entire backlog with throttled CPU
            batch = pending
            threads = THREADS_IDLE_AC
        elif on_ac:
            # Active + charging: medium batch
            batch = pending[:BATCH_SIZE_AC_ACTIVE]
            threads = THREADS_NORMAL
        else:
            # Battery: small batch
            batch = pending[:BATCH_SIZE_BATTERY]
            threads = THREADS_NORMAL

        # Put back any unprocessed timestamps
        overflow = pending[len(batch):]
        for ts in overflow:
            try:
                _ocr_queue.put_nowait(ts)
            except queue.Full:
                break

        # Process batch in a subprocess — all memory freed on exit
        batch_start = time.time()
        proc = Process(target=_process_ocr_batch, args=(batch, threads))
        proc.start()
        proc.join()
        batch_duration = time.time() - batch_start

        # Mark processed tasks as done
        for _ in batch:
            _ocr_queue.task_done()

        # Adaptive cooldown: rest at least as long as the batch took
        effective_cooldown = max(ocr_cooldown, batch_duration)
        time.sleep(effective_cooldown)

