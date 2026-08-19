import asyncio
import os
import shutil
import tempfile
from typing import AsyncGenerator
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Tenant, User, UserRole
from app.db.session import Base, get_db
from app.main import app
from app.services.auth_service import create_access_token, get_password_hash
from app.services.cas_engine import cas_engine


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_env():
    """Create an isolated temporary environment with database and storage directory."""
    temp_dir = tempfile.mkdtemp()
    test_db_path = os.path.join(temp_dir, "test_storage.db")
    test_blocks_dir = os.path.join(temp_dir, "blocks")
    os.makedirs(test_blocks_dir, exist_ok=True)

    test_db_url = f"sqlite+aiosqlite:///{test_db_path}"
    test_engine = create_async_engine(test_db_url, connect_args={"check_same_thread": False})
    test_session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False
    )

    # Configure CAS engine to use temp blocks dir
    original_blocks_dir = cas_engine.blocks_dir
    cas_engine.blocks_dir = test_blocks_dir

    # Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed default tenant and users
    async with test_session_factory() as session:
        tenant = Tenant(name="Test Org", description="Test Organization")
        session.add(tenant)
        await session.flush()

        admin = User(
            tenant_id=tenant.id,
            email="admin@test.local",
            hashed_password=get_password_hash("TestAdmin123!"),
            full_name="Test Admin",
            role=UserRole.ADMIN,
            is_active=True,
        )
        member = User(
            tenant_id=tenant.id,
            email="user@test.local",
            hashed_password=get_password_hash("TestUser123!"),
            full_name="Test User",
            role=UserRole.MEMBER,
            is_active=True,
        )
        session.add(admin)
        session.add(member)
        await session.commit()

        admin_id = admin.id
        member_id = member.id
        tenant_id = tenant.id

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db

    admin_token = create_access_token(admin_id, tenant_id, "admin@test.local", UserRole.ADMIN.value)
    member_token = create_access_token(member_id, tenant_id, "user@test.local", UserRole.MEMBER.value)

    yield {
        "admin_token": admin_token,
        "member_token": member_token,
        "admin_id": admin_id,
        "member_id": member_id,
        "tenant_id": tenant_id,
        "blocks_dir": test_blocks_dir,
    }

    # Teardown
    app.dependency_overrides.clear()
    cas_engine.blocks_dir = original_blocks_dir
    await test_engine.dispose()
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest_asyncio.fixture(scope="function")
async def client(test_env):
    """Async HTTP client for testing API routes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
