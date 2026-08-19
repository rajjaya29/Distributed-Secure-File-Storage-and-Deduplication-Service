from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, CASBlock, FileMetadata, Tenant, User
from app.services.cas_engine import cas_engine


class TelemetryService:
    def __init__(self):
        # In-memory fast telemetry buffer for latency & throughput percentiles
        self._latencies: list[float] = []
        self._max_latencies_samples = 1000

    def record_latency(self, latency_ms: float) -> None:
        """Record endpoint response latency."""
        self._latencies.append(latency_ms)
        if len(self._latencies) > self._max_latencies_samples:
            self._latencies.pop(0)

    def get_latency_percentiles(self) -> dict[str, float]:
        """Calculate P50, P95, and P99 latency in milliseconds."""
        if not self._latencies:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0, "samples": 0}

        sorted_lat = sorted(self._latencies)
        n = len(sorted_lat)

        p50 = sorted_lat[int(n * 0.50)]
        p95 = sorted_lat[min(int(n * 0.95), n - 1)]
        p99 = sorted_lat[min(int(n * 0.99), n - 1)]
        avg = sum(sorted_lat) / n

        return {
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "p99": round(p99, 2),
            "avg": round(avg, 2),
            "samples": n,
        }

    async def get_dashboard_summary(self, session: AsyncSession) -> dict[str, Any]:
        """
        Aggregate comprehensive storage and operational statistics.
        """
        # 1. CAS Engine Stats
        cas_stats = await cas_engine.get_storage_stats(session)

        # 2. File & Tenant Stats
        files_count_stmt = select(func.count(FileMetadata.id)).where(FileMetadata.is_deleted == False)
        total_files = (await session.execute(files_count_stmt)).scalar_one()

        tenants_count_stmt = select(func.count(Tenant.id))
        total_tenants = (await session.execute(tenants_count_stmt)).scalar_one()

        users_count_stmt = select(func.count(User.id))
        total_users = (await session.execute(users_count_stmt)).scalar_one()

        # 3. Latency Telemetry
        latency_stats = self.get_latency_percentiles()

        # 4. Recent Audit Activity
        audit_stmt = (
            select(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .limit(10)
        )
        recent_audits = (await session.execute(audit_stmt)).scalars().all()
        audit_list = [
            {
                "id": a.id,
                "action": a.action,
                "resource_type": a.resource_type,
                "resource_id": a.resource_id,
                "status": a.status,
                "latency_ms": a.latency_ms,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in recent_audits
        ]

        # 5. Top 5 Most Referenced Blocks (Hot Blocks)
        hot_blocks_stmt = (
            select(CASBlock)
            .order_by(CASBlock.ref_count.desc())
            .limit(5)
        )
        hot_blocks = (await session.execute(hot_blocks_stmt)).scalars().all()
        hot_block_list = [
            {
                "hash": b.hash,
                "size_bytes": b.size_bytes,
                "ref_count": b.ref_count,
                "savings_bytes": (b.ref_count - 1) * b.size_bytes,
            }
            for b in hot_blocks
        ]

        return {
            "cas_storage": cas_stats,
            "total_files": total_files,
            "total_tenants": total_tenants,
            "total_users": total_users,
            "latency": latency_stats,
            "hot_blocks": hot_block_list,
            "recent_activity": audit_list,
        }


telemetry_service = TelemetryService()
