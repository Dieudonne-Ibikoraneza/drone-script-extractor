#!/usr/bin/env python3
"""
Agremo PDF Report Extractor - Unified version supporting multiple analysis types
Supports: Plant Stress, Flowering, and extensible for future types
With Cloudinary upload for map images (no base64 in response)
"""

import fitz
import re
import os
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

import cloudinary
import cloudinary.uploader
from cloudinary.exceptions import Error as CloudinaryError
from app.config import settings

if settings.cloudinary_cloud_name and settings.cloudinary_api_key and settings.cloudinary_api_secret:
    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True
    )
else:
    print("Warning: Cloudinary credentials not configured in settings")

logger = __import__('logging').getLogger(__name__)


class AnalysisTypeConfig:
    """Configuration for different analysis types"""

    # Define patterns and metadata for each analysis type
    TYPES = {
        "plant_stress": {
            "keywords": ["PLANT STRESS", "Plant Stress"],
            "total_area_pattern": r"total area plant stress:",
            "levels": [
                {"name": "Fine", "severity": "healthy", "pattern": r'\bFine\s+([\d.]+)%\s+([\d.]+)\b'},
                {"name": "Potential Plant Stress", "severity": "moderate", "pattern": r'\bPotential\s+Plant\s+Stress\s+([\d.]+)%\s+([\d.]+)\b'},
                {"name": "Plant Stress", "severity": "high", "pattern": r'\bPlant\s+Stress\s+([\d.]+)%\s+([\d.]+)\b', "exclude_context": "potential"}
            ],
            "field_name": "weed_analysis"  # Generic name instead of "weed_analysis"
        },
        "flowering": {
            "keywords": ["FLOWERING", "Flowering"],
            "total_area_pattern": r"total area flowering:",
            "levels": [
                {"name": "Full Flowering", "severity": "high", "pattern": r'\bFull\s+Flowering\s+([\d.]+)%\s+([\d.]+)\b'},
                {"name": "Flowering", "severity": "moderate", "pattern": r'\bFlowering\s+([\d.]+)%\s+([\d.]+)\b', "exclude_context": "full|no"},
                {"name": "No Flowering", "severity": "low", "pattern": r'\bNo\s+Flowering\s+([\d.]+)%\s+([\d.]+)\b'}
            ],
            "field_name": "weed_analysis"
        },
        # Easy to add more types:
        # "weed_pressure": {
        #     "keywords": ["WEED", "Weed Pressure"],
        #     "total_area_pattern": r"total area weed:",
        #     "levels": [...],
        #     "field_name": "weed_analysis"
        # }
    }


