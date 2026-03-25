"""
Configuration management for the unified PDF extraction service.
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    # Upload Directory
    upload_dir: Optional[str] = None

    # File Processing
    max_file_size: int = 10 * 1024 * 1024  # 10MB

    # CORS
    cors_origins: str = "*"

    # Cloudinary
    cloudinary_cloud_name: Optional[str] = None
    cloudinary_api_key: Optional[str] = None
    cloudinary_api_secret: Optional[str] = None
    cloudinary_folder: str = "starhawk-report-images"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"

    def get_upload_dir(self) -> str:
        if self.upload_dir:
            return self.upload_dir
        project_root = Path(__file__).parent.parent
        return str(project_root / "uploads")

    def get_cors_origins_list(self) -> list:
        if self.cors_origins == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",")]


# Global settings instance
settings = Settings()
