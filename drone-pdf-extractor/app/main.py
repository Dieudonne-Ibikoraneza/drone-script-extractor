"""
FastAPI application for drone PDF extraction service.
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
from app.extractor import AgremoReportExtractor

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Drone PDF Extraction Service",
    description="Microservice for extracting structured data from Agremo drone PDF reports",
    version="1.0.0"
)

# Configure CORS
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
        logger.info(f"Incoming request: {request.method} {request.url}")
        logger.info(f"Request path: {request.url.path}")
        logger.info(f"Request query params: {dict(request.query_params)}")
        
        # For POST requests, log that we received it (but don't consume the body)
        if request.method == "POST":
            logger.info(f"POST request received to {request.url.path}")
            # Note: We don't read the body here to avoid consuming the stream
            # FastAPI will handle body parsing in the endpoint
        
        response = await call_next(request)
        logger.info(f"Response status: {response.status_code}")
        return response


app.add_middleware(RequestLoggingMiddleware)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "drone-pdf-extractor",
        "version": "1.0.0"
    }


@app.post("/extract-drone-data", response_model=ExtractResponse)
async def extract_drone_data(request: ExtractRequest) -> ExtractResponse:
    """
    Extract structured data from a drone PDF report.
    
    Args:
        request: ExtractRequest containing either PDF file path (legacy) or base64-encoded PDF content
        
    Returns:
        ExtractResponse with extracted data or error message
    """
    logger.info("=" * 50)
    logger.info("Received POST request to /extract-drone-data")
    logger.info(f"Request received: pdfPath={request.pdfPath is not None}, pdfContent={'present' if request.pdfContent else 'not present'}")
    
    temp_file_path = None
    pdf_path = None
    
    try:
        # Handle base64 content (new method - preferred for production)
        if request.pdfContent:
            logger.info("Processing PDF from base64 content")
            
            try:
                # Decode base64 content
                pdf_bytes = base64.b64decode(request.pdfContent)
                logger.info(f"Decoded PDF content: {len(pdf_bytes)} bytes")
                
                # Validate file size
                if len(pdf_bytes) > settings.max_file_size:
                    logger.error(f"PDF file exceeds maximum size: {len(pdf_bytes)} bytes")
                    return ExtractResponse(
                        success=False,
                        error=f"PDF file exceeds maximum size of {settings.max_file_size} bytes"
                    )
                
                # Create temporary file
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
                temp_file_path = temp_file.name
                temp_file.write(pdf_bytes)
                temp_file.close()
                
                pdf_path = temp_file_path
                logger.info(f"Created temporary PDF file: {pdf_path}")
                
            except (binascii.Error, ValueError) as e:
                logger.error(f"Invalid base64 encoding: {str(e)}")
                return ExtractResponse(
                    success=False,
                    error=f"Invalid base64 encoding: {str(e)}"
                )
            except Exception as e:
                logger.error(f"Error processing base64 content: {str(e)}", exc_info=True)
                return ExtractResponse(
                    success=False,
                    error=f"Failed to process PDF content: {str(e)}"
                )
        
        # Handle file path (legacy method - for backward compatibility)
        elif request.pdfPath:
            logger.info(f"Processing PDF from file path: {request.pdfPath}")
            pdf_path = request.pdfPath
            
            # Validate file exists
            if not os.path.exists(pdf_path):
                logger.error(f"PDF file not found: {pdf_path}")
                return ExtractResponse(
                    success=False,
                    error=f"PDF file not found: {pdf_path}"
                )
            
            # Validate file is readable
            if not os.access(pdf_path, os.R_OK):
                logger.error(f"PDF file is not readable: {pdf_path}")
                return ExtractResponse(
                    success=False,
                    error=f"PDF file is not readable: {pdf_path}"
                )
            
            # Validate file size
            file_size = os.path.getsize(pdf_path)
            if file_size > settings.max_file_size:
                logger.error(f"PDF file exceeds maximum size: {file_size} bytes")
                return ExtractResponse(
                    success=False,
                    error=f"PDF file exceeds maximum size of {settings.max_file_size} bytes"
                )
        else:
            logger.error("Neither pdfPath nor pdfContent provided")
            return ExtractResponse(
                success=False,
                error="Either pdfPath or pdfContent must be provided"
            )
        
        # Validate file extension (check if it's a PDF by reading first bytes)
        try:
            with open(pdf_path, 'rb') as f:
                header = f.read(4)
                if header != b'%PDF':
                    logger.error(f"File is not a valid PDF: {pdf_path}")
                    return ExtractResponse(
                        success=False,
                        error="File is not a valid PDF"
                    )
        except Exception as e:
            logger.error(f"Error validating PDF: {str(e)}")
            return ExtractResponse(
                success=False,
                error=f"Error validating PDF file: {str(e)}"
            )
        
        # Extract data from PDF
        extractor = None
        try:
            logger.info(f"Starting PDF extraction for: {pdf_path}")
            extractor = AgremoReportExtractor(pdf_path)
            
            extracted_data = extractor.extract()
            
            logger.info(f"Successfully extracted data from PDF: {pdf_path}")
            
            return ExtractResponse(
                success=True,
                extractedData=extracted_data
            )
            
        except Exception as e:
            logger.error(f"Error extracting PDF data: {str(e)}", exc_info=True)
            return ExtractResponse(
                success=False,
                error=f"Failed to extract PDF data: {str(e)}"
            )
        finally:
            if extractor:
                extractor.close()
    
    finally:
        # Clean up temporary file if created from base64
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
                logger.info(f"Cleaned up temporary file: {temp_file_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temporary file {temp_file_path}: {str(e)}")


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": "Internal server error",
            "detail": str(exc) if settings.log_level == "DEBUG" else "An unexpected error occurred"
        }
    )


if __name__ == "__main__":
    import uvicorn
    logger.info("=" * 50)
    logger.info("Starting Drone PDF Extraction Service")
    logger.info(f"Host: {settings.api_host}")
    logger.info(f"Port: {settings.api_port}")
    logger.info(f"Log Level: {settings.log_level}")
    logger.info("=" * 50)
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level=settings.log_level.lower()
    )

