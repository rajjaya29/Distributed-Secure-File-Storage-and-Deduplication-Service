from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, require_roles
from app.db.models import Tenant, User, UserRole
from app.db.session import get_db
from app.services.auth_service import get_password_hash

router = APIRouter(tags=["User & Tenant Management"])


class CreateUserRequest(BaseModel):
    email: str
    password: str
    full_name: str
    role: UserRole = UserRole.MEMBER
    tenant_id: str


@router.get("/users")
async def list_users(
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """List all registered users (Admin only)."""
    stmt = select(User).options(selectinload(User.tenant)).order_by(User.created_at.desc())
    result = await db.execute(stmt)
    users = result.scalars().all()

    return [
        {
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role.value,
            "is_active": u.is_active,
            "tenant": {
                "id": u.tenant.id,
                "name": u.tenant.name,
            } if u.tenant else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.get("/tenants")
async def list_tenants(
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """List all tenant organizations (Admin only)."""
    stmt = select(Tenant).order_by(Tenant.created_at.desc())
    result = await db.execute(stmt)
    tenants = result.scalars().all()

    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "storage_quota_bytes": t.storage_quota_bytes,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tenants
    ]
