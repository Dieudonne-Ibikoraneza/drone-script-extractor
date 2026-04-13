#!/usr/bin/env python3
"""
Unified PDF Report Extractor v1.0
Supports: Plant Stress, Flowering, Waterlogging, Pest, Stand Count — and any
future Agremo-style report that follows the same visual layout.

Architecture:
  1.  Multi-strategy text extraction (pdfplumber → fitz blocks → OCR) so we
      handle native PDFs *and* browser-printed / rasterised PDFs.
  2.  Keyword-based automatic type detection determines which analysis branch
      to run.
  3.  Agremo-style reports (all except Stand Count) share a common field
      layout — the same parsers work across types.
  4.  Level extraction is data-driven via a config dict: adding a new Agremo
      type only requires a new entry in ReportTypeConfig.TYPES.
"""

import fitz
import re
import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

import cloudinary
import cloudinary.uploader
from cloudinary.exceptions import Error as CloudinaryError
from app.config import settings

# Optional heavy deps — graceful degradation
try:
    import pdfplumber as _pdfplumber_module
    _PDFPLUMBER_AVAILABLE = True
except ImportError:
    _PDFPLUMBER_AVAILABLE = False

try:
    import pytesseract
    from PIL import Image as _PILImage
    _OCR_AVAILABLE = True
    
    # Windows Tesseract detection
    if os.name == 'nt':
        _TESS_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.exists(_TESS_PATH):
            # Set both to be safe
            pytesseract.pytesseract.tesseract_cmd = str(_TESS_PATH)
            if hasattr(pytesseract, 'tesseract_cmd'):
                pytesseract.tesseract_cmd = str(_TESS_PATH)
            logging.info(f"Using Tesseract at: {_TESS_PATH}")
        else:
            logging.warning(f"Tesseract not found at default path: {_TESS_PATH}")
except ImportError:
    _OCR_AVAILABLE = False
except Exception as e:
    logging.warning(f"Error initializing Tesseract: {e}")
    _OCR_AVAILABLE = False

try:
    from pdf2image import convert_from_path
    _PDF2IMAGE_AVAILABLE = True
except ImportError:
    _PDF2IMAGE_AVAILABLE = False

# Cloudinary init
if settings.cloudinary_cloud_name and settings.cloudinary_api_key and settings.cloudinary_api_secret:
    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )
else:
    print("Warning: Cloudinary credentials not configured in settings")

logger = logging.getLogger(__name__)

ACRES_TO_HA = 0.404686
_MIN_TEXT_LEN = 80


# ---------------------------------------------------------------------------
# Report type registry
# ---------------------------------------------------------------------------

