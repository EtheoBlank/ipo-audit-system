"""Inventory (收发存/盘点/跌价) sub-package.

Public surface:
- ``InventoryImporter``   — parse 收发存 Excel into normalized DataFrame
- ``CountSheetBuilder``   — generate 盘点用表 (amount-first + threshold coverage)
- ``CountPlanGenerator``  — industry-aware 盘点计划 with AI dialog revision
- ``InventoryAgingEngine``— FIFO aging + NRV impairment + reversal
- ``CountPhotoProcessor`` — photo OCR → AI parse → back-fill counted_qty
- ``InventoryExporter``   — write multi-sheet workbook
"""

from app.services.inventory.importer import InventoryImporter, InventoryImportError
from app.services.inventory.count_sheet import CountSheetBuilder, CountSheetStrategy
from app.services.inventory.count_plan import CountPlanGenerator
from app.services.inventory.aging_engine import InventoryAgingEngine
from app.services.inventory.photo_processor import CountPhotoProcessor
from app.services.inventory.excel_exporter import InventoryExporter

__all__ = [
    "InventoryImporter",
    "InventoryImportError",
    "CountSheetBuilder",
    "CountSheetStrategy",
    "CountPlanGenerator",
    "InventoryAgingEngine",
    "CountPhotoProcessor",
    "InventoryExporter",
]
