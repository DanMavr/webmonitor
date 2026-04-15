"""
ocr.py — extract clean text from a PNG image using pytesseract + Pillow.

pytesseract wraps the system tesseract-ocr binary and works on 32-bit ARM.

Install:
  sudo apt install tesseract-ocr
  sudo apt install tesseract-ocr-rus   # optional: Russian/Cyrillic
  pip install pytesseract Pillow
"""

import logging
from io import BytesIO
from PIL import Image

logger = logging.getLogger("monitor")

# Map EasyOCR-style language codes → Tesseract language codes.
# sites.yaml does not need to change.
_LANG_MAP = {
    "en":     "eng",
    "ru":     "rus",
    "ch_sim": "chi_sim",
    "ch_tra": "chi_tra",
    "fr":     "fra",
    "de":     "deu",
    "es":     "spa",
    "ja":     "jpn",
    "ko":     "kor",
    "ar":     "ara",
}


def _to_tesseract_lang(languages: list[str]) -> str:
    """Convert a list of EasyOCR-style lang codes to a Tesseract lang string."""
    return "+".join(_LANG_MAP.get(lang, lang) for lang in languages) or "eng"


def extract_text(png_bytes: bytes, languages: list[str], min_confidence: float = 0.4) -> str:
    """
    Run OCR on png_bytes and return recognised text as a whitespace-separated
    string, filtered by confidence threshold.

    min_confidence — words whose Tesseract confidence (0.0-1.0) is below this
                     value are dropped.  0.4 is a sensible default.
    """
    if not png_bytes:
        return ""

    try:
        import pytesseract
        from pytesseract import Output
    except ImportError:
        raise RuntimeError(
            "pytesseract is not installed.\n"
            "Run: sudo apt install tesseract-ocr && pip install pytesseract"
        )

    lang  = _to_tesseract_lang(languages)
    image = Image.open(BytesIO(png_bytes)).convert("RGB")

    try:
        data  = pytesseract.image_to_data(image, lang=lang, output_type=Output.DICT)
        words = []
        for text, conf in zip(data["text"], data["conf"]):
            text = text.strip()
            if not text:
                continue
            try:
                if float(conf) / 100.0 >= min_confidence:
                    words.append(text)
            except (ValueError, TypeError):
                pass
        return " ".join(words)

    except Exception as exc:
        logger.warning(f"Detailed OCR failed ({exc}), falling back to plain string")
        try:
            return pytesseract.image_to_string(image, lang=lang).strip()
        except Exception as exc2:
            logger.error(f"OCR failed: {exc2}")
            return ""
