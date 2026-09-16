"""Dispatch a document to the right extractor and apply the quality gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.extraction import convert, ocr
from app.extraction import docx as docx_mod
from app.extraction import pdf as pdf_mod
from app.extraction.quality import QualityReport, QualityVerdict, assess

logger = get_logger(__name__)

PDF_TYPES = {".pdf"}
DOCX_TYPES = {".docx"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}


@dataclass(slots=True)
class ExtractionResult:
    text: str
    page_count: int
    page_breaks: list[int]
    method: str
    quality: QualityReport
    hidden_text: list[dict] = field(default_factory=list)
    is_multi_column: bool = False

    @property
    def quarantined(self) -> bool:
        return not self.quality.passable


async def extract_document(path: str, filename: str) -> ExtractionResult:
    """
    Extract text, escalating to OCR when the text layer is unusable.

    Never raises for a bad document — returns a result whose quality report says
    it needs review. Silent failure is the one outcome we refuse.
    """
    suffix = Path(filename).suffix.lower()
    working = path
    method = "unknown"

    if suffix in convert.CONVERTIBLE:
        converted = await convert.to_pdf(path)
        if converted:
            working, suffix, method = converted, ".pdf", "libreoffice"
        else:
            return ExtractionResult(
                text="", page_count=0, page_breaks=[], method="failed",
                quality=QualityReport(
                    verdict=QualityVerdict.UNREADABLE, confidence=0.0,
                    reason=(
                        f"{suffix} files need converting before they can be read, "
                        "and conversion is unavailable on this server. Save the "
                        "document as PDF or DOCX and upload it again."
                    ),
                ),
            )

    if suffix in PDF_TYPES:
        result = pdf_mod.extract(working)
        quality = assess(result.text, result.page_count,
                         has_text_layer=result.has_text_layer)

        if quality.verdict is QualityVerdict.OCR_REQUIRED and settings.OCR_ENABLED:
            logger.info("escalating_to_ocr", filename=filename)
            try:
                images = pdf_mod.rasterise(working, settings.OCR_DPI)
                if settings.VISION_OCR_FALLBACK:
                    ocr_result = await ocr.run_vision(images)
                else:
                    ocr_result = ocr.run_tesseract(images)
                quality = assess(ocr_result.text, ocr_result.page_count)
                return ExtractionResult(
                    text=ocr_result.text, page_count=ocr_result.page_count,
                    page_breaks=ocr_result.page_breaks,
                    method=f"ocr_{ocr_result.engine}", quality=quality,
                )
            except Exception as exc:
                logger.error("ocr_failed", filename=filename, error=str(exc))
                quality = QualityReport(
                    verdict=QualityVerdict.NEEDS_REVIEW, confidence=0.0,
                    reason=("This looks like a scanned document and OCR could not "
                            "read it. Open the original to review it manually."),
                )

        return ExtractionResult(
            text=result.text, page_count=result.page_count,
            page_breaks=result.page_breaks,
            method=method if method != "unknown" else "pymupdf",
            quality=quality, hidden_text=result.hidden_text,
            is_multi_column=result.is_multi_column,
        )

    if suffix in DOCX_TYPES:
        result = docx_mod.extract(working)
        return ExtractionResult(
            text=result.text, page_count=result.page_count,
            page_breaks=result.page_breaks, method="python_docx",
            quality=assess(result.text, result.page_count,
                           has_text_layer=result.has_text_layer),
        )

    if suffix in IMAGE_TYPES and settings.OCR_ENABLED:
        with open(working, "rb") as handle:
            ocr_result = ocr.run_tesseract([handle.read()])
        return ExtractionResult(
            text=ocr_result.text, page_count=1, page_breaks=[],
            method="ocr_tesseract", quality=assess(ocr_result.text, 1),
        )

    return ExtractionResult(
        text="", page_count=0, page_breaks=[], method="unsupported",
        quality=QualityReport(
            verdict=QualityVerdict.UNREADABLE, confidence=0.0,
            reason=f"{suffix or 'This file type'} is not supported. Upload a PDF or DOCX.",
        ),
    )
