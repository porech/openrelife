import os
import time
from typing import List, Tuple

import mss
import numpy as np
from PIL import Image

from openrelife.config import screenshots_path, args
from openrelife.database import insert_entry
from openrelife.nlp import get_embedding
from openrelife.ocr import extract_text_from_image
from openrelife.utils import (
    get_active_app_name,
    get_active_window_title,
    is_user_active,
    is_browser_incognito,
)


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


def is_similar(
    img1: np.ndarray, img2: np.ndarray, similarity_threshold: float = 0.9
) -> bool:
    """Checks if two images are similar based on MSSIM.

    Args:
        img1: The first image as a NumPy array.
        img2: The second image as a NumPy array.
        similarity_threshold: The threshold above which images are considered similar.

    Returns:
        True if the images are similar, False otherwise.
    """
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

def record_screenshots_thread():
    # TODO: fix the error from huggingface tokenizers
    import os

    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    last_screenshots = take_screenshots()

    while True:
        # Check if recording is manually paused
        if is_recording_paused:
            time.sleep(1)
            continue

        # Avoid recording the recorder (OpenReLife itself)
        active_title = get_active_window_title()
        if active_title and "OpenReLife" in active_title:
            time.sleep(1)
            continue

        # Skip recording if browser is in incognito mode (if enabled)
        if skip_incognito_recording and is_browser_incognito():
            time.sleep(1)
            continue

        if not is_user_active():
            time.sleep(3)
            continue

        from datetime import datetime
        #print(f"[{datetime.now().strftime('%H:%M:%S.%f')}] Acquiring screenshot (interval: {screenshot_interval}s)...")
        screenshots = take_screenshots()

        for i, screenshot in enumerate(screenshots):

            last_screenshot = last_screenshots[i]

            if not is_similar(screenshot, last_screenshot):
                #print(f"[{datetime.now().strftime('%H:%M:%S.%f')}] Change detected! Saving screenshot {i}...")
                last_screenshots[i] = screenshot
                
                # 1. Run OCR on full resolution image
                text, words_coords = extract_text_from_image(screenshot)
                
                # 2. Resize and save image (Always save, regardless of text)
                image = Image.fromarray(screenshot)
                width, height = image.size
                
                # Quality Settings
                if screenshot_quality == 'high':
                    # No resize, lossless
                    save_kwargs = {'lossless': True}
                elif screenshot_quality == 'medium':
                    # 95% resize, 95 quality
                    image = image.resize((int(width * 0.95), int(height * 0.95)), Image.LANCZOS)
                    save_kwargs = {'lossless': False, 'quality': 95}
                else: # low
                    # 80% resize, 80 quality (default behavior)
                    image = image.resize((int(width * 0.8), int(height * 0.8)), Image.LANCZOS)
                    save_kwargs = {'lossless': False, 'quality': 80}
                
                timestamp = int(time.time() * 1000000)  # microseconds
                filename = f"{timestamp}.webp"
                
                image.save(
                    os.path.join(screenshots_path, filename),
                    format="webp",
                    **save_kwargs
                )
                
                # 3. Create DB entry (even if text is empty)
                embedding: np.ndarray = get_embedding(text) if text.strip() else np.zeros(384) # Zero embedding if no text
                active_app_name: str = get_active_app_name() or "Unknown App"
                active_window_title: str = get_active_window_title() or "Unknown Title"
                
                insert_entry(
                    text, timestamp, embedding, active_app_name, active_window_title, words_coords
                )

        time.sleep(screenshot_interval) # Wait before taking the next screenshot

