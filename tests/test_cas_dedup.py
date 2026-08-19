import hashlib
import os
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_cas_chunk_deduplication(client: AsyncClient, test_env):
    """
    Test Content-Addressable Storage (CAS) deduplication:
    1. Upload file A (192 KB = 3 chunks of 64 KB).
    2. Upload identical file B -> All 3 chunks should be deduplicated (0 new bytes stored, 100% savings).
    3. Upload file C (partially overlapping: 2 identical chunks + 1 new chunk) -> 2 hits, 1 miss (~66.7% savings).
    4. Download all files and verify byte-for-byte SHA-256 accuracy.
    """
    token = test_env["member_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Generate 3 distinct 64 KB chunks
    chunk_1 = b"A" * (64 * 1024)
    chunk_2 = b"B" * (64 * 1024)
    chunk_3 = b"C" * (64 * 1024)
    chunk_4 = b"D" * (64 * 1024)

    file_a_content = chunk_1 + chunk_2 + chunk_3  # 192 KB
    file_b_content = chunk_1 + chunk_2 + chunk_3  # 192 KB (100% duplicate)
    file_c_content = chunk_1 + chunk_2 + chunk_4  # 192 KB (2 chunks duplicate, 1 new)

    file_a_hash = hashlib.sha256(file_a_content).hexdigest()
    file_c_hash = hashlib.sha256(file_c_content).hexdigest()

    # 1. Upload File A
    resp_a = await client.post(
        "/api/v1/files/upload",
        headers=headers,
        files={"file": ("dataset_v1.bin", file_a_content, "application/octet-stream")},
    )
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["chunk_count"] == 3
    assert data_a["dedup_misses"] == 3
    assert data_a["dedup_hits"] == 0
    assert data_a["new_bytes_stored"] == 192 * 1024
    assert data_a["file_hash"] == file_a_hash
    file_a_id = data_a["file_id"]

    # 2. Upload File B (Exact duplicate)
    resp_b = await client.post(
        "/api/v1/files/upload",
        headers=headers,
        files={"file": ("dataset_v1_backup.bin", file_b_content, "application/octet-stream")},
    )
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    assert data_b["chunk_count"] == 3
    assert data_b["dedup_hits"] == 3  # All 3 chunks deduplicated!
    assert data_b["dedup_misses"] == 0
    assert data_b["new_bytes_stored"] == 0
    assert data_b["savings_percentage"] == 100.0
    file_b_id = data_b["file_id"]

    # 3. Upload File C (Partially duplicate)
    resp_c = await client.post(
        "/api/v1/files/upload",
        headers=headers,
        files={"file": ("dataset_v2_patch.bin", file_c_content, "application/octet-stream")},
    )
    assert resp_c.status_code == 200
    data_c = resp_c.json()
    assert data_c["chunk_count"] == 3
    assert data_c["dedup_hits"] == 2  # 2 chunks shared!
    assert data_c["dedup_misses"] == 1  # 1 new chunk
    assert data_c["new_bytes_stored"] == 64 * 1024
    assert data_c["savings_bytes"] == 128 * 1024
    file_c_id = data_c["file_id"]

    # 4. Check Aggregate Storage Telemetry
    stats_resp = await client.get("/api/v1/telemetry/stats", headers=headers)
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    cas_storage = stats["cas_storage"]

    # Virtual bytes uploaded: 192KB + 192KB + 192KB = 576KB (589,824 bytes)
    # Physical bytes stored: 4 chunks of 64KB = 256KB (262,144 bytes)
    # Storage saved: 320KB (327,680 bytes) = ~55.5% storage saved!
    assert cas_storage["unique_blocks_count"] == 4
    assert cas_storage["physical_bytes_stored"] == 256 * 1024
    assert cas_storage["virtual_bytes_referenced"] == 576 * 1024
    assert cas_storage["deduplication_percentage"] > 35.0

    # 5. Download and Reconstruct each file to verify byte-for-byte integrity
    down_a = await client.get(f"/api/v1/files/{file_a_id}/download", headers=headers)
    assert down_a.status_code == 200
    assert down_a.content == file_a_content

    down_b = await client.get(f"/api/v1/files/{file_b_id}/download", headers=headers)
    assert down_b.status_code == 200
    assert down_b.content == file_b_content

    down_c = await client.get(f"/api/v1/files/{file_c_id}/download", headers=headers)
    assert down_c.status_code == 200
    assert down_c.content == file_c_content