class ReportTypeConfig:
    """
    Central registry of every supported report type.

    To add a *new* Agremo-style type (e.g. "weed_pressure"):
      1. Add an entry in TYPES with keywords, total_area_patterns, and levels.
      2. Done — the generic parsers handle the rest.

    Each level supports two regex variants:
      • pattern_pct_first  — "Label  45.2%  12.8"   (percentage then area)
      • pattern_area_first — "Label  12.8  45.2%"   (area then percentage)
    The extractor tries both and takes whichever matches first.
    """

    TYPES: Dict[str, Dict[str, Any]] = {
        # ── Agremo-style reports ─────────────────────────────────────────
        "plant_stress": {
            "keywords": ["PLANT STRESS", "Plant Stress"],
            "total_area_patterns": [
                "total area plant stress",
            ],
            "levels": [
                {
                    "name": "Fine",
                    "severity": "healthy",
                    "pattern_pct_first":  r"\bFine\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bFine\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "Potential Plant Stress",
                    "severity": "moderate",
                    "pattern_pct_first":  r"\bPotential\s+Plant\s+(?:Stress\s+)?([\d.]+)\s*%\s+([\d.]+)(?:\s*Stress)?",
                    "pattern_area_first": r"\bPotential\s+Plant\s+(?:Stress\s+)?([\d.]+)\s+([\d.]+)\s*%(?:\s*Stress)?",
                },
                {
                    "name": "Plant Stress",
                    "severity": "high",
                    "pattern_pct_first":  r"(?<!Potential )\bPlant\s+Stress\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"(?<!Potential )\bPlant\s+Stress\s+([\d.]+)\s+([\d.]+)\s*%",
                },
            ],
            "field_name": "analysis",
        },
        "flowering": {
            "keywords": ["FLOWERING", "Flowering"],
            "total_area_patterns": [
                "total area flowering",
            ],
            "levels": [
                {
                    "name": "Full Flowering",
                    "severity": "high",
                    "pattern_pct_first":  r"\bFull\s+Flowering\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bFull\s+Flowering\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "Flowering",
                    "severity": "moderate",
                    "pattern_pct_first":  r"(?<!Full )(?<!No )\bFlowering\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"(?<!Full )(?<!No )\bFlowering\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "No Flowering",
                    "severity": "low",
                    "pattern_pct_first":  r"\bNo\s+Flowering\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bNo\s+Flowering\s+([\d.]+)\s+([\d.]+)\s*%",
                },
            ],
            "field_name": "analysis",
        },
        "waterlogging": {
            "keywords": [
                "WATERLOGGING", "Waterlogging", "WATER STRESS", "Water Stress",
                "FLOODING", "Flooding",
            ],
            "total_area_patterns": [
                "total waterlogging area is",
                "total waterlogging area",
                "total area waterlogging",
                "total area water stress",
                "total area water",
            ],
            "levels": [
                {
                    "name": "Not Waterlogged",
                    "severity": "none",
                    "pattern_pct_first":  r"\bNot\s+[Ww]aterlogged\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bNot\s+[Ww]aterlogged\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "No Waterlogging",
                    "severity": "none",
                    "pattern_pct_first":  r"\bNo\s+Waterlogging\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bNo\s+Waterlogging\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "Wet Zone",
                    "severity": "low",
                    "pattern_pct_first":  r"\bWet\s+[Zz]one\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bWet\s+[Zz]one\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "Waterlogged Zone",
                    "severity": "high",
                    "pattern_pct_first":  r"\bWaterlogged\s+[Zz]one\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bWaterlogged\s+[Zz]one\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "Low Waterlogging",
                    "severity": "low",
                    "pattern_pct_first":  r"\bLow\s+Waterlogging\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bLow\s+Waterlogging\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "Moderate Waterlogging",
                    "severity": "moderate",
                    "pattern_pct_first":  r"\bModerate\s+Waterlogging\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bModerate\s+Waterlogging\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "High Waterlogging",
                    "severity": "high",
                    "pattern_pct_first":  r"\bHigh\s+Waterlogging\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bHigh\s+Waterlogging\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "Severe Waterlogging",
                    "severity": "severe",
                    "pattern_pct_first":  r"\bSevere\s+Waterlogging\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bSevere\s+Waterlogging\s+([\d.]+)\s+([\d.]+)\s*%",
                },
            ],
            "field_name": "analysis",
        },
        "pest": {
            "keywords": [
                "PEST STRESS", "Pest Stress",
                "PEST DAMAGE", "Pest Damage", "PEST ANALYSIS", "Pest Analysis",
                "INSECT", "Insect",
            ],
            "total_area_patterns": [
                "total area pest stress",
                "total area pest",
            ],
            "levels": [
                {
                    "name": "Fine",
                    "severity": "healthy",
                    "pattern_pct_first":  r"\bFine\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bFine\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "Potential Pest Stress",
                    "severity": "moderate",
                    "pattern_pct_first":  r"\bPotential\s+Pest(?:\s+Stress)?\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bPotential\s+Pest(?:\s+Stress)?\s+([\d.]+)\s+([\d.]+)\s*%",
                    "pattern_any": r"\bPotential\s+Pest.*?\b([\d.]+)\s*%.*?\b([\d.]+)\b", # New permissive pattern
                },
                {
                    "name": "Pest Stress",
                    "severity": "high",
                    "pattern_pct_first":  r"(?<!Potential )\bPest(?:\s+Stress)?\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"(?<!Potential )\bPest(?:\s+Stress)?\s+([\d.]+)\s+([\d.]+)\s*%",
                    "pattern_any": r"(?<!Potential )\bPest.*?\b([\d.]+)\s*%.*?\b([\d.]+)\b", # New permissive pattern
                },
                {
                    "name": "No Damage",
                    "severity": "none",
                    "pattern_pct_first":  r"\bNo\s+Damage\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bNo\s+Damage\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "Low Damage",
                    "severity": "low",
                    "pattern_pct_first":  r"\bLow\s+Damage\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bLow\s+Damage\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "Moderate Damage",
                    "severity": "moderate",
                    "pattern_pct_first":  r"\bModerate\s+Damage\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bModerate\s+Damage\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "High Damage",
                    "severity": "high",
                    "pattern_pct_first":  r"\bHigh\s+Damage\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bHigh\s+Damage\s+([\d.]+)\s+([\d.]+)\s*%",
                },
                {
                    "name": "Severe Damage",
                    "severity": "severe",
                    "pattern_pct_first":  r"\bSevere\s+Damage\s+([\d.]+)\s*%\s+([\d.]+)\b",
                    "pattern_area_first": r"\bSevere\s+Damage\s+([\d.]+)\s+([\d.]+)\s*%",
                },
            ],
            "field_name": "analysis",
        },
        # ── Stand Count (different layout) ───────────────────────────────
        "stand_count": {
            "keywords": [
                "STAND COUNT", "Stand Count", "STAND COUNT REPORT",
                "PLANT COUNTING", "Plant Counting", "Plants Counted",
            ],
            "total_area_patterns": [],
            "levels": [],
            "field_name": "stand_count_analysis",
        },
        # ── Supplementary reports ────────────────────────────────────────
        "rx_spraying": {
            "keywords": ["RX - SPRAYING ZONE MANAGEMENT", "RX - SPRAYING", "RX_SPRAYING"],
            "total_area_patterns": [],
            "levels": [],
            "field_name": "rx_spraying_analysis",
        },
        "zonation": {
            "keywords": [
                "ZONAL STATISTICS ZONE MANAGEMENT", 
                "ESTADÍSTICAS ZONALES GESTIÓN DE ZONAS", 
                "ESTADÍSTICAS ZONALES"
            ],
            "total_area_patterns": [],
            "levels": [],
            "field_name": "zonation_analysis",
        },
    }


# ---------------------------------------------------------------------------
# Multi-strategy text extraction
# ---------------------------------------------------------------------------

def _is_acres(text: str) -> bool:
    """Detect whether the report uses acres (vs hectares)."""
    t = text.lower()
    acre_count = len(re.findall(r"\bacres?\b|\bac\b", t))
    ha_count = len(re.findall(r"\bhectares?\b|\bha\b", t))
    return acre_count > ha_count


def _ocr_page(pdf_path: str, page_num: int) -> str:
    """OCR a page using high-resolution rendering (preferring pdf2image/poppler)."""
    if not _OCR_AVAILABLE:
        logger.warning("pytesseract not available — cannot OCR")
        return ""
        
    try:
        # Strategy A: pdf2image (best quality, requires Poppler)
        if _PDF2IMAGE_AVAILABLE:
            try:
                images = convert_from_path(
                    pdf_path, 
                    first_page=page_num + 1, 
                    last_page=page_num + 1, 
                    dpi=300
                )
                if images:
                    img = images[0]
                    text = pytesseract.image_to_string(img, config="--psm 6")
                    logger.info(f"OCR page {page_num} (pdf2image): recovered {len(text)} chars")
                    return text
            except Exception as e:
                logger.debug(f"pdf2image failed, falling back to pdfplumber: {e}")

        # Strategy B: pdfplumber fallback
        if _PDFPLUMBER_AVAILABLE:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                if page_num < len(pdf.pages):
                    page = pdf.pages[page_num]
                    img = page.to_image(resolution=300) # Increased resolution
                    text = pytesseract.image_to_string(img.original, config="--psm 6")
                    logger.info(f"OCR page {page_num} (pdfplumber): recovered {len(text)} chars")
                    return text
                    
    except Exception as e:
        logger.warning(f"OCR failed on page {page_num}: {e}")
        
    return ""


