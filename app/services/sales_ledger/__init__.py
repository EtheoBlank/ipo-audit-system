"""Sales Ledger (销售清单整理) subpackage.

Exports the public surface used by the API layer and the Streamlit frontend.
The API key is read from settings.DEEPSEEK_API_KEY — never hard-coded.
"""

from app.services.sales_ledger.deepseek_client import DeepSeekClient
from app.services.sales_ledger.document_parser import DocumentParser, DocumentParserError
from app.services.sales_ledger.synthesizer import SalesLedgerSynthesizer
from app.services.sales_ledger.analyzer import RevenueAnalyzer
from app.services.sales_ledger.excel_exporter import SalesLedgerExporter

__all__ = [
    "DeepSeekClient",
    "DocumentParser",
    "DocumentParserError",
    "SalesLedgerSynthesizer",
    "RevenueAnalyzer",
    "SalesLedgerExporter",
]
