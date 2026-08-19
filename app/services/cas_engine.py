import asyncio
import hashlib
import os
from pathlib import Path
from typing import Optional, Tuple
import aiofiles
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import CASBlock, get_utc_now
from app.services.crypto_service import crypto_service


class CASEngine:
    """
    Content-Addressable Storage (CAS) Engine.
    Uses SHA-256 hashing to identify, store, and deduplicate file blocks.
    Organizes files into a 2-level directory tree (e.g. data/blocks/a1/b2/a1b2c3d4...).
    """

    def __init__(self, blocks_dir: Path = settings.BLOCKS_DIR):
        self.blocks_dir = Path(blocks_dir)
        self.blocks_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def compute_hash(self, data: bytes) -> str:
        """Compute SHA-256 hash string for raw byte chunk."""
        return hashlib.sha256(data).hexdigest()

    def get_block_path(self, block_hash: str) -> Path:
        """
        Get hierarchical file path for a block hash.
        Example: data/blocks/fa/4b/fa4b2389...
        """
        prefix1 = block_hash[:2]
        prefix2 = block_hash[2:4]
        directory = Path(self.blocks_dir) / prefix1 / prefix2
        directory.mkdir(parents=True, exist_ok=True)
        return directory / block_hash

    async def store_block(
        self, session: AsyncSession, chunk_data: bytes, is_encrypted: bool = settings.ENABLE_BLOCK_ENCRYPTION
    ) -> tuple[str, int, bool]:
        """
        Store a block in CAS.
        If the block hash already exists in metadata, increment reference count without writing to disk.
        Returns: (block_hash, size_bytes, is_deduplicated)
        """
        block_hash = self.compute_hash(chunk_data)
        size_bytes = len(chunk_data)

        # Check if block exists in DB
        async with self._lock:
            stmt = select(CASBlock).where(CASBlock.hash == block_hash)
            result = await session.execute(stmt)
            existing_block = result.scalar_one_or_none()

            if existing_block:
                # Deduplication hit! Increment reference counter
                existing_block.ref_count += 1
                existing_block.last_accessed_at = get_utc_now()
                await session.flush()
                return block_hash, size_bytes, True

            # Deduplication miss: write new block to disk
            block_path = self.get_block_path(block_hash)
            data_to_write = chunk_data
            if is_encrypted:
                data_to_write = crypto_service.encrypt_chunk(chunk_data)

            async with aiofiles.open(block_path, "wb") as f:
                await f.write(data_to_write)

            new_block = CASBlock(
                hash=block_hash,
                size_bytes=size_bytes,
                ref_count=1,
                is_encrypted=is_encrypted,
                created_at=get_utc_now(),
                last_accessed_at=get_utc_now(),
            )
            session.add(new_block)
            await session.flush()
            return block_hash, size_bytes, False

    async def read_block(self, session: AsyncSession, block_hash: str) -> bytes:
        """
        Read block bytes from CAS disk storage by hash.
        Decrypts payload if stored with encryption.
        """
        stmt = select(CASBlock).where(CASBlock.hash == block_hash)
        result = await session.execute(stmt)
        block = result.scalar_one_or_none()

        if not block:
            raise FileNotFoundError(f"CAS block with hash {block_hash} not found in database registry")

        block_path = self.get_block_path(block_hash)
        if not block_path.exists():
            raise FileNotFoundError(f"CAS block file {block_path} missing on physical disk")

        async with aiofiles.open(block_path, "rb") as f:
            raw_data = await f.read()

        # Update last accessed timestamp
        block.last_accessed_at = get_utc_now()
        await session.flush()

        if block.is_encrypted:
            return crypto_service.decrypt_chunk(raw_data)
        return raw_data

    async def release_block(self, session: AsyncSession, block_hash: str) -> int:
        """
        Decrement reference count of a block.
        If reference count drops to 0 or below, purge the block record and remove physical file from disk.
        Returns: Remaining reference count
        """
        async with self._lock:
            stmt = select(CASBlock).where(CASBlock.hash == block_hash)
            result = await session.execute(stmt)
            block = result.scalar_one_or_none()

            if not block:
                return 0

            block.ref_count -= 1
            remaining = block.ref_count

            if remaining <= 0:
                # Remove from database
                await session.delete(block)
                await session.flush()

                # Remove from disk
                block_path = self.get_block_path(block_hash)
                if block_path.exists():
                    try:
                        os.remove(block_path)
                    except OSError:
                        pass
                return 0
            else:
                await session.flush()
                return remaining

    async def garbage_collect_orphans(self, session: AsyncSession) -> dict[str, int]:
        """
        Garbage collect any orphaned blocks on disk and clean up zero-reference records.
        """
        async with self._lock:
            # Query all active block hashes
            stmt = select(CASBlock.hash)
            result = await session.execute(stmt)
            db_hashes = set(result.scalars().all())

            orphans_deleted = 0
            bytes_reclaimed = 0

            # Traverse directory
            for root, _, files in os.walk(self.blocks_dir):
                for filename in files:
                    if len(filename) == 64:  # SHA-256 hash length
                        if filename not in db_hashes:
                            file_path = Path(root) / filename
                            try:
                                size = file_path.stat().st_size
                                os.remove(file_path)
                                orphans_deleted += 1
                                bytes_reclaimed += size
                            except OSError:
                                pass

            # Delete any blocks with ref_count <= 0
            del_stmt = delete(CASBlock).where(CASBlock.ref_count <= 0)
            await session.execute(del_stmt)
            await session.flush()

            return {
                "orphaned_blocks_deleted": orphans_deleted,
                "bytes_reclaimed": bytes_reclaimed,
            }

    async def get_storage_stats(self, session: AsyncSession) -> dict:
        """
        Compute aggregate CAS storage metrics and deduplication efficiency.
        """
        # Unique physical blocks and physical stored bytes
        stats_stmt = select(
            func.count(CASBlock.hash).label("unique_blocks_count"),
            func.coalesce(func.sum(CASBlock.size_bytes), 0).label("physical_bytes_stored"),
            func.coalesce(func.sum(CASBlock.size_bytes * CASBlock.ref_count), 0).label("virtual_bytes_referenced"),
            func.coalesce(func.sum(CASBlock.ref_count), 0).label("total_block_references"),
        )
        result = await session.execute(stats_stmt)
        row = result.mappings().one()

        unique_blocks = row["unique_blocks_count"]
        physical_bytes = row["physical_bytes_stored"]
        virtual_bytes = row["virtual_bytes_referenced"]
        total_references = row["total_block_references"]

        bytes_saved = max(0, virtual_bytes - physical_bytes)
        dedup_ratio = round(virtual_bytes / physical_bytes, 2) if physical_bytes > 0 else 1.0
        dedup_percentage = round((bytes_saved / virtual_bytes) * 100, 2) if virtual_bytes > 0 else 0.0

        return {
            "unique_blocks_count": unique_blocks,
            "total_block_references": total_references,
            "physical_bytes_stored": physical_bytes,
            "virtual_bytes_referenced": virtual_bytes,
            "bytes_saved": bytes_saved,
            "deduplication_ratio": dedup_ratio,
            "deduplication_percentage": dedup_percentage,
        }


cas_engine = CASEngine()
