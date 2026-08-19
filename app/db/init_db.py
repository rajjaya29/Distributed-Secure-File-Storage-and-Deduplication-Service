import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Tenant, User, UserRole
from app.db.session import Base, async_session_factory, engine
from app.services.auth_service import get_password_hash

logger = logging.getLogger("storage.init_db")


async def init_db() -> None:
    """Create all database tables and seed default administrator and organization."""
    settings.init_directories()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        # Check if default tenant exists
        stmt = select(Tenant).where(Tenant.name == settings.DEFAULT_TENANT_NAME)
        result = await session.execute(stmt)
        default_tenant = result.scalar_one_or_none()

        if not default_tenant:
            default_tenant = Tenant(
                name=settings.DEFAULT_TENANT_NAME,
                description="Default Primary Organization",
                storage_quota_bytes=50 * 1024 * 1024 * 1024,  # 50 GB
            )
            session.add(default_tenant)
            await session.flush()
            logger.info("Created default tenant: %s", default_tenant.name)

        # Check if default admin user exists
        stmt_user = select(User).where(User.email == settings.DEFAULT_ADMIN_EMAIL)
        result_user = await session.execute(stmt_user)
        admin_user = result_user.scalar_one_or_none()

        if not admin_user:
            admin_user = User(
                tenant_id=default_tenant.id,
                email=settings.DEFAULT_ADMIN_EMAIL,
                hashed_password=get_password_hash(settings.DEFAULT_ADMIN_PASSWORD),
                full_name=settings.DEFAULT_ADMIN_NAME,
                role=UserRole.ADMIN,
                is_active=True,
            )
            session.add(admin_user)
            await session.commit()
            logger.info("Created default admin user: %s", admin_user.email)
        else:
            await session.commit()
