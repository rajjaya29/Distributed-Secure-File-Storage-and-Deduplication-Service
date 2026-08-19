import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App Information
    APP_NAME: str = "Distributed Secure File Storage & Deduplication Service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    
    # Server Host & Port
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Base Storage Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    BLOCKS_DIR: Path = DATA_DIR / "blocks"
    DATABASE_PATH: Path = DATA_DIR / "storage_service.db"
    
    # Database
    DATABASE_URL: str = f"sqlite+aiosqlite:///{DATABASE_PATH}"
    
    # Content-Addressable Storage (CAS) Configuration
    # 64 KB (65,536 bytes) chunk size for fine-grained deduplication
    DEFAULT_CHUNK_SIZE: int = 64 * 1024
    
    # JWT Security & Auth
    SECRET_KEY: str = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # Encryption at rest
    # 32-byte key in hex or generated
    ENABLE_BLOCK_ENCRYPTION: bool = False
    MASTER_ENCRYPTION_KEY: str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    
    # Rate Limiting
    RATE_LIMIT_DEFAULT: str = "100/minute"
    RATE_LIMIT_UPLOAD: str = "30/minute"
    
    # Initial Admin Seed
    DEFAULT_ADMIN_EMAIL: str = "admin@storage.local"
    DEFAULT_ADMIN_PASSWORD: str = "AdminSecure2026!"
    DEFAULT_ADMIN_NAME: str = "System Administrator"
    DEFAULT_TENANT_NAME: str = "Default Organization"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def init_directories(self) -> None:
        """Ensure necessary data and storage directories exist."""
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.BLOCKS_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.init_directories()
