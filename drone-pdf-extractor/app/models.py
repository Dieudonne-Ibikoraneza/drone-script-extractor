"""
Pydantic models for API request and response.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ExtractRequest(BaseModel):
    """Request model for PDF extraction endpoint."""
    
    pdfPath: Optional[str] = Field(
        None,
        description="Absolute path to the PDF file to extract data from (legacy support)",
        example="/path/to/uploads/drone-analysis/file.pdf"
    )
    
    pdfContent: Optional[str] = Field(
        None,
        description="Base64-encoded PDF file content",
        example="JVBERi0xLjQKJeLjz9MKMSAwIG9iago8PC..."
    )


class ExtractResponse(BaseModel):
    """Response model for PDF extraction endpoint."""
    
    success: bool = Field(
        ...,
        description="Whether the extraction was successful"
    )
    
    extractedData: Optional[Dict[str, Any]] = Field(
        None,
        description="Extracted data from the PDF if successful"
    )
    
    error: Optional[str] = Field(
        None,
        description="Error message if extraction failed"
    )

