"""
OCR fallback for scanned documents and images.

Tesseract with OpenCV preprocessing is the baseline. Deskew, denoise, and
adaptive threshold produce meaningful accuracy gains on photographed pages,
which recruiters do receive. A vision-model fallback is available behind a
config flag for layouts Tesseract mangles.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class OcrResult:
    text: str
    page_count: int
    mean_confidence: float
    page_breaks: list[int] = field(default_factory=list)
    engine: str = "tesseract"


def _preprocess(image_bytes: bytes):
    """Deskew, denoise, threshold. Returns a numpy array for Tesseract."""
    import cv2
    import numpy as np

    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError("Could not decode image data")

    image = cv2.fastNlMeansDenoising(image, h=10)

    # Deskew using the minimum-area rectangle of the text mask.
    coords = cv2.findNonZero(cv2.bitwise_not(image))
    if coords is not None and len(coords) > 100:
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = 90 + angle
        if abs(angle) > 0.4:
            h, w = image.shape
            matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
            image = cv2.warpAffine(
                image, matrix, (w, h),
                flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
            )

    return cv2.adaptiveThreshold(
        image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
    )


def run_tesseract(images: list[bytes]) -> OcrResult:
    import pytesseract

    texts: list[str] = []
    breaks: list[int] = []
    confidences: list[float] = []
    cursor = 0

    for index, raw in enumerate(images):
        try:
            processed = _preprocess(raw)
        except Exception as exc:
            logger.warning("ocr_preprocess_failed", page=index + 1, error=str(exc))
            processed = raw  # let Tesseract try the original

        data = pytesseract.image_to_data(
            processed, lang=settings.OCR_LANGUAGES,
            output_type=pytesseract.Output.DICT, config="--psm 3",
        )
        words, page_conf = [], []
        for word, conf in zip(data["text"], data["conf"], strict=False):
            if word.strip():
                words.append(word)
                try:
                    value = float(conf)
                    if value >= 0:
                        page_conf.append(value)
                except (TypeError, ValueError):
                    pass

        page_text = " ".join(words)
        texts.append(page_text)
        confidences.extend(page_conf)
        cursor += len(page_text) + 1
        if index < len(images) - 1:
            breaks.append(cursor)

    mean_conf = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0
    return OcrResult(
        text="\n".join(texts), page_count=len(images),
        mean_confidence=round(mean_conf, 3), page_breaks=breaks,
    )


async def run_vision(images: list[bytes]) -> OcrResult:
    """
    Vision-model OCR for layouts Tesseract cannot handle.

    Off by default — it costs materially more per page. Enable with
    VISION_OCR_FALLBACK=true when Tesseract confidence is consistently low.
    """
    import base64

    from app.ai.client import get_client

    client = get_client()
    if not client.enabled:
        raise RuntimeError("Vision OCR requires ANTHROPIC_API_KEY")

    anthropic = client._ensure()
    texts: list[str] = []

    for image in images[: settings.MAX_RESUME_PAGES]:
        encoded = base64.standard_b64encode(image).decode()
        response = await anthropic.messages.create(
            model=settings.ANTHROPIC_EXTRACTION_MODEL,
            max_tokens=4096,
            system=(
                "Transcribe all text from this document image exactly as it "
                "appears, preserving reading order. For multi-column layouts, "
                "read each column fully before moving to the next. Output only "
                "the transcribed text."
            ),
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/png", "data": encoded}},
                    {"type": "text", "text": "Transcribe this page."},
                ],
            }],
        )
        texts.append("".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ))

    return OcrResult(
        text="\n".join(texts), page_count=len(texts),
        mean_confidence=0.85, engine="vision",
    )
