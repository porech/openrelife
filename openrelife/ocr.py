"""OCR dispatcher.

Public API: extract_text_from_image(image: np.ndarray, use_apple_vision: bool) -> (str, List[Dict])
Dispatches to Apple Vision when enabled and available, with automatic doctr fallback.

doctr is lazy-loaded: the predictor is constructed on first call. On systems
where Vision works without errors, doctr is never instantiated.
"""
import logging
from typing import Dict, List, Tuple

import numpy as np

from openrelife import apple_vision_ocr

_logger = logging.getLogger("openrelife.ocr")
_doctr_predictor = None


def _get_doctr_predictor():
    """Lazy-load the doctr OCR predictor. Cached after first call."""
    global _doctr_predictor
    if _doctr_predictor is None:
        from doctr.models import ocr_predictor
        _doctr_predictor = ocr_predictor(
            pretrained=True,
            det_arch="db_mobilenet_v3_large",
            reco_arch="crnn_mobilenet_v3_large",
        )
    return _doctr_predictor


def _extract_with_doctr(image: np.ndarray) -> Tuple[str, List[Dict]]:
    """OCR via doctr (PyTorch CPU). Returns (text, words_with_coords)."""
    predictor = _get_doctr_predictor()
    result = predictor([image])
    text = ""
    words_with_coords: List[Dict] = []
    for page in result.pages:
        for block in page.blocks:
            for line in block.lines:
                for word in line.words:
                    text += word.value + " "
                    x1, y1 = word.geometry[0]
                    x2, y2 = word.geometry[1]
                    words_with_coords.append({
                        "text": word.value,
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                    })
                text += "\n"
            text += "\n"
    return text, words_with_coords


def _extract_with_vision(image: np.ndarray) -> Tuple[str, List[Dict]]:
    """OCR via Apple Vision framework. Returns (text, words_with_coords)."""
    return apple_vision_ocr.extract_text_with_vision(image)


def extract_text_from_image(image: np.ndarray,
                            use_apple_vision: bool = False) -> Tuple[str, List[Dict]]:
    """Run OCR on an RGB image and return (text, words_with_coords).

    If `use_apple_vision` is True AND Apple Vision is available on this
    platform, try Vision first and fall back to doctr on any exception.
    Otherwise, use doctr directly.
    """
    if use_apple_vision and apple_vision_ocr.is_apple_vision_available():
        try:
            return _extract_with_vision(image)
        except Exception as e:
            _logger.warning("Vision OCR failed (%s), falling back to doctr", e)
    return _extract_with_doctr(image)
