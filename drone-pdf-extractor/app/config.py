"""
Configuration management for the drone PDF extraction service.
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
    upload_dir: Optional[str] = None  # Defaults to ./uploads/drone-analysis
    
    # File Processing
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    
    # CORS
    cors_origins: str = "*"  # Comma-separated list of allowed origins

    # CLOUDINARY
    cloudinary_cloud_name: Optional[str] = None
    cloudinary_api_key: Optional[str] = None
    cloudinary_api_secret: Optional[str] = None
    cloudinary_folder: str = "starhawk-map-images"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra="allow"
        
    def get_upload_dir(self) -> str:
        """Get the upload directory path, defaulting if not set."""
        if self.upload_dir:
            return self.upload_dir
        
        # Default to ./uploads/drone-analysis relative to project root
        project_root = Path(__file__).parent.parent.parent
        return str(project_root / "uploads" / "drone-analysis")
    
    def get_cors_origins_list(self) -> list:
        """Parse CORS origins string into a list."""
        if self.cors_origins == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",")]


# Global settings instance
settings = Settings()

