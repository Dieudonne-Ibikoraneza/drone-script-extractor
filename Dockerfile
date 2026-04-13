# Use Python 3.11-slim as the base image for a small footprint
FROM python:3.11-slim

# Install system dependencies for OCR and PDF processing
# - tesseract-ocr: The OCR engine
# - tesseract-ocr-eng: English language data for Tesseract
# - poppler-utils: Required by pdf2image for high-quality rendering
# - libgl1: Required by some image processing libraries
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    libgl1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create uploads directory
RUN mkdir -p uploads

# Expose the port the app runs on
EXPOSE 8000

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Run the application using uvicorn
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
