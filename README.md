# Drone Script Extractor: Unified PDF Extraction Service

The **Drone Script Extractor** is a high-performance Python microservice built with FastAPI, designed to extract structured agricultural data from drone-based survey and loss-assessment PDF reports. 

Whether the PDF is a native digital file or a scanned/browser-printed rasterised document, this service employs a multi-strategy extraction pipeline (Native Text → Fitz Blocks → OCR) to ensure 100% accuracy in data retrieval.

---

## 🚀 Key Features

- **🛡️ 100% Extraction Accuracy**: Combined strategies for native text and OCR (Tesseract + PIL).
- **🤖 Automatic Type Detection**: Identifies report types from content and filenames (Plant Stress, Flowering, etc.).
- **📊 Structured JSON Output**: Returns comprehensive metadata, field details, analysis results, and map images.
- **🖼️ Cloudinary Integration**: Automatically uploads maps and analysis images to Cloudinary for easy access.
- **🔌 Highly Extensible**: New Agremo-style report types can be added via a central configuration registry.
- **🌐 FastAPI Powered**: High-performance, asynchronous REST API with built-in health checks.

---

## 📋 Supported Report Types

The service currently supports the following report formats:

1.  **Plant Stress**: Fine, Potential, and High stress levels.
2.  **Flowering**: No Flowering, Flowering, and Full Flowering.
3.  **Waterlogging**: Zonal analysis of wet and waterlogged areas.
4.  **Pest Stress**: Detection of pest damage and stress levels.
5.  **Stand Count**: Statistical analysis of plant populations, density, and planned vs. actual counts.
6.  **RX Spraying Zone Management**: Prescription maps and pesticide rate analysis.
7.  **Zonal Statistics (Zonation)**: Detailed zonal management metrics.

---

## 🏗️ Getting Started

### Prerequisites

- Python 3.9+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (optional but recommended for rasterised PDFs)

### Installation

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/Dieudonne-Ibikoraneza/drone-script-extractor.git
    cd drone-script-extractor
    ```

2.  **Create and Activate a Virtual Environment**:
    ```bash
    python -m venv env
    # On Windows:
    .\env\Scripts\activate
    # On Linux/macOS:
    source env/bin/activate
    ```

3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

---

## ⚙️ Configuration

Create a `.env` file in the root directory based on `.env.example`:

```env
# API Settings
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=INFO

# Cloudinary (Required for Image Extraction)
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret

# Security
CORS_ORIGINS=["*"]
```

---

## 📡 API Usage

### Start the Server

```bash
python -m app.main
```

The API will be available at `http://localhost:8000`. Full docs at `/docs`.

### Extracting Data

#### POST `/extract`

Accepts a JSON payload with either `pdfPath` (local file) or `pdfContent` (base64 string).

**Request (Local File):**
```bash
curl -X POST "http://localhost:8000/extract" \
     -H "Content-Type: application/json" \
     -d '{"pdfPath": "REPORTS/stand_count_report.pdf"}'
```

**Request (Base64 Content):**
```bash
curl -X POST "http://localhost:8000/extract" \
     -H "Content-Type: application/json" \
     -d '{"pdfContent": "JVBERi0xLjQKJ..."}'
```

---

## 🧪 Testing

Run the extraction test script to verify performance across all report types:

```bash
python test_extraction.py
```

---

## 🛠️ Development Guidelines

### Adding a New Report Type

The architecture is designed to be **data-driven**. To add a new Agremo-style report (e.g., Weed Pressure):

1.  Open `app/extractor.py`.
2.  Locate the `ReportTypeConfig.TYPES` registry.
3.  Add a new entry with keywords and level patterns:
    ```python
    "weed_pressure": {
        "keywords": ["WEED PRESSURE", "Weeds"],
        "total_area_patterns": ["total area weed pressure"],
        "levels": [
            {"name": "No Weeds", "severity": "none", "pattern_pct_first": r"...", "pattern_area_first": r"..."},
            # ... additional levels
        ],
        "field_name": "analysis",
    }
    ```
4.  The service will automatically handle the new type in the next `/extract` request.

### Code Style
- Follow PEP 8 guidelines.
- Use type hints for all function signatures.
- Log significant events using the `logger` from `logging`.

### Contribution Process
1.  Create a feature branch.
2.  Write tests in `test_extraction.py` if adding new parsing logic.
3.  Ensure your branch passes all existing tests.
4.  Submit a PR with a detailed description of changes.

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
