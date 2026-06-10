"""Contract analysis (收入合同五步法) subpackage.

Exposes:
  ContractOCR       — image/PDF → text  (lazy paddleocr / easyocr / tesseract)
  ContractAnalyzer  — text → 基础 7 字段 + CAS 14 五步法 (via DeepSeek)
"""

from app.services.contract_analysis.ocr import ContractOCR, OCRError
from app.services.contract_analysis.analyzer import ContractAnalyzer

__all__ = ["ContractOCR", "OCRError", "ContractAnalyzer"]
