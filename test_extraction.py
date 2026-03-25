#!/usr/bin/env python3
"""
Test script — runs the unified extractor against all available PDFs
and prints the extracted JSON for verification.
"""

import os
import sys
import json

# Ensure imports work from the pdf-extractor root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.extractor import UnifiedReportExtractor


# Discover PDFs: local test files + sibling project PDFs
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)

PDF_SEARCH_DIRS = [
    BASE_DIR,
    os.path.join(ROOT_DIR, "drone-pdf-extractor"),
    os.path.join(ROOT_DIR, "loss-assessment-extractor"),
]


def find_pdfs() -> list:
    pdfs = []
    for d in PDF_SEARCH_DIRS:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.lower().endswith(".pdf"):
                pdfs.append(os.path.join(d, f))
    return sorted(set(pdfs))


def run():
    pdfs = find_pdfs()
    if not pdfs:
        print("ERROR: No PDF files found. Place test PDFs next to this script or in sibling project folders.")
        sys.exit(1)

    print(f"Found {len(pdfs)} PDF(s) to test:\n")
    for p in pdfs:
        print(f"  • {os.path.basename(p)}")
    print()

    results = {}
    failures = []

    for pdf_path in pdfs:
        name = os.path.basename(pdf_path)
        print("=" * 70)
        print(f"EXTRACTING: {name}")
        print("=" * 70)

        try:
            extractor = UnifiedReportExtractor(pdf_path)
            data = extractor.extract()
            extractor.close()

            # Strip map_image for cleaner output (it will have Cloudinary errors without creds)
            display = {k: v for k, v in data.items() if k != "map_image"}
            print(json.dumps(display, indent=2, default=str))

            # Quick health check
            report_type = data["report"].get("detected_report_type")
            print(f"\n  ✓ Detected type: {report_type}")

            checks = []
            if data["report"].get("survey_date"):
                checks.append("survey_date")
            if data["field"].get("crop"):
                checks.append("crop")
            if data["field"].get("area_hectares"):
                checks.append("area_hectares")

            if report_type == "stand_count":
                if data["stand_count_analysis"].get("plants_counted"):
                    checks.append("plants_counted")
                if data["stand_count_analysis"].get("average_plant_density"):
                    checks.append("avg_density")
            else:
                if data["analysis"].get("levels"):
                    checks.append(f"levels({len(data['analysis']['levels'])})")
                if data["analysis"].get("total_area_percent"):
                    checks.append("total_pct")

            print(f"  ✓ Fields extracted: {', '.join(checks) if checks else '(none)'}")

            if not report_type:
                failures.append((name, "type not detected"))

            results[name] = data

        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            failures.append((name, str(e)))

        print()

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Total PDFs: {len(pdfs)}")
    print(f"  Successful: {len(results)}")
    print(f"  Failed:     {len(failures)}")
    if failures:
        for name, reason in failures:
            print(f"    ✗ {name}: {reason}")
    else:
        print("  All PDFs extracted successfully! ✓")


if __name__ == "__main__":
    run()
