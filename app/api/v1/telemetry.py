from typing import Any, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.db.models import AuditLog, CASBlock, User, UserRole
from app.db.session import get_db
from app.services.cas_engine import cas_engine
from app.services.telemetry_service import telemetry_service

router = APIRouter(prefix="/telemetry", tags=["Observability & Telemetry"])


@router.get("/stats")
async def get_storage_and_system_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve full telemetry dashboard data including storage deduplication and API latency."""
    return await telemetry_service.get_dashboard_summary(db)


@router.post("/gc")
async def trigger_garbage_collection(
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Trigger administrative CAS garbage collection to purge unreferenced or orphaned disk blocks."""
    result = await cas_engine.garbage_collect_orphans(db)
    return {
        "status": "success",
        "details": result,
    }


@router.get("/blocks")
async def list_cas_blocks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List physical Content-Addressable Storage (CAS) blocks and their reference counts."""
    stmt = (
        select(CASBlock)
        .order_by(desc(CASBlock.ref_count), desc(CASBlock.last_accessed_at))
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    blocks = result.scalars().all()

    return [
        {
            "hash": b.hash,
            "size_bytes": b.size_bytes,
            "ref_count": b.ref_count,
            "is_encrypted": b.is_encrypted,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "last_accessed_at": b.last_accessed_at.isoformat() if b.last_accessed_at else None,
        }
        for b in blocks
    ]


@router.get("/audit")
async def get_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve structured audit trail."""
    stmt = select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)
    result = await db.execute(stmt)
    logs = result.scalars().all()

    return [
        {
            "id": l.id,
            "action": l.action,
            "resource_type": l.resource_type,
            "resource_id": l.resource_id,
            "status": l.status,
            "latency_ms": l.latency_ms,
            "details": l.details,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in logs
    ]
