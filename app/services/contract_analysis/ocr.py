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

    # P1 性能 (2026-06-19): OCR 引擎模块级单例, 避免每次调用都冷启动
    # paddleocr / easyocr 初始化耗时 5-15s, 多文件并发上传瓶颈
    _paddle_ocr_cache: dict = {}
    _easyocr_reader_cache: dict = {}

    @staticmethod
    def is_image(filename: str) -> bool:
        ext = Path(filename).suffix.lower()
        return ext in {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}

    @staticmethod
    def is_pdf(filename: str) -> bool:
        return Path(filename).suffix.lower() == ".pdf"

    @classmethod
    def run(cls, file_path: Path, filename: str, allowed_base: Path | None = None) -> Tuple[str, str]:
        """Return (engine_name, text). Raises OCRError on failure.

        P0 安全修复: 若传入 allowed_base, 校验 file_path 在 allowed_base 内,
        防攻击者通过 file_path='/etc/passwd' 之类读取任意文件 (虽 OCR 不会泄露内容,
        但读取行为本身违反最小权限)。
        """
        file_path = Path(file_path)
        # P2 修复 (2026-06-19): file_path.resolve() 在 Windows 不存在路径上抛
        # FileNotFoundError (WinError 3), 之前裸抛到 500. 现兜底 OCRError
        try:
            file_path = file_path.resolve()
        except (OSError, ValueError) as exc:
            raise OCRError(f"无法解析文件路径: {file_path} ({exc})") from exc
        if allowed_base is not None:
            allowed_resolved = Path(allowed_base).resolve()
            target_resolved = file_path  # 上面已 resolve 过
            if not target_resolved.is_relative_to(allowed_resolved):
                raise OCRError(f"file_path 不在允许目录内: {file_path}")
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
                # round 36 P1: info 留不下 traceback, 改 exception
                logger.exception("pdfplumber failed for %s: %s", filename, exc)
            # Fall through to OCR if PDF has no text layer

        if not cls.is_image(filename) and not cls.is_pdf(filename):
            raise OCRError(f"不支持的文件类型: {filename}")

        # 1) paddleocr
        try:
            from paddleocr import PaddleOCR  # type: ignore

            cache_key = ("paddleocr", "ch")
            if cache_key not in cls._paddle_ocr_cache:
                cls._paddle_ocr_cache[cache_key] = PaddleOCR(
                    use_angle_cls=True, lang="ch", show_log=False
                )
            ocr = cls._paddle_ocr_cache[cache_key]
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
            # round 36 P1: warning 留不下 traceback, 改 exception
            logger.exception("paddleocr failed for %s: %s", filename, exc)

        # 2) easyocr
        try:
            import easyocr  # type: ignore

            cache_key = ("easyocr", "ch_sim+en")
            if cache_key not in cls._easyocr_reader_cache:
                cls._easyocr_reader_cache[cache_key] = easyocr.Reader(
                    ["ch_sim", "en"], gpu=False, verbose=False
                )
            reader = cls._easyocr_reader_cache[cache_key]
            result = reader.readtext(str(file_path), detail=0, paragraph=True)
            if result:
                return "easyocr", "\n".join(result)
        except ImportError:
            logger.info("easyocr not installed, falling back")
        except Exception as exc:  # noqa: BLE001
            # round 36 P1: warning 留不下 traceback, 改 exception
            logger.exception("easyocr failed for %s: %s", filename, exc)

        # 3) tesseract
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore

            with Image.open(str(file_path)) as img:
                text = pytesseract.image_to_string(img, lang="chi_sim+eng")
            if text.strip():
                return "tesseract", text
        except ImportError:
            logger.info("pytesseract not installed, falling back")
        except Exception as exc:  # noqa: BLE001
            # round 36 P1: warning 留不下 traceback, 改 exception
            logger.exception("tesseract failed for %s: %s", filename, exc)

        raise OCRError(
            "OCR 失败：未安装任何 OCR 引擎（paddleocr / easyocr / pytesseract）。"
            "请 `uv add paddleocr`（推荐，中文最佳），"
            "或在前端『直接粘贴 OCR 文本』模式上传纯文本。"
        )
