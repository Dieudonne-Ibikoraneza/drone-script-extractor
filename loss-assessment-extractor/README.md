# Loss Assessment PDF Extraction Service

Python microservice for extracting structured data from loss assessment PDF reports.

## Overview

This service provides an HTTP API endpoint that extracts structured data from loss assessment PDF reports. It is designed to be called by the NestJS backend after a PDF file has been uploaded.

## Features

- Extracts structured data from loss assessment PDF reports (Pest and Waterlogging)
- Extracts field maps and images from PDFs
- Provides RESTful API endpoint
- Health check endpoint for monitoring
- Comprehensive error handling
- Configurable via environment variables
- Cloudinary integration for image storage

## Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

## Installation

1. Navigate to the service directory:
   ```bash
   cd loss-assessment-extractor
   ```

2. Create a virtual environment (recommended):
   ```bash
   python -m venv venv
   
   # On Windows
   venv\Scripts\activate
   
   # On Linux/Mac
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Copy environment template:
   ```bash
   cp .env.example .env
   ```

5. Edit `.env` file with your configuration (if needed)

## Configuration

The service can be configured via environment variables in the `.env` file:

- `API_HOST`: Server host (default: `0.0.0.0`)
- `API_PORT`: Server port (default: `8000`)
- `LOG_LEVEL`: Logging level - DEBUG, INFO, WARNING, ERROR (default: `INFO`)
- `UPLOAD_DIR`: Directory where PDF files are stored (optional, defaults to `./uploads/loss-assessment` relative to backend root)
- `MAX_FILE_SIZE`: Maximum PDF file size in bytes (default: `10485760` = 10MB)
- `CORS_ORIGINS`: Comma-separated list of allowed origins, or `*` for all (default: `*`)

## Running the Service

### Development Mode

```bash
# From the loss-assessment-extractor directory
uvicorn app.main:app --reload --port 8000
```

Or run directly:
```bash
python -m app.main
```

### Production Mode

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## API Endpoints

### Health Check

**GET** `/health`

Returns the health status of the service.

**Response:**
```json
{
  "status": "healthy",
  "service": "loss-assessment-extractor",
  "version": "1.0.0"
}
```

### Extract Loss Assessment Data

**POST** `/extract-loss-assessment-data`

Extracts structured data from a loss assessment PDF report.

**Request Body:**
```json
{
  "pdfPath": "/absolute/path/to/uploads/loss-assessment/file.pdf"
}
```

**Success Response (200):**
```json
{
  "success": true,
  "extractedData": {
    "metadata": {
      "source_file": "file.pdf",
      "extracted_at": "2024-01-01T12:00:00",
      "total_pages": 2,
      "extractor_version": "1.0-loss-assessment"
    },
    "report": {
      "provider": "STARHAWK",
      "type": "Loss Assessment",
      "survey_date": "01-01-2024",
      "analysis_name": "Pest Damage",
      "detected_assessment_type": "pest"
    },
    "field": {
      "crop": "maize",
      "growing_stage": "Vegetative",
      "area_hectares": 2.5
    },
    "damage_analysis": {
      "total_area_hectares": 0.5,
      "total_area_percent": 20,
      "levels": [
        {
          "level": "No Damage",
          "severity": "none",
          "percentage": 80.0,
          "area_hectares": 2.0
        },
        {
          "level": "Low Damage",
          "severity": "low",
          "percentage": 15.0,
          "area_hectares": 0.375
        },
        {
          "level": "Moderate Damage",
          "severity": "moderate",
          "percentage": 5.0,
          "area_hectares": 0.125
        }
      ]
    },
    "additional_info": null,
    "map_image": {
      "source": "cloudinary",
      "url": "https://res.cloudinary.com/...",
      "public_id": "starhawk-loss-assessment-images/file_20240101_120000",
      "format": "png",
      "width": 800,
      "height": 600,
      "size_bytes": 50000
    }
  }
}
```

**Error Response (200):**
```json
{
  "success": false,
  "error": "PDF file not found: /path/to/file.pdf"
}
```

## Supported Assessment Types

The service supports the following loss assessment types:

### Pest Damage Assessment
- Keywords: "PEST", "Pest", "PEST DAMAGE", "Pest Damage"
- Damage Levels: No Damage, Low Damage, Moderate Damage, High Damage, Severe Damage

### Waterlogging Assessment
- Keywords: "WATERLOGGING", "Waterlogging", "WATER STRESS", "Water Stress"
- Damage Levels: No Waterlogging, Low Waterlogging, Moderate Waterlogging, High Waterlogging, Severe Waterlogging

## Integration with NestJS Backend

The NestJS backend should call this service after uploading a PDF file. The backend needs to:

1. Save the PDF file to the uploads directory
2. Call this service with the absolute path to the PDF file
3. Handle the response and store the extracted data

### Example Integration

The backend should configure the service URL in `.env`:

```env
LOSS_ASSESSMENT_SERVICE_URL=http://localhost:8000
LOSS_ASSESSMENT_SERVICE_TIMEOUT=30000
```

The backend sends the absolute file path to this service, which then reads the file, extracts the data, and returns it.

## File Path Requirements

- The service requires **absolute paths** to PDF files
- The service must have read access to the PDF file location
- Files must be valid PDF format
- Files must not exceed the configured maximum file size

## Error Handling

The service handles various error scenarios:

- **File not found**: Returns error response
- **File not readable**: Returns error response
- **File too large**: Returns error response
- **Invalid PDF format**: Returns error response
- **Extraction failures**: Returns error response with details
- **Unsupported assessment type**: Returns error response

All errors are logged for debugging purposes.

## Development

### Project Structure

```
loss-assessment-extractor/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI application
│   ├── extractor.py     # PDF extraction logic
│   ├── models.py        # Pydantic models
│   └── config.py        # Configuration management
├── requirements.txt     # Python dependencies
├── .env.example        # Environment template
├── .gitignore
└── README.md           # This file
```

### Testing

To test the service manually:

```bash
# Start the service
uvicorn app.main:app --reload

# In another terminal, test the health endpoint
curl http://localhost:8000/health

# Test extraction (replace with actual PDF path)
curl -X POST http://localhost:8000/extract-loss-assessment-data \
  -H "Content-Type: application/json" \
  -d '{"pdfPath": "/absolute/path/to/test.pdf"}'
```

## Docker Deployment (Optional)

A Dockerfile can be added for containerized deployment. Example:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## License

Part of the Starhawk Agricultural Insurance Management System.