def _build_text_from_page(
    pdf_path: str, fitz_doc, page_num: int
) -> Tuple[str, str]:
    """
    Extract text from a page using multiple strategies, returning the first
    that yields >= _MIN_TEXT_LEN characters.

    Returns (newline_text, spaced_text).

    Strategies (in order):
      1. pdfplumber extract_text — best for native PDFs with clean text layers.
      2. pdfplumber extract_words — fallback when extract_text is sparse.
      3. fitz blocks — safety net for PDF types where fitz works better.
      4. OCR via pdfplumber rendering + pytesseract — for rasterised PDFs.
    """
    # Strategy 1: pdfplumber extract_text
    if _PDFPLUMBER_AVAILABLE:
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                if page_num < len(pdf.pages):
                    text = pdf.pages[page_num].extract_text() or ""
                    if len(text.strip()) >= _MIN_TEXT_LEN:
                        logger.debug(f"Page {page_num}: pdfplumber extract_text → {len(text)} chars")
                        spaced = re.sub(r"\s+", " ", text)
                        return text, spaced
        except Exception as e:
            logger.warning(f"pdfplumber extract_text failed on page {page_num}: {e}")

    # Strategy 2: pdfplumber extract_words
    if _PDFPLUMBER_AVAILABLE:
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                if page_num < len(pdf.pages):
                    words = pdf.pages[page_num].extract_words()
                    if words:
                        joined = " ".join(w["text"] for w in words)
                        if len(joined.strip()) >= _MIN_TEXT_LEN:
                            logger.debug(f"Page {page_num}: pdfplumber extract_words → {len(joined)} chars")
                            return joined, joined
        except Exception as e:
            logger.warning(f"pdfplumber extract_words failed on page {page_num}: {e}")

    # Strategy 3: fitz blocks
    try:
        page = fitz_doc[page_num]
        blocks = page.get_text("blocks")
        block_lines = [b[4].strip() for b in blocks if len(b[4].strip()) > 3]
        joined_blocks = " ".join(block_lines)
        if len(joined_blocks.strip()) >= _MIN_TEXT_LEN:
            logger.debug(f"Page {page_num}: fitz blocks → {len(joined_blocks)} chars")
            return "\n".join(block_lines), joined_blocks
    except Exception as e:
        logger.warning(f"fitz blocks failed on page {page_num}: {e}")

    # Strategy 4: OCR
    logger.info(f"Page {page_num}: no text layer, attempting OCR")
    ocr_text = _ocr_page(pdf_path, page_num)
    if ocr_text.strip():
        spaced = re.sub(r"\s+", " ", ocr_text)
        return ocr_text, spaced

    logger.warning(f"Page {page_num}: all extraction strategies failed")
    return "", ""


# ---------------------------------------------------------------------------
# Unified extractor
# ---------------------------------------------------------------------------

