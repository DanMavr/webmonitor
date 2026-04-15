"""
ocr.py — extract clean text from a PNG image using EasyOCR.

EasyOCR handles:
  - Standard Latin text (English)
  - Cyrillic script (Mongolian, Russian) via the "ru" language model
  - Returns confidence scores so low-confidence noise can be filtered

Install:
  pip install easyocr
  (Downloads ~500MB model on first use — cached in ~/.EasyOCR/)
"""

import logging
from io import BytesIO
from PIL import Image

logger = logging.getLogger("monitor")

# Module-level reader cache — EasyOCR initialisation is slow (~5s),
# so we keep one reader per language set alive for the process lifetime.
_readers: dict[tuple, object] = {}


def _get_reader(languages: list[str]):
    key = tuple(sorted(languages))
    if key not in _readers:
        try:
            import easyocr
            logger.info(f"Initialising EasyOCR for languages: {languages}")
            _readers[key] = easyocr.Reader(languages, gpu=False)
        except ImportError:
            raise RuntimeError(
                "easyocr is not installed. Run: pip install easyocr"
            )
    return _readers[key]


def extract_text(png_bytes: bytes, languages: list[str], min_confidence: float = 0.4) -> str:
    """
    Run OCR on png_bytes and return a clean newline-separated string of
    recognised text, filtered by confidence threshold.

    min_confidence — drop any OCR result below this score (0.0–1.0).
                     0.4 is a reasonable default; lower = more noise,
                     higher = may miss small/blurry text.
    """
    if not png_bytes:
        return ""

    reader = _get_reader(languages)

    try:
        image = Image.open(BytesIO(png_bytes)).convert("RGB")
        results = reader.readtext(image, detail=1, paragraph=False)
    except Exception as e:
        logger.error(f"OCR failed: {e}")
        return ""

    lines = []
    for (_, text, confidence) in results:
        text = text.strip()
        if text and confidence >= min_confidence:
            lines.append(text)

    return "\n".join(lines)