class AgremoReportExtractor:
    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.doc = fitz.open(pdf_path)
        self.analysis_type = None  # Will be detected during parsing
        self.analysis_config = None
        self.result = self._init_result_structure()

    def _init_result_structure(self) -> Dict[str, Any]:
        return {
            "metadata": {
                "source_file": os.path.basename(self.pdf_path),
                "extracted_at": datetime.now().isoformat(),
                "total_pages": len(self.doc),
                "extractor_version": "3.0-unified"
            },
            "report": {
                "provider": "STARHAWK",
                "type": None,
                "survey_date": None,
                "analysis_name": None,
                "detected_analysis_type": None  # New field to indicate what type was detected
            },
            "field": {
                "crop": None,
                "growing_stage": None,
                "area_hectares": None
            },
            "weed_analysis": {
                "total_area_hectares": None,
                "total_area_percent": None,
                "levels": []
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
                "error": None
            }
        }

    def _detect_analysis_type(self, text: str) -> Tuple[Optional[str], Optional[Dict]]:
        """
        Detect the analysis type from the PDF text
        Returns: (type_key, type_config) or (None, None)
        """
        text_lower = text.lower()

        for type_key, config in AnalysisTypeConfig.TYPES.items():
            for keyword in config["keywords"]:
                if keyword.lower() in text_lower:
                    logger.info(f"Detected analysis type: {type_key} (keyword: '{keyword}')")
                    return type_key, config

        logger.warning("Could not detect analysis type from PDF")
        return None, None

    def _parse_page1_text(self, text: str) -> None:
        blocks = self.doc[0].get_text("blocks")
        full_text = '\n'.join([b[4].strip() for b in blocks if len(b[4].strip()) > 3])
        full_text_spaced = ' '.join([b[4].strip() for b in blocks if len(b[4].strip()) > 3])
        lower_full = full_text.lower()
        lower_full_spaced = full_text_spaced.lower()

        # Detect analysis type first
        self.analysis_type, self.analysis_config = self._detect_analysis_type(full_text)
        if self.analysis_type:
            self.result["report"]["detected_analysis_type"] = self.analysis_type

        # Survey date
        date_match = re.search(r'Survey\s+date:\s*(\d{2}-\d{2}-\d{4})', full_text_spaced, re.I)
        if not date_match:
            date_match = re.search(r'(\d{2}-\d{2}-\d{4})', full_text_spaced)
        if date_match:
            self.result["report"]["survey_date"] = date_match.group(1)

        # Report type
        if "Crop Monitoring" in full_text:
            self.result["report"]["type"] = "Crop Monitoring"
        if "Plant Health Monitoring" in full_text:
            self.result["report"]["type"] = "Plant Health Monitoring"

        # Analysis name
        analysis_match = re.search(r'Analysis\s+name:\s*([\w\s]+?)(?:\s+(?:Growing|Field|STRESS|Total|Additional)|$)', full_text_spaced, re.I)
        if analysis_match:
            name = analysis_match.group(1).strip()
            if "STRESS LEVEL" not in name.upper() and len(name) < 50:
                self.result["report"]["analysis_name"] = name

        # Fallback: use detected type keywords if no analysis name found
        if not self.result["report"]["analysis_name"] and self.analysis_config:
            for keyword in self.analysis_config["keywords"]:
                if keyword in full_text:
                    self.result["report"]["analysis_name"] = keyword
                    break

        # Crop extraction
        self._extract_crop(full_text_spaced)

        # Growing stage
        self._extract_growing_stage(full_text_spaced)

        # Field area
        self._extract_field_area(full_text_spaced)

        # Total area & percentage (dynamic based on analysis type)
        self._extract_total_area(lower_full_spaced)

        # Extract levels (dynamic based on analysis type)
        self._extract_levels(full_text_spaced)

        # Additional info
        self._extract_additional_info(full_text, full_text_spaced)

    def _extract_crop(self, full_text_spaced: str) -> None:
        """Extract crop information"""
        crop_label_pos = full_text_spaced.find("Crop:")
        if crop_label_pos >= 0:
            crop_patterns = [
                r'(?:sugar\s+beet|wheat|corn|soybean|rice|barley|potato|tomato|cotton|canola|tobacco)',
                r'([a-z]+\s+[a-z]+)',
                r'([a-z]{4,})',
            ]
            search_start = crop_label_pos + 5
            search_text = full_text_spaced[search_start:search_start + 200].lower()

            excluded_words = ['total', 'area', 'stress', 'field', 'growing', 'stage',
                              'analysis', 'name', 'plant', 'health', 'monitoring', 'flowering']

            for pattern in crop_patterns:
                match = re.search(pattern, search_text, re.I)
                if match:
                    crop = match.group(1 if match.lastindex else 0).strip()
                    if crop and crop.lower() not in excluded_words:
                        self.result["field"]["crop"] = crop
                        break

    def _extract_growing_stage(self, full_text_spaced: str) -> None:
        """Extract growing stage (BBCH code)"""
        stage_label_pos = full_text_spaced.find("Growing stage:")
        if stage_label_pos >= 0:
            search_text = full_text_spaced[stage_label_pos:stage_label_pos + 100]
            stage_match = re.search(r'BBCH\s*\d+|BBCH\d+', search_text, re.I)
            if stage_match:
                self.result["field"]["growing_stage"] = stage_match.group(0).strip()
            else:
                stage_match = re.search(r'BBCH\s*\d+|BBCH\d+', full_text_spaced, re.I)
                if stage_match:
                    self.result["field"]["growing_stage"] = stage_match.group(0).strip()

    def _extract_field_area(self, full_text_spaced: str) -> None:
        """Extract field area in hectares"""
        area_label_pos = full_text_spaced.find("Field area:")
        if area_label_pos >= 0:
            search_text = full_text_spaced[area_label_pos:area_label_pos + 100]
            area_match = re.search(r'([\d.]+)\s*Hectare', search_text, re.I)
            if area_match:
                try:
                    self.result["field"]["area_hectares"] = float(area_match.group(1))
                except ValueError:
                    pass
        else:
            area_match = re.search(r'([\d.]+)\s*Hectare', full_text_spaced, re.I)
            if area_match:
                try:
                    self.result["field"]["area_hectares"] = float(area_match.group(1))
                except ValueError:
                    pass

    def _extract_total_area(self, lower_full_spaced: str) -> None:
        """Extract total area and percentage (dynamic based on analysis type)"""
        if not self.analysis_config:
            logger.warning("No analysis config detected, skipping total area extraction")
            return

        # Use the pattern from the config
        total_pattern = self.analysis_config["total_area_pattern"]
        total_label_pos = lower_full_spaced.find(total_pattern)

        if total_label_pos >= 0:
            # Search AFTER the label (Plant Stress format: "Total area PLANT STRESS: 22.04 ha = 69% field")
            search_text_after = lower_full_spaced[total_label_pos:total_label_pos + 200]

            # Try pattern 1: "X ha = Y% field" (Plant Stress format)
            total_match = re.search(r'([\d.]+)\s*ha\s*=\s*([\d.]+)%\s*field', search_text_after, re.I)
            if total_match:
                try:
                    self.result["weed_analysis"]["total_area_hectares"] = float(total_match.group(1))
                    self.result["weed_analysis"]["total_area_percent"] = float(total_match.group(2))
                    logger.info(f"Extracted total area from pattern 1: {total_match.group(1)} ha = {total_match.group(2)}%")
                    return  # Success, exit
                except (ValueError, IndexError) as e:
                    logger.error(f"Error parsing total area pattern 1: {e}")

            # Pattern 2: "Y% field" after label (Flowering format)
            percent_match = re.search(r'([\d.]+)%\s*field', search_text_after, re.I)
            if percent_match:
                try:
                    self.result["weed_analysis"]["total_area_percent"] = float(percent_match.group(1))
                    logger.info(f"Extracted percentage from after label: {percent_match.group(1)}%")
                    return  # Success, exit
                except (ValueError, IndexError) as e:
                    logger.error(f"Error parsing total area pattern 2: {e}")

            # Pattern 3: Search BEFORE the label (Flowering alternative format: "6.58% field\nTotal area FLOWERING:")
            search_text_before = lower_full_spaced[max(0, total_label_pos - 100):total_label_pos]
            percent_match_before = re.search(r'([\d.]+)%\s*field', search_text_before, re.I)
            if percent_match_before:
                try:
                    self.result["weed_analysis"]["total_area_percent"] = float(percent_match_before.group(1))
                    logger.info(f"Extracted percentage from before label: {percent_match_before.group(1)}%")
                    return  # Success, exit
                except (ValueError, IndexError) as e:
                    logger.error(f"Error parsing percentage before label: {e}")

        # Fallback 1: search entire text for "X ha = Y% field"
        total_match = re.search(r'([\d.]+)\s*ha\s*=\s*([\d.]+)%\s*field', lower_full_spaced, re.I)
        if total_match:
            try:
                self.result["weed_analysis"]["total_area_hectares"] = float(total_match.group(1))
                self.result["weed_analysis"]["total_area_percent"] = float(total_match.group(2))
                logger.info(f"Extracted total area from fallback 1: {total_match.group(1)} ha = {total_match.group(2)}%")
                return  # Success, exit
            except (ValueError, IndexError) as e:
                logger.error(f"Error parsing total area (fallback 1): {e}")

        # Fallback 2: search entire text for "Y% field" only
        percent_match = re.search(r'([\d.]+)%\s*field', lower_full_spaced, re.I)
        if percent_match:
            try:
                self.result["weed_analysis"]["total_area_percent"] = float(percent_match.group(1))
                logger.info(f"Extracted percentage from fallback 2: {percent_match.group(1)}%")
            except (ValueError, IndexError) as e:
                logger.error(f"Error parsing percentage (fallback 2): {e}")

    def _extract_levels(self, full_text_spaced: str) -> None:
        """Extract levels dynamically based on analysis type configuration"""
        if not self.analysis_config:
            logger.warning("No analysis config detected, skipping levels extraction")
            return

        levels = []
        seen = set()

        for level_config in self.analysis_config["levels"]:
            level_name = level_config["name"]
            severity = level_config["severity"]
            pattern = level_config["pattern"]
            exclude_context = level_config.get("exclude_context", None)

            matches = re.finditer(pattern, full_text_spaced, re.I)

            for match in matches:
                # Check exclude context if specified
                if exclude_context:
                    start_pos = match.start()
                    context_start = max(0, start_pos - 20)
                    context = full_text_spaced[context_start:start_pos].lower()

                    # Check if any exclude keyword is in context
                    exclude_keywords = exclude_context.split("|")
                    if any(keyword in context for keyword in exclude_keywords):
                        continue

                # Create unique key to avoid duplicates
                key = f"{level_name}_{match.group(1)}_{match.group(2)}"
                if key not in seen:
                    seen.add(key)
                    try:
                        percent = float(match.group(1))
                        ha = float(match.group(2))

                        # Always add the level (even 0% entries - they're still useful info)
                        levels.append({
                            "level": level_name,
                            "severity": severity,
                            "percentage": percent,
                            "area_hectares": ha
                        })
                    except ValueError as e:
                        logger.warning(f"Error parsing level {level_name}: {e}")

        self.result["weed_analysis"]["levels"] = levels

    def _extract_additional_info(self, full_text: str, full_text_spaced: str) -> None:
        """Extract additional information or recommendations"""
        if "Test comment" in full_text:
            self.result["additional_info"] = "Test comment"
        else:
            info_match = re.search(r'Additional\s+Information\s*\(or\s+recommendation\):\s*(.+?)(?:\s+Powered|$)',
                                   full_text_spaced, re.I | re.DOTALL)
            if info_match:
                comment = info_match.group(1).strip()
                if "Test comment" in comment:
                    self.result["additional_info"] = "Test comment"
                elif len(comment) < 200:
                    self.result["additional_info"] = comment

    def _upload_to_cloudinary(self, image_bytes: bytes, img_format: str) -> Dict[str, Any]:
        """Upload image bytes to Cloudinary and return relevant info"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            public_id = f"agremo_map_{timestamp}"

            upload_result = cloudinary.uploader.upload(
                image_bytes,
                resource_type="image",
                public_id=public_id,
                folder=settings.cloudinary_folder or "drone-reports",
                format=img_format.lower(),
                overwrite=False,
            )

            return {
                "url": upload_result["secure_url"],
                "public_id": upload_result["public_id"],
                "width": upload_result.get("width"),
                "height": upload_result.get("height"),
                "format": upload_result.get("format"),
                "bytes": upload_result.get("bytes"),
            }

        except CloudinaryError as e:
            logger.error(f"Cloudinary upload error: {str(e)}")
            return {"error": f"Cloudinary upload failed: {str(e)}"}
        except Exception as e:
            logger.error(f"Unexpected error during Cloudinary upload: {str(e)}")
            return {"error": str(e)}

    def _extract_map_image(self, page_num: int = 1, output_dir: Optional[str] = None) -> Dict[str, Any]:
        """Extract map image from specified page"""
        if page_num >= len(self.doc):
            return {"error": "Page not found"}

        page = self.doc[page_num]
        image_list = page.get_images(full=True)

        image_bytes = None
        img_format = "png"
        width = None
        height = None
        source = "unknown"

        if image_list:
            # Extract embedded images
            images_data = []
            for img in image_list:
                xref = img[0]
                base_image = self.doc.extract_image(xref)
                bytes_data = base_image["image"]
                images_data.append({
                    "bytes": bytes_data,
                    "format": base_image["ext"],
                    "size": len(bytes_data),
                    "width": base_image.get("width", 0),
                    "height": base_image.get("height", 0),
                })

            # Use the largest image
            largest = max(images_data, key=lambda x: x["size"])
            image_bytes = largest["bytes"]
            img_format = largest["format"]
            width = largest["width"]
            height = largest["height"]
            source = "embedded"

        else:
            # Fallback: render the whole page as image
            zoom = 150 / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            image_bytes = pix.tobytes("png")
            width = pix.width
            height = pix.height
            source = "page_render"

            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
                filepath = os.path.join(output_dir, "field_map.png")
                pix.save(filepath)

        if not image_bytes:
            return {"error": "Could not extract or render map image"}

        # Upload to Cloudinary
        upload_result = self._upload_to_cloudinary(image_bytes, img_format)

        if "error" in upload_result:
            return {
                "source": source,
                "error": upload_result["error"],
                "width": width,
                "height": height,
                "format": img_format
            }

        # Success
        return {
            "source": "cloudinary",
            "url": upload_result["url"],
            "public_id": upload_result["public_id"],
            "width": upload_result.get("width", width),
            "height": upload_result.get("height", height),
            "format": upload_result.get("format", img_format),
            "bytes": upload_result.get("bytes")
        }

    def _calculate_total_from_levels(self) -> None:
        """Calculate total area from levels if not already set (fallback for Flowering format)"""
        levels = self.result["weed_analysis"]["levels"]

        # If total_area_hectares is missing but we have levels, calculate it
        if self.result["weed_analysis"]["total_area_hectares"] is None and levels:
            # For Flowering: sum the areas of severity levels (not "No Flowering")
            # For Plant Stress: sum the areas of stress levels (not "Fine")

            total_hectares = 0.0

            if self.analysis_type == "flowering":
                # Sum "Full Flowering" and "Flowering" (exclude "No Flowering")
                for level in levels:
                    if level["severity"] in ["high", "moderate"]:
                        total_hectares += level["area_hectares"]
            elif self.analysis_type == "plant_stress":
                # Sum "Plant Stress" and "Potential Plant Stress" (exclude "Fine")
                for level in levels:
                    if level["severity"] in ["high", "moderate"]:
                        total_hectares += level["area_hectares"]
            else:
                # Generic: sum all non-healthy/low severity levels
                for level in levels:
                    if level["severity"] not in ["healthy", "low"]:
                        total_hectares += level["area_hectares"]

            if total_hectares > 0:
                self.result["weed_analysis"]["total_area_hectares"] = round(total_hectares, 2)
                logger.info(f"Calculated total_area_hectares from levels: {total_hectares} ha")

    def extract(self, output_dir: Optional[str] = None) -> Dict[str, Any]:
        """Main extraction method"""
        if len(self.doc) >= 1:
            page1_text = self.doc[0].get_text("text")
            self._parse_page1_text(page1_text)

            # Calculate total from levels if missing (for Flowering PDFs)
            self._calculate_total_from_levels()

        if len(self.doc) >= 2:
            map_data = self._extract_map_image(1, output_dir)
            self.result["map_image"] = map_data

        return self.result

    def close(self):
        if hasattr(self, 'doc') and self.doc:
            self.doc.close()


def extract_pdf_report(pdf_path: str, output_dir: str = None) -> Dict[str, Any]:
    """Convenience function to extract PDF report"""
    extractor = AgremoReportExtractor(pdf_path)
    try:
        result = extractor.extract(output_dir)
        return result
    finally:
        extractor.close()