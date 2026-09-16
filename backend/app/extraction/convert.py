"""
Format conversion via headless LibreOffice.

Turns .doc, .odt, .rtf, and .pages into PDF so the normal pipeline can handle
them. This is what converts an upload rejection into a successful screen.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

CONVERTIBLE = {".doc", ".odt", ".rtf", ".pages", ".wpd", ".txt", ".html", ".htm"}


def available() -> bool:
    return shutil.which(settings.LIBREOFFICE_BIN) is not None


async def to_pdf(source: str, timeout: int = 120) -> str | None:
    """Convert to PDF, returning the new path or None if conversion failed."""
    if not available():
        logger.warning("libreoffice_missing", binary=settings.LIBREOFFICE_BIN)
        return None

    outdir = tempfile.mkdtemp(prefix="resumeiq-convert-")
    process = await asyncio.create_subprocess_exec(
        settings.LIBREOFFICE_BIN,
        "--headless", "--norestore", "--invisible",
        "--convert-to", "pdf", "--outdir", outdir, source,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        logger.error("libreoffice_timeout", source=source)
        return None

    if process.returncode != 0:
        logger.error("libreoffice_failed", source=source, stderr=stderr.decode()[:400])
        return None

    produced = list(Path(outdir).glob("*.pdf"))
    return str(produced[0]) if produced else None
