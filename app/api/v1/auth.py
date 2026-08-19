from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import Tenant, User, UserRole
from app.db.session import get_db
from app.services.auth_service import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str
    tenant_name: Optional[str] = None
    role: Optional[UserRole] = UserRole.MEMBER


class RefreshTokenRequest(BaseModel):
    refresh_token: str


@router.post("/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate user with email and password, returning JWT access and refresh tokens."""
    stmt = select(User).where(User.email == req.email.lower())
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    access_token = create_access_token(user.id, user.tenant_id, user.email, user.role.value)
    refresh_token = create_refresh_token(user.id, user.tenant_id, user.email, user.role.value)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value,
            "tenant_id": user.tenant_id,
        },
    }


@router.post("/register")
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user account and attach to an organization/tenant."""
    # Check if email is already taken
    stmt = select(User).where(User.email == req.email.lower())
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists",
        )

    # Get or create tenant
    tenant_name = req.tenant_name or "Default Organization"
    stmt_tenant = select(Tenant).where(Tenant.name == tenant_name)
    tenant = (await db.execute(stmt_tenant)).scalar_one_or_none()

    if not tenant:
        tenant = Tenant(name=tenant_name, description=f"Tenant for {tenant_name}")
        db.add(tenant)
        await db.flush()

    new_user = User(
        tenant_id=tenant.id,
        email=req.email.lower(),
        hashed_password=get_password_hash(req.password),
        full_name=req.full_name,
        role=req.role or UserRole.MEMBER,
        is_active=True,
    )
    db.add(new_user)
    await db.commit()

    access_token = create_access_token(new_user.id, tenant.id, new_user.email, new_user.role.value)
    refresh_token = create_refresh_token(new_user.id, tenant.id, new_user.email, new_user.role.value)

    return {
        "message": "User registered successfully",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": new_user.id,
            "email": new_user.email,
            "full_name": new_user.full_name,
            "role": new_user.role.value,
            "tenant_id": tenant.id,
            "tenant_name": tenant.name,
        },
    }


@router.post("/refresh")
async def refresh_token(req: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    """Generate a new access token from a valid refresh token."""
    payload = decode_token(req.refresh_token)
    if not payload or payload.type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    stmt = select(User).where(User.id == payload.sub, User.is_active == True)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    new_access_token = create_access_token(user.id, user.tenant_id, user.email, user.role.value)
    return {
        "access_token": new_access_token,
        "token_type": "bearer",
    }


@router.get("/me")
async def get_my_profile(current_user: User = Depends(get_current_user)):
    """Retrieve details of the currently authenticated user."""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role.value,
        "tenant": {
            "id": current_user.tenant.id,
            "name": current_user.tenant.name,
            "storage_quota_bytes": current_user.tenant.storage_quota_bytes,
        },
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
    }
