from collections.abc import AsyncGenerator
from typing import Any, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, require_roles
from app.config import settings
from app.db.models import CASBlock, FileChunkMap, FileMetadata, User, UserRole
from app.db.session import get_db
from app.services.file_service import file_service

router = APIRouter(prefix="/files", tags=["File Storage & Deduplication"])


class ShareRequest(BaseModel):
    expires_in_hours: Optional[int] = 72


async def async_file_chunk_reader(upload_file: UploadFile, read_chunk_size: int = 65536) -> AsyncGenerator[bytes, None]:
    """Helper generator to stream bytes from an uploaded file asynchronously."""
    while True:
        chunk = await upload_file.read(read_chunk_size)
        if not chunk:
            break
        yield chunk


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    chunk_size: Optional[int] = Query(default=settings.DEFAULT_CHUNK_SIZE, ge=4096, le=10 * 1024 * 1024),
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.MEMBER])),
    db: AsyncSession = Depends(get_db),
):
    """
    Stream and upload a file in chunks into Content-Addressable Storage (CAS).
    Calculates SHA-256 block hashes and deduplicates identical blocks across all uploads.
    """
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required")

    result = await file_service.ingest_file_stream(
        session=db,
        user=current_user,
        filename=file.filename,
        content_type=file.content_type or "application/octet-stream",
        stream=async_file_chunk_reader(file),
        chunk_size=chunk_size or settings.DEFAULT_CHUNK_SIZE,
    )
    return result


@router.get("")
async def list_files(
    search: Optional[str] = Query(None, description="Filter by filename"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List accessible files with metadata, owner details, and pagination."""
    files, total = await file_service.list_files(
        session=db,
        user=current_user,
        search=search,
        limit=limit,
        offset=offset,
    )
    return {
        "items": files,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{file_id}")
async def get_file_details(
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed file metadata and constituent CAS chunk block manifest."""
    file_obj = await file_service.get_file_metadata(db, file_id, current_user)
    if not file_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    # Fetch chunk mappings with block reference details
    stmt = (
        select(FileChunkMap, CASBlock)
        .join(CASBlock, FileChunkMap.block_hash == CASBlock.hash)
        .where(FileChunkMap.file_id == file_id)
        .order_by(FileChunkMap.chunk_index.asc())
    )
    result = await db.execute(stmt)
    chunks_data = []
    for chunk_map, block in result.all():
        chunks_data.append({
            "chunk_index": chunk_map.chunk_index,
            "block_hash": chunk_map.block_hash,
            "size_bytes": chunk_map.chunk_size_bytes,
            "block_ref_count": block.ref_count,
            "is_deduplicated": block.ref_count > 1,
        })

    return {
        "id": file_obj.id,
        "filename": file_obj.filename,
        "content_type": file_obj.content_type,
        "raw_size_bytes": file_obj.raw_size_bytes,
        "file_hash": file_obj.file_hash,
        "chunk_count": file_obj.chunk_count,
        "is_encrypted": file_obj.is_encrypted,
        "created_at": file_obj.created_at.isoformat(),
        "owner": {
            "id": file_obj.owner.id if file_obj.owner else None,
            "name": file_obj.owner.full_name if file_obj.owner else "Unknown",
        },
        "chunks": chunks_data,
    }


@router.get("/{file_id}/download")
async def download_file(
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream and reconstruct file chunks from CAS storage."""
    file_obj = await file_service.get_file_metadata(db, file_id, current_user)
    if not file_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    headers = {
        "Content-Disposition": f'attachment; filename="{file_obj.filename}"',
        "Content-Length": str(file_obj.raw_size_bytes),
        "X-File-Hash-SHA256": file_obj.file_hash,
    }

    return StreamingResponse(
        file_service.stream_file_chunks(db, file_id),
        media_type=file_obj.content_type or "application/octet-stream",
        headers=headers,
    )


@router.delete("/{file_id}")
async def delete_file(
    file_id: str,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.MEMBER])),
    db: AsyncSession = Depends(get_db),
):
    """
    Securely delete file and release CAS block references.
    Orphaned blocks (ref_count == 0) are immediately purged from disk.
    """
    try:
        result = await file_service.delete_file(db, file_id, current_user)
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")


@router.post("/{file_id}/share")
async def create_share_link(
    file_id: str,
    req: ShareRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a shareable public download link with configurable expiration."""
    try:
        return await file_service.create_share_link(
            session=db,
            file_id=file_id,
            user=current_user,
            expires_hours=req.expires_in_hours,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")


@router.get("/shared/{share_token}/download")
async def download_shared_file(
    share_token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public download endpoint for shared files."""
    try:
        file_obj, share = await file_service.get_shared_file(db, share_token)
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(e))

    headers = {
        "Content-Disposition": f'attachment; filename="{file_obj.filename}"',
        "Content-Length": str(file_obj.raw_size_bytes),
        "X-File-Hash-SHA256": file_obj.file_hash,
    }

    return StreamingResponse(
        file_service.stream_file_chunks(db, file_obj.id),
        media_type=file_obj.content_type or "application/octet-stream",
        headers=headers,
    )
