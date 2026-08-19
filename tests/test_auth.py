import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_auth_login_success(client: AsyncClient, test_env):
    """Test login returns JWT access and refresh tokens."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.local", "password": "TestAdmin123!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == "admin@test.local"
    assert data["user"]["role"] == "admin"


@pytest.mark.asyncio
async def test_auth_login_invalid_password(client: AsyncClient, test_env):
    """Test login failure with invalid password."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.local", "password": "WrongPassword!"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_register_and_me(client: AsyncClient, test_env):
    """Test user registration and authenticated profile lookup."""
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "newuser@test.local",
            "password": "Password123!",
            "full_name": "New Developer",
            "tenant_name": "Test Org",
        },
    )
    assert reg_resp.status_code == 200
    data = reg_resp.json()
    token = data["access_token"]

    # Access /auth/me with new token
    me_resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["email"] == "newuser@test.local"
    assert me_data["full_name"] == "New Developer"
