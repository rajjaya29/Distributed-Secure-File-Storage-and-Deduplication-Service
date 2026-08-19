import os
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_deletion_reference_counting(client: AsyncClient, test_env):
    """
    Test reference-counted secure deletion:
    1. Upload File 1 (chunks X, Y).
    2. Upload File 2 (chunks Y, Z). Block Y has ref_count = 2.
    3. Delete File 1: Block X purged (ref 0), Block Y retained (ref 1). File 2 remains fully readable.
    4. Delete File 2: Blocks Y and Z purged (ref 0). No blocks remain in CAS.
    """
    token = test_env["member_token"]
    headers = {"Authorization": f"Bearer {token}"}

    chunk_x = b"X" * (64 * 1024)
    chunk_y = b"Y" * (64 * 1024)
    chunk_z = b"Z" * (64 * 1024)

    file_1_content = chunk_x + chunk_y
    file_2_content = chunk_y + chunk_z

    # Upload File 1
    resp_1 = await client.post(
        "/api/v1/files/upload",
        headers=headers,
        files={"file": ("file1.dat", file_1_content, "application/octet-stream")},
    )
    assert resp_1.status_code == 200
    file_1_id = resp_1.json()["file_id"]

    # Upload File 2
    resp_2 = await client.post(
        "/api/v1/files/upload",
        headers=headers,
        files={"file": ("file2.dat", file_2_content, "application/octet-stream")},
    )
    assert resp_2.status_code == 200
    file_2_id = resp_2.json()["file_id"]

    # Verify 3 unique blocks exist in CAS
    blocks_resp = await client.get("/api/v1/telemetry/blocks", headers=headers)
    assert len(blocks_resp.json()) == 3

    # Delete File 1
    del_1 = await client.delete(f"/api/v1/files/{file_1_id}", headers=headers)
    assert del_1.status_code == 200
    del_data_1 = del_1.json()
    assert del_data_1["purged_blocks"] == 1  # Block X purged
    assert del_data_1["retained_blocks"] == 1  # Block Y retained (still used by File 2)

    # File 2 should still be completely downloadable and valid
    down_2 = await client.get(f"/api/v1/files/{file_2_id}/download", headers=headers)
    assert down_2.status_code == 200
    assert down_2.content == file_2_content

    # Delete File 2
    del_2 = await client.delete(f"/api/v1/files/{file_2_id}", headers=headers)
    assert del_2.status_code == 200
    del_data_2 = del_2.json()
    assert del_data_2["purged_blocks"] == 2  # Blocks Y and Z purged

    # Verify 0 active CAS blocks remain
    blocks_resp_after = await client.get("/api/v1/telemetry/blocks", headers=headers)
    assert len(blocks_resp_after.json()) == 0
