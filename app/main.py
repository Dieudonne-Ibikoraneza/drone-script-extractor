"""
FastAPI application for the unified PDF extraction service.
Single endpoint handles ALL report types: Plant Stress, Flowering,
Waterlogging, Pest, Stand Count, and any future Agremo-style report.
"""

import os
import logging
import base64
import binascii
import tempfile
from pathlib import Path
from typing import Dict, Any

from fastapi import FastAPI, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.models import ExtractRequest, ExtractResponse
from app.extractor import UnifiedReportExtractor

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Unified PDF Extraction Service",
    description="Microservice for extracting structured data from drone / loss-assessment PDF reports. "
                "Auto-detects report type (Plant Stress, Flowering, Waterlogging, Pest, Stand Count).",
    version="1.0.0",
)

# CORS
cors_origins = settings.get_cors_origins_list()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins if "*" not in cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request logging middleware
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        logger.info(f"Incoming: {request.method} {request.url.path}")
        response = await call_next(request)
        logger.info(f"Response: {response.status_code}")
        return response


app.add_middleware(RequestLoggingMiddleware)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "pdf-extractor",
        "version": "1.0.0",
        "supported_types": [
            "plant_stress", "flowering", "waterlogging", "pest", "stand_count",
        ],
    }


@app.post("/extract", response_model=ExtractResponse)
async def extract_pdf_data(request: ExtractRequest) -> ExtractResponse:
    """
    Extract structured data from any supported PDF report.

    Accepts either a file path (legacy) or base64-encoded PDF content.
    Auto-detects the report type and returns the appropriate structured data.
    """
    logger.info("=" * 50)
    logger.info("POST /extract")
    logger.info(
        f"pdfPath={'yes' if request.pdfPath else 'no'}, "
        f"pdfContent={'yes' if request.pdfContent else 'no'}"
    )

    temp_file_path = None
    pdf_path = None

    try:
        # --- Resolve PDF path ---
        if request.pdfContent:
            logger.info("Processing PDF from base64 content")
            try:
                pdf_bytes = base64.b64decode(request.pdfContent)
                logger.info(f"Decoded: {len(pdf_bytes)} bytes")

                if len(pdf_bytes) > settings.max_file_size:
                    return ExtractResponse(
                        success=False,
                        error=f"PDF exceeds max size ({settings.max_file_size} bytes)",
                    )

                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                temp_file_path = temp_file.name
                temp_file.write(pdf_bytes)
                temp_file.close()
                pdf_path = temp_file_path

            except (binascii.Error, ValueError) as e:
                return ExtractResponse(success=False, error=f"Invalid base64: {e}")
            except Exception as e:
                return ExtractResponse(success=False, error=f"Failed to process PDF content: {e}")

        elif request.pdfPath:
            logger.info(f"Processing PDF from path: {request.pdfPath}")
            pdf_path = request.pdfPath

            if not os.path.exists(pdf_path):
                return ExtractResponse(success=False, error=f"File not found: {pdf_path}")
            if not os.access(pdf_path, os.R_OK):
                return ExtractResponse(success=False, error=f"File not readable: {pdf_path}")
            file_size = os.path.getsize(pdf_path)
            if file_size > settings.max_file_size:
                return ExtractResponse(
                    success=False,
                    error=f"PDF exceeds max size ({settings.max_file_size} bytes)",
                )
        else:
            return ExtractResponse(
                success=False,
                error="Either pdfPath or pdfContent must be provided",
            )

        # --- Validate PDF header ---
        try:
            with open(pdf_path, "rb") as f:
                header = f.read(4)
                if header != b"%PDF":
                    return ExtractResponse(success=False, error="File is not a valid PDF")
        except Exception as e:
            return ExtractResponse(success=False, error=f"Error validating PDF: {e}")

        # --- Extract ---
        extractor = None
        try:
            extractor = UnifiedReportExtractor(pdf_path)
            extracted_data = extractor.extract()
            logger.info(f"Extraction complete: type={extracted_data['report'].get('detected_report_type')}")
            return ExtractResponse(success=True, extractedData=extracted_data)
        except Exception as e:
            logger.error(f"Extraction failed: {e}", exc_info=True)
            return ExtractResponse(success=False, error=f"Extraction failed: {e}")
        finally:
            if extractor:
                extractor.close()

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception:
                pass


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": "Internal server error",
            "detail": str(exc) if settings.log_level == "DEBUG" else "An unexpected error occurred",
        },
    )


if __name__ == "__main__":
    import uvicorn

    logger.info("=" * 50)
    logger.info("Starting Unified PDF Extraction Service")
    logger.info(f"Host: {settings.api_host}")
    logger.info(f"Port: {settings.api_port}")
    logger.info("=" * 50)
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level=settings.log_level.lower(),
    )
