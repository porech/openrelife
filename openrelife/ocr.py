"""OCR dispatcher.

Public API: extract_text_from_image(image: np.ndarray) -> (str, List[Dict])
Currently dispatches to doctr only. Vision wiring is added in a later task.

doctr is lazy-loaded: the predictor is constructed on first call.
"""
from typing import Dict, List, Tuple

import numpy as np

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


def extract_text_from_image(image: np.ndarray) -> Tuple[str, List[Dict]]:
    """Run OCR on an RGB image and return (text, words_with_coords).

    words_with_coords: list of {text, x1, y1, x2, y2} with normalized
    coordinates in [0, 1], top-left origin.
    """
    return _extract_with_doctr(image)