class UnifiedReportExtractor:
    """
    Extracts structured data from Agremo drone / loss-assessment PDF reports.

    Automatically detects the report type and applies the appropriate parsing
    branch. For Agremo-style reports (plant stress, flowering, waterlogging,
    pest), the same generic parsers are used — only the level config differs.
    Stand Count has a dedicated branch because its layout is fundamentally
    different.
    """

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.doc = fitz.open(pdf_path)
        self.report_type: Optional[str] = None
        self.type_config: Optional[Dict[str, Any]] = None
        self._uses_acres: bool = False
        self.result = self._init_result()

    # ── Result structure ─────────────────────────────────────────────────

    def _init_result(self) -> Dict[str, Any]:
        return {
            "metadata": {
                "source_file": os.path.basename(self.pdf_path),
                "extracted_at": datetime.now().isoformat(),
                "total_pages": len(self.doc),
                "extractor_version": "1.0-unified",
            },
            "report": {
                "provider": "STARHAWK",
                "type": None,
                "survey_date": None,
                "analysis_name": None,
                "detected_report_type": None,
            },
            "field": {
                "crop": None,
                "growing_stage": None,
                "area_hectares": None,
                "area_acres": None,
            },
            "analysis": {
                "total_area_hectares": None,
                "total_area_acres": None,
                "total_area_percent": None,
                "levels": [],
            },
            "stand_count_analysis": {
                "plants_counted": None,
                "average_plant_density": None,
                "plant_density_unit": None,
                "planned_plants": None,
                "difference_percent": None,
                "difference_type": None,
                "difference_plants": None,
            },
            "rx_spraying_analysis": {
                "planned_date": None,
                "pesticide_type": None,
                "total_pesticide_amount": None,
                "average_pesticide_amount": None,
                "rates": [],
            },
            "zonation_analysis": {
                "tile_size": None,
                "num_zones": None,
                "zones": [],
            },
            "additional_info": None,
            "map_image": {
                "source": None,
                "url": None,
                "public_id": None,
                "width": None,
                "height": None,
                "format": None,
                "bytes": None,
                "error": None,
            },
        }

    # ── Type detection ───────────────────────────────────────────────────

    def _detect_report_type(self, text: str) -> Tuple[Optional[str], Optional[Dict]]:
        """
        Auto-detect report type from text content and/or filename.
        Checks keywords in priority order: stand_count first (most unique
        keywords), then the rest.
        """
        text_upper = text.upper()
        filename_upper = os.path.basename(self.pdf_path).upper()

        # Check stand_count first — its keywords are the most distinctive
        priority_order = ["rx_spraying", "zonation", "stand_count", "plant_stress", "flowering", "pest", "waterlogging"]

        for type_key in priority_order:
            config = ReportTypeConfig.TYPES[type_key]
            for keyword in config["keywords"]:
                if keyword.upper() in text_upper or keyword.upper() in filename_upper:
                    logger.info(f"Detected report type: {type_key} (keyword: '{keyword}')")
                    return type_key, config

        # Filename-based fallback
        for type_key in priority_order:
            simple_name = type_key.upper().replace("_", " ")
            if simple_name in filename_upper or type_key.upper() in filename_upper:
                logger.info(f"Detected report type from filename: {type_key}")
                return type_key, ReportTypeConfig.TYPES[type_key]

        logger.warning("Could not detect report type")
        return None, None

    # ── Field parsers (shared across all Agremo types) ───────────────────

    def _extract_survey_date(self, text: str) -> None:
        patterns = [
            r"Survey\s+date\s*:\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
            r"Survey\s+date\s*:\s*(\d{4}-\d{2}-\d{2})",
            r"Survey\s+date[:\s]+(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
            r"(\d{2}-\d{2}-\d{4})",
            r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if not m:
                continue
            candidate = m.group(1)
            parts = re.split(r"[-/]", candidate)
            if len(parts) == 3:
                year_part = parts[2] if len(parts[2]) == 4 else parts[0]
                try:
                    if int(year_part) >= 2015:
                        self.result["report"]["survey_date"] = candidate
                        return
                except ValueError:
                    pass

    def _extract_report_type_label(self, text: str) -> None:
        """Extract the human-readable report type label."""
        type_labels = [
            "Crop Monitoring", "Plant Health Monitoring", "Loss Assessment",
            "Drone Analysis", "Field Analysis",
        ]
        for label in type_labels:
            if label in text:
                self.result["report"]["type"] = label
                return
        # Fallback: derive from detected type
        if self.report_type:
            self.result["report"]["type"] = self.report_type.replace("_", " ").title()

    def _extract_analysis_name(self, text_spaced: str, text_raw: str) -> None:
        m = re.search(
            r"Analysis\s+name\s*:\s*([\w][\w\s.\-]*?)"
            r"(?=\s{2,}|\s+(?:Field\s+area|Crop\s*:|Growing\s+stage|Total\s+area"
            r"|Additional\s+[Ii]nformation|STRESS\s+LEVEL)|$)",
            text_spaced, re.I,
        )
        if m:
            name = m.group(1).strip()
            name = re.sub(
                r"\s+(Field\s+area|Crop\s*:|Growing|Total|Additional|STRESS\s+LEVEL).*$",
                "", name, flags=re.I,
            ).strip()
            if 2 < len(name) < 80 and "STRESS LEVEL" not in name.upper():
                self.result["report"]["analysis_name"] = name
                return

        # Fallback: value before metadata labels (Analysis name is often above them)
        # We look for a single line [^\n] to avoid capturing multiple blocks
        m = re.search(r"([^\n]{2,40}?)\s+(?:\d+\s+days?\s+)?Growing\s+stage\s*:", text_raw, re.I)
        if m:
            name = m.group(1).strip()
            # Ensure it doesn't accidentally capture other metadata labels
            if "ZONE MANAGEMENT" not in name.upper() and not re.search(r"\b(?:Crop|Field\s+area|Analysis\s+name)\b", name, re.I):
                self.result["report"]["analysis_name"] = name
                return

        # Fallback: use type keyword if we found one
        if self.report_type:
            self.result["report"]["analysis_name"] = self.report_type.replace("_", " ").title()
            return
            
        # Fallback: use type keyword from the text
        if self.type_config:
            for kw in self.type_config["keywords"]:
                if kw in text_raw and len(kw) > 3:
                    self.result["report"]["analysis_name"] = kw
                    break

    def _extract_crop(self, text: str) -> None:
        # Primary: "Crop: value"
        m = re.search(
            r"\bCrop\s*:\s*([a-zA-Z][a-zA-Z\s]{0,25}?)"
            r"(?=\s*\d|\s{2,}|\s+(?:Field|Growing|Total|Analysis|Additional|STRESS|Powered|PLANT)|$)",
            text, re.I,
        )
        if m:
            crop = m.group(1).strip()
            excluded = {
                "total", "area", "stress", "field", "growing", "stage", "analysis",
                "name", "plant", "damage", "pest", "waterlogging", "monitoring",
                "detection", "health", "crop", "not", "wet", "zone", "level", "table",
                "flowering", "count",
            }
            if crop and not any(w in excluded for w in crop.lower().split()) and len(crop) >= 2:
                # Normalization
                if crop.lower() == "maiz":
                    crop = "Maize"
                self.result["field"]["crop"] = crop
                return

        # Fallback: single-word crop after "Crop:"
        m = re.search(r"\bCrop\s*:\s*([a-zA-Z]{3,20})\b", text, re.I)
        if m:
            crop = m.group(1).strip()
            excluded = {"total", "area", "stress", "field", "plant", "waterlogging", "level", "table"}
            if crop.lower() not in excluded:
                self.result["field"]["crop"] = crop
                return

        # Fallback: known crops anywhere in first 600 chars
        known_crops = (
            "corn", "maize", "wheat", "rice", "soybean", "barley", "cotton",
            "sugarcane", "potato", "tomato", "canola", "tobacco", "sugar beet",
            "sunflower", "sorghum", "millet", "oat", "rye",
        )
        crop_zone = text[:600].lower()
        for c in known_crops:
            if re.search(r"\b" + c + r"\b", crop_zone) and ("crop" in crop_zone or "field" in crop_zone):
                self.result["field"]["crop"] = c.title()
                return

    def _extract_growing_stage(self, text: str) -> None:
        INVALID_STAGES = {
            "stress", "level", "table", "potential", "waterlogged", "wet", "zone",
            "plant", "pest", "waterlogging", "analysis", "detection", "health", "fine",
            "flowering", "damage", "count", "additional", "information",
            "management", "zone", "management",
        }

        def _valid_stage(s: str) -> bool:
            if not s or len(s) > 25:
                return False
            lower = s.lower()
            return lower not in INVALID_STAGES and not any(w in INVALID_STAGES for w in lower.split())

        # "Growing stage: BBCH 40" or "Growing stage: V3" or "Growing stage: V3-V4"
        # Try multi-word first: "BBCH 40", then single-word: "V3"
        m = re.search(r"Growing\s+stage\s*:\s*(BBCH\s*\d+)", text, re.I)
        if m:
            self.result["field"]["growing_stage"] = m.group(1).strip()
            return

        # "76 days" or other numeric + unit stages
        m = re.search(r"Growing\s+stage\s*:\s*(\d+\s+days?)\b", text, re.I)
        if m:
            self.result["field"]["growing_stage"] = m.group(1).strip()
            return

        m = re.search(r"Growing\s+stage\s*:\s*([^\s]+(?:\s*-\s*[^\s]+)?)", text, re.I)
        if m:
            stage = m.group(1).strip().rstrip(".")
            if _valid_stage(stage):
                self.result["field"]["growing_stage"] = stage
                return

        # Fallback: value before label (happens in some layouts)
        # Try specific patterns first: "76 days Growing stage:"
        m = re.search(r"(\d+\s+days?)\s+Growing\s+stage\s*:", text, re.I)
        if m:
            self.result["field"]["growing_stage"] = m.group(1).strip()
            return
            
        m = re.search(r"([\w\s.\-]+?)\s+Growing\s+stage\s*:", text, re.I)
        if m:
            stage = m.group(1).strip()
            # If multiple words, take the last one (often the stage)
            if " " in stage:
                # But skip if it's "ZONE" or "MANAGEMENT"
                parts = [p for p in stage.split() if p.upper() not in ("ZONE", "MANAGEMENT", "STRESS")]
                stage = parts[-1] if parts else ""
            if _valid_stage(stage):
                self.result["field"]["growing_stage"] = stage
                return

        # BBCH anywhere
        m = re.search(r"BBCH\s*\d+", text, re.I)
        if m:
            self.result["field"]["growing_stage"] = m.group(0).strip()
            return

        # VT, V3, R1-R2 style
        m = re.search(r"(?<!\w)([VR][T\d](?:-[VR][T\d])?)(?!\w)", text)
        if m:
            self.result["field"]["growing_stage"] = m.group(1)
            return

        # Named stages
        for stage in ("vegetative", "flowering", "fruiting", "maturity", "harvest"):
            if re.search(r"\b" + stage + r"\b", text, re.I):
                self.result["field"]["growing_stage"] = stage
                return

    def _extract_field_area(self, text: str) -> None:
        HA_TO_ACRES = 1 / ACRES_TO_HA  # ~2.47105

        def _set_both(ha=None, ac=None):
            """Always populate both hectares and acres."""
            if ha is not None:
                self.result["field"]["area_hectares"] = ha
                self.result["field"]["area_acres"] = round(ha * HA_TO_ACRES, 4)
            elif ac is not None:
                self.result["field"]["area_acres"] = ac
                self.result["field"]["area_hectares"] = round(ac * ACRES_TO_HA, 4)

        # Hectares
        m = re.search(r"Field\s+area\s*:\s*([\d.]+)\s*(?:Hectares?|Ha)\b", text, re.I)
        if m:
            try:
                _set_both(ha=float(m.group(1)))
                return
            except ValueError:
                pass

        # Acres
        m = re.search(r"Field\s+area\s*:\s*([\d.]+)\s*(?:Acres?|Ac)\b", text, re.I)
        if m:
            try:
                _set_both(ac=float(m.group(1)))
                return
            except ValueError:
                pass

        # Windowed fallback near "Field area:" label
        label = re.search(r"Field\s+area\s*:", text, re.I)
        if label:
            window = text[label.start(): label.start() + 80]
            for pat, is_acre in [
                (r"([\d.]+)\s*(?:Hectares?|Ha)\b", False),
                (r"([\d.]+)\s*(?:Acres?|Ac)\b", True),
            ]:
                mw = re.search(pat, window, re.I)
                if mw:
                    try:
                        val = float(mw.group(1))
                        _set_both(ha=val if not is_acre else None, ac=val if is_acre else None)
                        return
                    except ValueError:
                        pass

        # Global fallback: any "X.XX Hectare" in the text
        m = re.search(r"([\d.]+)\s*Hectare", text, re.I)
        if m:
            try:
                _set_both(ha=float(m.group(1)))
            except ValueError:
                pass

    # ── Level extraction (data-driven from type config) ──────────────────

    def _extract_total_area(self, lower_spaced: str, full_spaced: str) -> None:
        if not self.type_config:
            return

        # Find the label position
        label_pos = -1
        for pat in self.type_config.get("total_area_patterns", []):
            idx = lower_spaced.find(pat)
            if idx >= 0:
                label_pos = idx
                break

        window = lower_spaced[label_pos: label_pos + 300] if label_pos >= 0 else lower_spaced

        # "X ha = Y% field"
        HA_TO_ACRES = 1 / ACRES_TO_HA
        m = re.search(r"([\d.]+)\s*ha\s*=\s*([\d.]+)\s*%", window, re.I)
        if m:
            ha = float(m.group(1))
            self.result["analysis"]["total_area_hectares"] = ha
            self.result["analysis"]["total_area_acres"] = round(ha * HA_TO_ACRES, 4)
            self.result["analysis"]["total_area_percent"] = float(m.group(2))
            return

        # "X acre = Y%"
        m = re.search(r"([\d.]+)\s*ac(?:re)?\s*=\s*([\d.]+)\s*%", window, re.I)
        if m:
            ac = float(m.group(1))
            self.result["analysis"]["total_area_acres"] = ac
            self.result["analysis"]["total_area_hectares"] = round(ac * ACRES_TO_HA, 4)
            self.result["analysis"]["total_area_percent"] = float(m.group(2))
            return

        # "Y% field" (percentage only)
        m = re.search(r"([\d.]+)\s*%\s*(?:of\s+the\s+)?field", window, re.I)
        if m:
            self.result["analysis"]["total_area_percent"] = float(m.group(1))

        # Global fallback — search entire text
        for pat, is_acre in [
            (r"([\d.]+)\s*ha\s*=\s*([\d.]+)\s*%", False),
            (r"([\d.]+)\s*ac(?:re)?\s*=\s*([\d.]+)\s*%", True),
        ]:
            m = re.search(pat, lower_spaced, re.I)
            if m:
                area, pct = float(m.group(1)), float(m.group(2))
                if is_acre:
                    self.result["analysis"]["total_area_acres"] = area
                    self.result["analysis"]["total_area_hectares"] = round(area * ACRES_TO_HA, 4)
                else:
                    self.result["analysis"]["total_area_hectares"] = area
                    self.result["analysis"]["total_area_acres"] = round(area * HA_TO_ACRES, 4)
                self.result["analysis"]["total_area_percent"] = pct
                return

    def _try_level_match(
        self, pattern: str, text: str, pct_group: int, area_group: int
    ) -> Optional[Tuple[float, float]]:
        for m in re.finditer(pattern, text, re.I):
            try:
                return float(m.group(pct_group)), float(m.group(area_group))
            except (ValueError, IndexError):
                continue
        return None

    def _extract_levels(self, text: str) -> None:
        if not self.type_config:
            return

        levels: List[Dict[str, Any]] = []
        seen: set = set()

        for lc in self.type_config.get("levels", []):
            name, severity = lc["name"], lc["severity"]
            if name in seen:
                continue

            result = None
            # Try percentage-first pattern
            if "pattern_pct_first" in lc:
                result = self._try_level_match(lc["pattern_pct_first"], text, 1, 2)
            # Try area-first pattern
            if result is None and "pattern_area_first" in lc:
                result = self._try_level_match(lc["pattern_area_first"], text, 2, 1)

            if result is None and "pattern_any" in lc:
                result = self._try_level_match(lc["pattern_any"], text, 1, 2)

            if result is None:
                # Spanish fallbacks
                if name == "Fine":
                    result = self._try_level_match(r"\bFine\s+([\d.]+)\s*%\s+([\d.]+)\b", text, 1, 2)
                elif "Potential" in name:
                    result = self._try_level_match(r"\bPotencial\s+Plaga\s+([\d.]+)\s*%\s+([\d.]+)\b", text, 1, 2)
                elif "Pest" in name:
                    result = self._try_level_match(r"\bPlaga\s+([\d.]+)\s*%\s+([\d.]+)\b", text, 1, 2)
            
            if result is None:
                continue

            pct, area_raw = result
            seen.add(name)

            area_ha = round(area_raw * ACRES_TO_HA, 4) if self._uses_acres else area_raw
            entry: Dict[str, Any] = {
                "level": name,
                "severity": severity,
                "percentage": pct,
                "area_hectares": area_ha,
            }
            if self._uses_acres:
                entry["area_acres"] = area_raw
            levels.append(entry)

        self.result["analysis"]["levels"] = levels

    # ── Supplementary report specific extraction ─────────────────────────

    def _extract_rx_spraying(self, text: str) -> None:
        rx = self.result["rx_spraying_analysis"]
        
        # Planned date and Pesticide type
        # Layout: Planned date [date] [type] Pesticide type
        m = re.search(r"Planned\s+date\s+([\d/:-]+)\s+([\w\s.\-]+?)\s+Pesticide\s+type", text, re.I)
        if m:
            rx["planned_date"] = m.group(1).strip()
            rx["pesticide_type"] = m.group(2).strip()
        else:
            # Fallbacks
            m_date = re.search(r"Planned\s+date\s+([\d/:-]+)", text, re.I)
            if m_date:
                rx["planned_date"] = m_date.group(1).strip()
            
            m_type = re.search(r"([\w\s.\-]+?)\s+Pesticide\s+type", text, re.I)
            if m_type:
                rx["pesticide_type"] = m_type.group(1).strip()

        # Total and Average pesticide
        # Layout: Total pesticide amount [val] [unit] Average pesticide amount [val] [unit]
        m = re.search(r"Total\s+pesticide\s+amount\s+([\d.]+)\s+([^\s]+)\s+Average\s+pesticide\s+amount\s+([\d.]+)\s+([^\s]+)", text, re.I)
        if m:
            rx["total_pesticide_amount"] = f"{m.group(1)} {m.group(2)}"
            rx["average_pesticide_amount"] = f"{m.group(3)} {m.group(4)}"
        else:
            # Fallback for individual matches
            m_total = re.search(r"Total\s+pesticide\s+amount\s+([\d.]+)\s+([^\s]+)", text, re.I)
            if m_total:
                rx["total_pesticide_amount"] = f"{m_total.group(1)} {m_total.group(2)}"
            m_avg = re.search(r"Average\s+pesticide\s+amount\s+([\d.]+)\s+([^\s]+)", text, re.I)
            if m_avg:
                rx["average_pesticide_amount"] = f"{m_avg.group(1)} {m_avg.group(2)}"
            
        # Rate table (Zone Range Area % Rate)
        rx["rates"] = []
        for m in re.finditer(r"\b(\d+)\s+([\d.]+:[\d.]+)\s+([\d.]+)\s*(ha|ac(?:re)?s?|Hect(?:área|are)[\s\w]*?)\s+([\d.]+)%\s+([\d.]+)\s*([^\s]+)", text, re.I):
            try:
                rx["rates"].append({
                    "color": int(m.group(1)),
                    "zone_range": m.group(2),
                    "area": float(m.group(3)),
                    "area_unit": m.group(4).strip(),
                    "percentage": float(m.group(5)),
                    "rate": float(m.group(6)),
                    "rate_unit": m.group(7).strip()
                })
            except ValueError:
                pass

    def _extract_zonation(self, text: str) -> None:
        zn = self.result["zonation_analysis"]
        
        # Tile size and zones
        m = re.search(r"(?:Tile\s+Size|Tamaño\s+de\s+la\s+Placa).*?(?:No\.\s+of\s+Zones|No\.\s+de\s+zonas)\s+(\d+)\s+([\d.]+m\s*[xX]\s*[\d.]+m)", text, re.I)
        if m:
            try:
                zn["num_zones"] = int(m.group(1))
                zn["tile_size"] = m.group(2).strip()
            except ValueError:
                pass

        # Zones table (Zone Range Area %)
        zn["zones"] = []
        for m in re.finditer(r"\b(\d+)\s+([\d.]+:[\d.]+)\s+([\d.]+)\s*(ha|ac(?:re)?s?|Hect(?:área|are)[\s\w]*?)\s+([\d.]+)%", text, re.I):
            try:
                zn["zones"].append({
                    "color": int(m.group(1)),
                    "zone_range": m.group(2),
                    "area": float(m.group(3)),
                    "area_unit": m.group(4).strip(),
                    "percentage": float(m.group(5))
                })
            except ValueError:
                pass

    # ── Stand Count specific extraction ──────────────────────────────────

    def _extract_stand_count(self, text: str) -> None:
        def _int(s: str) -> Optional[int]:
            try:
                return int(s.replace(",", ""))
            except ValueError:
                return None

        def _float(s: str) -> Optional[float]:
            try:
                return float(s.replace(",", ""))
            except ValueError:
                return None

        sc = self.result["stand_count_analysis"]

        # Plants Counted
        m = re.search(r"Plants?\s+Counted\s*:?\s*([\d,]+)", text, re.I)
        if m:
            v = _int(m.group(1))
            if v is not None:
                sc["plants_counted"] = v

        # Average Plant Density
        m = re.search(
            r"Average\s+Plant\s+Density\s*:?\s*([\d,.]+)\s*/\s*(Acre|Hectare|Ha)\b",
            text, re.I,
        )
        if m:
            dens = _float(m.group(1))
            if dens is not None:
                sc["average_plant_density"] = dens
                sc["plant_density_unit"] = m.group(2).strip()

        # Difference: "is 17% ... UNDER NORM" (text may have words between % and UNDER)
        for pat in [
            r"difference\b[^0-9]*?is\s*([\d,.]+)\s*%[\s\S]{0,100}?(UNDER|OVER)\s+NORM",
            r"([\d,.]+)\s*%[\s\S]{0,100}?(UNDER|OVER)\s+NORM",
            r"is\s+(UNDER|OVER)\s+NORM",
        ]:
            m = re.search(pat, text, re.I)
            if m:
                grps = m.groups()
                if len(grps) >= 2:
                    pct_val = grps[0].replace(",", "")
                    if pct_val.replace(".", "").isdigit():
                        sc["difference_percent"] = float(pct_val)
                typ = grps[-1].strip().upper() + " NORM"
                sc["difference_type"] = typ
                break

        # "close to X plants"
        m = re.search(r"close\s+to\s+([\d,]+)\s+plants?", text, re.I)
        if m:
            v = _int(m.group(1))
            if v is not None:
                sc["difference_plants"] = v

        # Planned / recommended plants — label may be split: "Recommended ... Plants ... 1,687,227"
        m = re.search(
            r"(?:Recommended|Planned|Target)\s+(?:[\w\s]*?)Plants?\s*:?\s*([\d,]+)", text, re.I
        )
        if m:
            v = _int(m.group(1))
            if v is not None:
                sc["planned_plants"] = v

        # Fallback: look for large numbers near "Recommended" or at bottom of text
        # In Stand Count charts, the data numbers often appear far from labels
        if sc["planned_plants"] is None:
            # Find all large numbers (> 10,000)
            large_nums = []
            for num_m in re.finditer(r"([\d,]{5,})", text):
                v = _int(num_m.group(1))
                if v is not None and v > 10000:
                    large_nums.append(v)
            
            # If we have multiple, the planned/recommended is typically the largest 
            # (bigger than counted if UNDER NORM, or middle if within norm)
            if large_nums:
                # Often the planned value is the max in the list of counts
                # especially if counted is 1.4M and planned is 1.6M
                sc["planned_plants"] = max(large_nums)

        # Compute difference_percent from planned vs counted if not in text
        planned = sc["planned_plants"]
        counted = sc["plants_counted"]
        if sc["difference_percent"] is None and planned and counted and planned > 0:
            sc["difference_percent"] = round(abs(planned - counted) / planned * 100, 1)

    # ── Additional info ──────────────────────────────────────────────────

    def _clean_additional_info(self, raw: str) -> Optional[str]:
        if not raw or len(raw) < 2:
            return None
        s = raw.strip()
        if "Test comment" in s:
            return "Test comment"
        # Filter out placeholder/template text
        if re.match(r"^\(?or\s+recommendation\)?$", s, re.I):
            return None
        s = re.sub(r"^\)\s*", "", s)
        s = re.sub(r"^\(or\s+recommendation\)\s*", "", s, flags=re.I).strip()
        s = re.sub(r"^Analysis\s+name\s*:\s*[^\n]+(?=\s|$)", "", s, flags=re.I).strip()
        s = re.sub(r"\s+Powered\s+by\s*:?\s*.*$", "", s, flags=re.I).strip()
        s = re.sub(r"\s+STRESS\s+LEVEL\s+TABLE\s*.*$", "", s, flags=re.I).strip()
        return s if 2 <= len(s) <= 400 else None

    def _extract_additional_info(self, full_text: str, text_spaced: str) -> None:
        patterns = [
            # Main pattern with lookahead for other sections
            r"Additional\s+[Ii]nformation\s*(?:\([^)]*\))?\s*:?\s*(.+?)(?=\s+(?:Powered\s+by|Analysis\s+name|STRESS\s+LEVEL\s+TABLE|Stress\s+level)|$)",
            r"Recommendation\s*:\s*([^\n]+)",
            r"Note\s*:\s*([^\n]+)",
        ]
        for pat in patterns:
            m = re.search(pat, text_spaced, re.I | re.DOTALL)
            if m:
                info = self._clean_additional_info(m.group(1).strip())
                if info:
                    self.result["additional_info"] = info
                    return
                # If it's just a placeholder, don't return yet; try subsequent fallbacks
                break

        # Multi-line fallback
        m = re.search(
            r"Additional\s+[Ii]nformation[^:]*:\s*\n(.+?)(?:\nPowered|\Z)",
            full_text, re.I | re.DOTALL,
        )
        if m:
            info = self._clean_additional_info(m.group(1).strip())
            if info:
                self.result["additional_info"] = info
                return

        # Bottom-of-page fallback: Look for notes between "STRESS LEVEL TABLE" and "Powered by"
        # Often in the "Test comment" layout
        m = re.search(r"STRESS\s+LEVEL\s+TABLE\s*.*?\n(.*?)(?=\s*Powered\s+by|$)", full_text, re.I | re.DOTALL)
        if m:
            content = m.group(1).strip()
            # Clean it — but only if it doesn't look like table data
            # Table data usually consists of level names followed by numbers
            if not re.search(r"^(?:Not\s+waterlogged|Fine|No\s+Damage|Full\s+Flowering)\b", content, re.I):
                info = self._clean_additional_info(content)
                if info:
                    self.result["additional_info"] = info

    # ── Totals fallback ──────────────────────────────────────────────────

    def _calculate_total_from_levels(self) -> None:
        """Calculate total area from levels, excluding healthy/none/low severity."""
        HA_TO_ACRES = 1 / ACRES_TO_HA
        levels = self.result["analysis"]["levels"]
        if not levels:
            return
        # Only sum "affected" levels — exclude healthy/none/low
        affected = [l for l in levels if l["severity"] not in ("healthy", "none", "low")]
        if self.result["analysis"]["total_area_hectares"] is None and affected:
            total = sum(l["area_hectares"] for l in affected)
            if total > 0:
                self.result["analysis"]["total_area_hectares"] = round(total, 4)
                self.result["analysis"]["total_area_acres"] = round(total * HA_TO_ACRES, 4)
        if self._uses_acres and self.result["analysis"]["total_area_acres"] is None and affected:
            total_ac = sum(l.get("area_acres", 0) for l in affected)
            if total_ac > 0:
                self.result["analysis"]["total_area_acres"] = round(total_ac, 4)
                self.result["analysis"]["total_area_hectares"] = round(total_ac * ACRES_TO_HA, 4)
        # Ensure both units exist if one is set
        if self.result["analysis"]["total_area_hectares"] and not self.result["analysis"]["total_area_acres"]:
            self.result["analysis"]["total_area_acres"] = round(
                self.result["analysis"]["total_area_hectares"] * HA_TO_ACRES, 4
            )
        elif self.result["analysis"]["total_area_acres"] and not self.result["analysis"]["total_area_hectares"]:
            self.result["analysis"]["total_area_hectares"] = round(
                self.result["analysis"]["total_area_acres"] * ACRES_TO_HA, 4
            )
        if self.result["analysis"]["total_area_percent"] is None and affected:
            pct = sum(l["percentage"] for l in affected)
            if pct > 0:
                self.result["analysis"]["total_area_percent"] = round(pct, 2)

    # ── Map image extraction + Cloudinary upload ─────────────────────────

    def _upload_to_cloudinary(self, image_bytes: bytes, img_format: str) -> Dict[str, Any]:
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = os.path.splitext(os.path.basename(self.pdf_path))[0]
            public_id = f"report_{base_name}_{timestamp}"
            res = cloudinary.uploader.upload(
                image_bytes,
                resource_type="image",
                public_id=public_id,
                folder=settings.cloudinary_folder or "starhawk-report-images",
                format=img_format.lower(),
                overwrite=False,
            )
            return {
                "url": res["secure_url"],
                "public_id": res["public_id"],
                "width": res.get("width"),
                "height": res.get("height"),
                "format": res.get("format"),
                "bytes": res.get("bytes"),
            }
        except CloudinaryError as e:
            logger.error(f"Cloudinary upload error: {e}")
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"Unexpected Cloudinary error: {e}")
            return {"error": str(e)}

    def _extract_map_image(self, page_num: int = 1, output_dir: Optional[str] = None) -> Dict[str, Any]:
        if page_num >= len(self.doc):
            return {"error": "Page not found"}

        page = self.doc[page_num]
        image_list = page.get_images(full=True)
        image_bytes = None
        img_format = "png"
        width = height = None
        source = "unknown"

        # For Agremo reports, we typically want a high-res RENDERED and CROPPED page
        # rather than an embedded image, because browser-printed PDFs often
        # embed the whole page as a single image, bypassing our crop.
        pass

        if not image_bytes:
            logger.info("Strategy: Rendering page for map image")
            # Render page with high DPI for clarity
            zoom = 300 / 72  # 300 DPI
            
            # Smart Crop for Agremo Reports (Remove headers, footers, and scale bars)
            # We calculate coordinates based on the original 72 DPI page size
            page_rect = page.rect
            y0 = page_rect.height * 0.11
            y1 = page_rect.height * 0.82
            clip_rect = fitz.Rect(0, y0, page_rect.width, y1)
            
            logger.info(f"Rendering map image with clip: {clip_rect} at zoom {zoom}")
            
            try:
                # The most reliable way to crop and render in one go
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip_rect)
                
                image_bytes = pix.tobytes("png")
                width, height = pix.width, pix.height
                source = "page_render_cropped"
                img_format = "png"
                
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                    pix.save(os.path.join(output_dir, "field_map_cropped.png"))
            except Exception as e:
                logger.warning(f"Cropped rendering failed, using full page: {e}")
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                image_bytes = pix.tobytes("png")
                width, height = pix.width, pix.height
                source = "page_render_full"
                img_format = "png"

        if not image_bytes:
            return {"error": "Could not extract or render map image"}

        upload_result = self._upload_to_cloudinary(image_bytes, img_format)
        if "error" in upload_result:
            return {
                "source": source, "error": upload_result["error"],
                "width": width, "height": height, "format": img_format,
            }
        return {
            "source": "cloudinary",
            "url": upload_result["url"],
            "public_id": upload_result["public_id"],
            "width": upload_result.get("width", width),
            "height": upload_result.get("height", height),
            "format": upload_result.get("format", img_format),
            "bytes": upload_result.get("bytes"),
        }

    # ── Page 1 orchestrator ──────────────────────────────────────────────

    def _parse_page1(self) -> None:
        if len(self.doc) < 1:
            return

        full_text, full_text_spaced = _build_text_from_page(self.pdf_path, self.doc, 0)
        lower_spaced = full_text_spaced.lower()

        logger.info(f"Page 1 text: {len(full_text_spaced)} chars")

        # Detect units & type
        self._uses_acres = _is_acres(full_text_spaced)
        self.report_type, self.type_config = self._detect_report_type(full_text)
        if self.report_type:
            self.result["report"]["detected_report_type"] = self.report_type

        # Common field extraction (works for ALL types)
        self._extract_survey_date(full_text_spaced)
        self._extract_report_type_label(full_text_spaced)
        self._extract_analysis_name(full_text_spaced, full_text)
        self._extract_crop(full_text_spaced)
        self._extract_growing_stage(full_text_spaced)
        self._extract_field_area(full_text_spaced)
        self._extract_additional_info(full_text, full_text_spaced)

        # Fallback analysis name
        if not self.result["report"]["analysis_name"] and self.report_type:
            self.result["report"]["analysis_name"] = self.report_type.replace("_", " ").title()

        # Type-specific extraction
        if self.report_type == "stand_count":
            self._extract_stand_count(full_text_spaced)
        elif self.report_type == "rx_spraying":
            self._extract_rx_spraying(full_text_spaced)
        elif self.report_type == "zonation":
            self._extract_zonation(full_text_spaced)
        else:
            # Agremo-style: extract levels and total area
            self._extract_total_area(lower_spaced, full_text_spaced)
            self._extract_levels(full_text_spaced)

    # ── Main entry point ─────────────────────────────────────────────────

    def extract(self, output_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Extract all structured data from the PDF.
        Returns a dict with metadata, report info, field, analysis, and map image.
        """
        try:
            if len(self.doc) >= 1:
                self._parse_page1()
                self._calculate_total_from_levels()

            if len(self.doc) >= 2:
                self.result["map_image"] = self._extract_map_image(1, output_dir)

            return self.result
        except Exception as e:
            logger.error(f"Extraction error: {e}", exc_info=True)
            raise

    def close(self):
        if hasattr(self, "doc") and self.doc:
            self.doc.close()


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def extract_pdf_report(pdf_path: str, output_dir: str = None) -> Dict[str, Any]:
    """Extract data from a PDF report — auto-detects type."""
    extractor = UnifiedReportExtractor(pdf_path)
    try:
        return extractor.extract(output_dir)
    finally:
        extractor.close()
