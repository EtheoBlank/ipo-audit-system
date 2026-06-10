"""OCR for uploaded contract images / PDFs.

Strategy:
  1) Always try to extract text directly from PDFs (pdfplumber) — many PDFs
     carry a text layer and OCR is unnecessary.
  2) For images (and scanned PDFs without text) we prefer paddleocr for
     Chinese accuracy. It's lazy-imported so the rest of the app keeps
     working if paddleocr isn't installed.
  3) EasyOCR is the fallback. Tesseract via pytesseract is the last resort.
  4) The user can also paste the OCR text directly via the API; in that case
     we skip OCR entirely.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


class OCRError(RuntimeError):
    """Raised when OCR fails for any reason."""


class ContractOCR:
    """Image / PDF → (engine_name, plain_text)."""

    @staticmethod
    def is_image(filename: str) -> bool:
        ext = Path(filename).suffix.lower()
        return ext in {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}

    @staticmethod
    def is_pdf(filename: str) -> bool:
        return Path(filename).suffix.lower() == ".pdf"

    @classmethod
    def run(cls, file_path: Path, filename: str) -> Tuple[str, str]:
        """Return (engine_name, text). Raises OCRError on failure."""
        # Fast path: PDFs often have a text layer
        if cls.is_pdf(filename):
            try:
                import pdfplumber

                with pdfplumber.open(str(file_path)) as pdf:
                    chunks = [(p.extract_text() or "").strip() for p in pdf.pages]
                text = "\n\n".join(c for c in chunks if c)
                if text and len(text) > 20:
                    return "pdfplumber", text
            except Exception as exc:  # noqa: BLE001
                logger.info("pdfplumber failed for %s: %s", filename, exc)
            # Fall through to OCR if PDF has no text layer

        if not cls.is_image(filename) and not cls.is_pdf(filename):
            raise OCRError(f"不支持的文件类型: {filename}")

        # 1) paddleocr
        try:
            from paddleocr import PaddleOCR  # type: ignore

            ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            result = ocr.ocr(str(file_path), cls=True)
            lines = []
            for page in result or []:
                for det in page or []:
                    if det and len(det) >= 2 and det[1]:
                        lines.append(det[1][0])
            if lines:
                return "paddleocr", "\n".join(lines)
        except ImportError:
            logger.info("paddleocr not installed, falling back")
        except Exception as exc:  # noqa: BLE001
            logger.warning("paddleocr failed for %s: %s", filename, exc)

        # 2) easyocr
        try:
            import easyocr  # type: ignore

            reader = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
            result = reader.readtext(str(file_path), detail=0, paragraph=True)
            if result:
                return "easyocr", "\n".join(result)
        except ImportError:
            logger.info("easyocr not installed, falling back")
        except Exception as exc:  # noqa: BLE001
            logger.warning("easyocr failed for %s: %s", filename, exc)

        # 3) tesseract
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore

            img = Image.open(str(file_path))
            text = pytesseract.image_to_string(img, lang="chi_sim+eng")
            if text.strip():
                return "tesseract", text
        except ImportError:
            logger.info("pytesseract not installed, falling back")
        except Exception as exc:  # noqa: BLE001
            logger.warning("tesseract failed for %s: %s", filename, exc)

        raise OCRError(
            "OCR 失败：未安装任何 OCR 引擎（paddleocr / easyocr / pytesseract）。"
            "请 `uv add paddleocr`（推荐，中文最佳），"
            "或在前端『直接粘贴 OCR 文本』模式上传纯文本。"
        )
