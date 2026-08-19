import os
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_large_streaming_upload_and_sharing(client: AsyncClient, test_env):
    """
    Test streaming upload of a multi-chunk file, creating public share link,
    and unauthenticated download via share token.
    """
    token = test_env["member_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 512 KB payload
    payload = os.urandom(512 * 1024)

    # Upload
    resp = await client.post(
        "/api/v1/files/upload",
        headers=headers,
        files={"file": ("large_blob.bin", payload, "application/octet-stream")},
    )
    assert resp.status_code == 200
    data = resp.json()
    file_id = data["file_id"]
    assert data["chunk_count"] == 8  # 512 KB / 64 KB = 8 chunks

    # Create share link
    share_resp = await client.post(
        f"/api/v1/files/{file_id}/share",
        headers=headers,
        json={"expires_in_hours": 24},
    )
    assert share_resp.status_code == 200
    share_data = share_resp.json()
    share_token = share_data["share_token"]

    # Download without authorization headers using share token
    public_down = await client.get(f"/api/v1/files/shared/{share_token}/download")
    assert public_down.status_code == 200
    assert public_down.content == payload


@pytest.mark.asyncio
async def test_multi_tenant_isolation(client: AsyncClient, test_env):
    """
    Test that users cannot access files from another tenant.
    """
    admin_token = test_env["admin_token"]
    member_token = test_env["member_token"]

    # Register user in a separate tenant
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "external@acme.com",
            "password": "Password123!",
            "full_name": "Acme User",
            "tenant_name": "Acme Corp",
        },
    )
    assert reg_resp.status_code == 200
    acme_token = reg_resp.json()["access_token"]

    # Acme user uploads a file
    resp = await client.post(
        "/api/v1/files/upload",
        headers={"Authorization": f"Bearer {acme_token}"},
        files={"file": ("acme_confidential.txt", b"Acme Secret 123", "text/plain")},
    )
    assert resp.status_code == 200
    acme_file_id = resp.json()["file_id"]

    # Test Org member attempts to access Acme file -> 404 Forbidden/Not Found
    fail_get = await client.get(
        f"/api/v1/files/{acme_file_id}",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert fail_get.status_code == 404

    # Test Org member attempts to delete Acme file -> 404
    fail_del = await client.delete(
        f"/api/v1/files/{acme_file_id}",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert fail_del.status_code == 404

    # System Admin CAN see all files
    admin_get = await client.get(
        f"/api/v1/files/{acme_file_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert admin_get.status_code == 200
