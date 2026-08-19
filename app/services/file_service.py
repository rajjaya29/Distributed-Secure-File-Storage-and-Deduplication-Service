from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from typing import Any, Optional
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import AuditLog, FileChunkMap, FileMetadata, FileShare, Tenant, User, UserRole, get_utc_now
from app.services.cas_engine import cas_engine


class FileService:
    async def ingest_file_stream(
        self,
        session: AsyncSession,
        user: User,
        filename: str,
        content_type: str,
        stream: AsyncGenerator[bytes, None],
        chunk_size: int = settings.DEFAULT_CHUNK_SIZE,
        is_encrypted: bool = settings.ENABLE_BLOCK_ENCRYPTION,
    ) -> dict[str, Any]:
        """
        Stream an uploaded file in chunks, compute SHA-256 hashes, deduplicate blocks via CAS,
        and link chunks in the relational metadata layer.
        """
        file_hasher = hashlib.sha256()
        total_raw_bytes = 0
        chunk_index = 0
        chunk_maps = []
        dedup_hits = 0
        dedup_misses = 0
        new_bytes_stored = 0

        # Buffer for slicing exact chunk sizes from arbitrary stream chunk sizes
        buffer = bytearray()

        file_obj = FileMetadata(
            tenant_id=user.tenant_id,
            owner_id=user.id,
            filename=filename,
            content_type=content_type or "application/octet-stream",
            raw_size_bytes=0,
            file_hash="",
            chunk_count=0,
            is_encrypted=is_encrypted,
        )
        session.add(file_obj)
        await session.flush()  # Generate file_obj.id

        async for chunk_bytes in stream:
            if not chunk_bytes:
                continue
            file_hasher.update(chunk_bytes)
            total_raw_bytes += len(chunk_bytes)
            buffer.extend(chunk_bytes)

            while len(buffer) >= chunk_size:
                block_data = bytes(buffer[:chunk_size])
                del buffer[:chunk_size]

                block_hash, size, is_dedup = await cas_engine.store_block(
                    session, block_data, is_encrypted=is_encrypted
                )
                if is_dedup:
                    dedup_hits += 1
                else:
                    dedup_misses += 1
                    new_bytes_stored += size

                chunk_map = FileChunkMap(
                    file_id=file_obj.id,
                    chunk_index=chunk_index,
                    block_hash=block_hash,
                    chunk_size_bytes=size,
                )
                chunk_maps.append(chunk_map)
                chunk_index += 1

        # Process any remaining residual bytes in the buffer
        if len(buffer) > 0:
            block_data = bytes(buffer)
            buffer.clear()

            block_hash, size, is_dedup = await cas_engine.store_block(
                session, block_data, is_encrypted=is_encrypted
            )
            if is_dedup:
                dedup_hits += 1
            else:
                dedup_misses += 1
                new_bytes_stored += size

            chunk_map = FileChunkMap(
                file_id=file_obj.id,
                chunk_index=chunk_index,
                block_hash=block_hash,
                chunk_size_bytes=size,
            )
            chunk_maps.append(chunk_map)
            chunk_index += 1

        # Finalize file metadata
        file_hash_hex = file_hasher.hexdigest()
        file_obj.raw_size_bytes = total_raw_bytes
        file_obj.file_hash = file_hash_hex
        file_obj.chunk_count = chunk_index

        for cm in chunk_maps:
            session.add(cm)

        # Audit log entry
        savings_bytes = total_raw_bytes - new_bytes_stored
        savings_pct = round((savings_bytes / total_raw_bytes) * 100, 2) if total_raw_bytes > 0 else 0.0

        audit = AuditLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action="UPLOAD",
            resource_type="file",
            resource_id=file_obj.id,
            status="SUCCESS",
            details=json.dumps({
                "filename": filename,
                "raw_size_bytes": total_raw_bytes,
                "file_hash": file_hash_hex,
                "chunk_count": chunk_index,
                "dedup_hits": dedup_hits,
                "dedup_misses": dedup_misses,
                "new_bytes_stored": new_bytes_stored,
                "savings_pct": savings_pct,
            }),
        )
        session.add(audit)
        await session.flush()

        return {
            "file_id": file_obj.id,
            "filename": file_obj.filename,
            "raw_size_bytes": total_raw_bytes,
            "file_hash": file_hash_hex,
            "chunk_count": chunk_index,
            "dedup_hits": dedup_hits,
            "dedup_misses": dedup_misses,
            "new_bytes_stored": new_bytes_stored,
            "savings_bytes": savings_bytes,
            "savings_percentage": savings_pct,
            "is_encrypted": is_encrypted,
            "created_at": file_obj.created_at.isoformat(),
        }

    async def get_file_metadata(
        self, session: AsyncSession, file_id: str, user: Optional[User] = None
    ) -> Optional[FileMetadata]:
        """Fetch file metadata ensuring tenant/user permissions."""
        stmt = (
            select(FileMetadata)
            .where(FileMetadata.id == file_id, FileMetadata.is_deleted == False)
            .options(selectinload(FileMetadata.owner), selectinload(FileMetadata.chunks))
        )
        result = await session.execute(stmt)
        file_obj = result.scalar_one_or_none()

        if not file_obj:
            return None

        # Check tenant access if user provided
        if user and user.role != UserRole.ADMIN:
            if file_obj.tenant_id != user.tenant_id:
                return None

        return file_obj

    async def stream_file_chunks(
        self, session: AsyncSession, file_id: str
    ) -> AsyncGenerator[bytes, None]:
        """
        Yield reconstructed byte chunks of a file in correct sequential order.
        """
        stmt = (
            select(FileChunkMap)
            .where(FileChunkMap.file_id == file_id)
            .order_by(FileChunkMap.chunk_index.asc())
        )
        result = await session.execute(stmt)
        chunks = result.scalars().all()

        for chunk in chunks:
            chunk_data = await cas_engine.read_block(session, chunk.block_hash)
            yield chunk_data

    async def delete_file(self, session: AsyncSession, file_id: str, user: User) -> dict[str, Any]:
        """
        Securely delete a file and decrement reference counts of all underlying CAS blocks.
        Blocks with ref_count == 0 are automatically purged from disk.
        """
        file_obj = await self.get_file_metadata(session, file_id, user)
        if not file_obj:
            raise FileNotFoundError(f"File {file_id} not found or permission denied")

        if user.role != UserRole.ADMIN and file_obj.owner_id != user.id and file_obj.tenant_id != user.tenant_id:
            raise PermissionError("Unauthorized to delete this file")

        # Get all chunk block hashes before deleting metadata
        stmt = select(FileChunkMap.block_hash).where(FileChunkMap.file_id == file_id)
        result = await session.execute(stmt)
        block_hashes = result.scalars().all()

        filename = file_obj.filename
        tenant_id = user.tenant_id
        user_id = user.id

        # First delete file metadata and its chunk maps to release FK constraints
        await session.delete(file_obj)
        await session.flush()

        purged_blocks = 0
        retained_blocks = 0

        # Decrement ref count for each block and purge zero-ref blocks
        for b_hash in block_hashes:
            rem = await cas_engine.release_block(session, b_hash)
            if rem == 0:
                purged_blocks += 1
            else:
                retained_blocks += 1

        audit = AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action="DELETE",
            resource_type="file",
            resource_id=file_id,
            status="SUCCESS",
            details=json.dumps({
                "filename": filename,
                "purged_blocks": purged_blocks,
                "retained_blocks": retained_blocks,
            }),
        )
        session.add(audit)
        await session.flush()

        return {
            "file_id": file_id,
            "filename": filename,
            "purged_blocks": purged_blocks,
            "retained_blocks": retained_blocks,
            "message": "File deleted and storage references cleaned successfully",
        }

    async def list_files(
        self,
        session: AsyncSession,
        user: User,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List tenant files with search, pagination, and owner metadata."""
        query = select(FileMetadata).where(FileMetadata.is_deleted == False)

        if user.role != UserRole.ADMIN:
            query = query.where(FileMetadata.tenant_id == user.tenant_id)

        if search:
            query = query.where(FileMetadata.filename.ilike(f"%{search}%"))

        count_stmt = select(func.count()).select_from(query.subquery())
        total_count = (await session.execute(count_stmt)).scalar_one()

        query = (
            query.options(selectinload(FileMetadata.owner), selectinload(FileMetadata.shares))
            .order_by(desc(FileMetadata.created_at))
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(query)
        files = result.scalars().all()

        file_list = []
        for f in files:
            file_list.append({
                "id": f.id,
                "filename": f.filename,
                "content_type": f.content_type,
                "raw_size_bytes": f.raw_size_bytes,
                "file_hash": f.file_hash,
                "chunk_count": f.chunk_count,
                "is_encrypted": f.is_encrypted,
                "owner": {
                    "id": f.owner.id if f.owner else None,
                    "name": f.owner.full_name if f.owner else "Unknown",
                    "email": f.owner.email if f.owner else "Unknown",
                },
                "shares_count": len(f.shares),
                "created_at": f.created_at.isoformat() if f.created_at else None,
                "updated_at": f.updated_at.isoformat() if f.updated_at else None,
            })

        return file_list, total_count

    async def create_share_link(
        self, session: AsyncSession, file_id: str, user: User, expires_hours: Optional[int] = 72
    ) -> dict[str, Any]:
        """Generate a secure expiring share token for a file."""
        file_obj = await self.get_file_metadata(session, file_id, user)
        if not file_obj:
            raise FileNotFoundError(f"File {file_id} not found")

        token = secrets.token_urlsafe(32)
        expires_at = None
        if expires_hours:
            expires_at = get_utc_now() + timedelta(hours=expires_hours)

        share = FileShare(
            file_id=file_id,
            created_by=user.id,
            share_token=token,
            can_download=True,
            expires_at=expires_at,
        )
        session.add(share)
        await session.flush()

        return {
            "share_token": token,
            "file_id": file_id,
            "filename": file_obj.filename,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "share_url": f"/api/v1/files/shared/{token}/download",
        }

    async def get_shared_file(self, session: AsyncSession, share_token: str) -> tuple[FileMetadata, FileShare]:
        """Validate share token and return corresponding file."""
        stmt = (
            select(FileShare)
            .where(FileShare.share_token == share_token)
            .options(selectinload(FileShare.file))
        )
        result = await session.execute(stmt)
        share = result.scalar_one_or_none()

        if not share:
            raise FileNotFoundError("Share link is invalid or expired")

        if share.expires_at:
            exp = share.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < get_utc_now():
                raise ValueError("Share link has expired")

        if not share.file or share.file.is_deleted:
            raise FileNotFoundError("Target file is no longer available")

        # Increment download count
        share.download_count += 1
        await session.flush()

        return share.file, share


file_service = FileService()
