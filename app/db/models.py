from datetime import datetime, timezone
import enum
import uuid
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(128), unique=True, nullable=False, index=True)
    description = Column(String(255), nullable=True)
    storage_quota_bytes = Column(Integer, default=10 * 1024 * 1024 * 1024)  # 10 GB default
    created_at = Column(DateTime(timezone=True), default=get_utc_now)
    is_active = Column(Boolean, default=True)

    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    files = relationship("FileMetadata", back_populates="tenant", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(128), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.MEMBER, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now)

    tenant = relationship("Tenant", back_populates="users")
    files = relationship("FileMetadata", back_populates="owner", cascade="all, delete-orphan")


class CASBlock(Base):
    """
    Content-Addressable Storage (CAS) Block Table.
    Each unique chunk/block of data has exactly one entry, identified by its SHA-256 hash.
    ref_count tracks how many FileChunkMap rows point to this block.
    """
    __tablename__ = "cas_blocks"

    hash = Column(String(64), primary_key=True, index=True)  # SHA-256 hex string (64 chars)
    size_bytes = Column(Integer, nullable=False)
    ref_count = Column(Integer, default=1, nullable=False, index=True)
    is_encrypted = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now)
    last_accessed_at = Column(DateTime(timezone=True), default=get_utc_now)

    chunk_mappings = relationship("FileChunkMap", back_populates="block")


class FileMetadata(Base):
    """
    Relational file metadata model.
    Contains high-level file properties and references the manifest of constituent CAS blocks.
    """
    __tablename__ = "files"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    filename = Column(String(255), nullable=False, index=True)
    content_type = Column(String(128), default="application/octet-stream")
    raw_size_bytes = Column(Integer, nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)  # Complete file SHA-256 manifest hash
    chunk_count = Column(Integer, default=1, nullable=False)
    is_encrypted = Column(Boolean, default=False)
    
    # Soft deletion flag
    is_deleted = Column(Boolean, default=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=get_utc_now)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now)

    tenant = relationship("Tenant", back_populates="files")
    owner = relationship("User", back_populates="files")
    chunks = relationship("FileChunkMap", back_populates="file", cascade="all, delete-orphan", order_by="FileChunkMap.chunk_index")
    shares = relationship("FileShare", back_populates="file", cascade="all, delete-orphan")


class FileChunkMap(Base):
    """
    Ordered mapping between a File and its CAS Blocks.
    Enables reassembly of any file from its distributed chunk blocks.
    """
    __tablename__ = "file_chunk_maps"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    file_id = Column(String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)  # 0, 1, 2, ...
    block_hash = Column(String(64), ForeignKey("cas_blocks.hash", ondelete="RESTRICT"), nullable=False, index=True)
    chunk_size_bytes = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("file_id", "chunk_index", name="uq_file_chunk_index"),
        Index("idx_file_chunk_lookup", "file_id", "chunk_index"),
    )

    file = relationship("FileMetadata", back_populates="chunks")
    block = relationship("CASBlock", back_populates="chunk_mappings")


class FileShare(Base):
    """
    Secure file sharing tokens with optional expiration.
    """
    __tablename__ = "file_shares"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    file_id = Column(String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    share_token = Column(String(64), unique=True, nullable=False, index=True)
    can_download = Column(Boolean, default=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    download_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=get_utc_now)

    file = relationship("FileMetadata", back_populates="shares")


class AuditLog(Base):
    """
    Structured audit and telemetry log for all operations.
    """
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), nullable=True, index=True)
    user_id = Column(String(36), nullable=True, index=True)
    action = Column(String(64), nullable=False, index=True)  # UPLOAD, DOWNLOAD, DELETE, SHARE, LOGIN
    resource_type = Column(String(64), default="file")
    resource_id = Column(String(64), nullable=True)
    status = Column(String(32), default="SUCCESS")
    ip_address = Column(String(64), nullable=True)
    latency_ms = Column(Float, nullable=True)
    details = Column(Text, nullable=True)  # JSON metadata string
    created_at = Column(DateTime(timezone=True), default=get_utc_now, index=True)
