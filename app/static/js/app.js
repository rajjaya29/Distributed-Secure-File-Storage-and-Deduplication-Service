// State Management
const state = {
  token: localStorage.getItem("access_token") || "",
  refreshToken: localStorage.getItem("refresh_token") || "",
  user: JSON.parse(localStorage.getItem("user_info") || "null"),
  currentView: "dashboard",
  stats: null,
  files: [],
  casBlocks: [],
  auditLogs: [],
};

// Utilities
function formatBytes(bytes, decimals = 2) {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + " " + sizes[i];
}

function truncateHash(hash, len = 8) {
  if (!hash) return "";
  return hash.substring(0, len) + "..." + hash.substring(hash.length - len);
}

function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// API Helper
async function apiRequest(endpoint, options = {}) {
  const headers = options.headers || {};
  if (state.token && !headers["Authorization"]) {
    headers["Authorization"] = `Bearer ${state.token}`;
  }

  try {
    const res = await fetch(endpoint, { ...options, headers });
    if (res.status === 401) {
      // Try refresh
      if (state.refreshToken && !endpoint.includes("/auth/")) {
        const refreshed = await attemptRefreshToken();
        if (refreshed) {
          headers["Authorization"] = `Bearer ${state.token}`;
          return fetch(endpoint, { ...options, headers });
        }
      }
      handleLogout();
      throw new Error("Session expired. Please log in again.");
    }
    return res;
  } catch (err) {
    showToast(err.message, "error");
    throw err;
  }
}

async function attemptRefreshToken() {
  try {
    const res = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: state.refreshToken }),
    });
    if (res.ok) {
      const data = await res.json();
      state.token = data.access_token;
      localStorage.setItem("access_token", data.access_token);
      return true;
    }
  } catch (e) {}
  return false;
}

// Auth Handlers
function setAuthState(token, refreshToken, user) {
  state.token = token;
  state.refreshToken = refreshToken;
  state.user = user;
  localStorage.setItem("access_token", token);
  localStorage.setItem("refresh_token", refreshToken);
  localStorage.setItem("user_info", JSON.stringify(user));
  updateUserUI();
}

function handleLogout() {
  state.token = "";
  state.refreshToken = "";
  state.user = null;
  localStorage.clear();
  updateUserUI();
  openAuthModal();
}

function updateUserUI() {
  const userBox = document.getElementById("user-status-box");
  const userNameElem = document.getElementById("user-name-display");
  const userRoleElem = document.getElementById("user-role-display");
  const avatarElem = document.getElementById("avatar-letter");

  if (state.user) {
    userBox.style.display = "flex";
    userNameElem.innerText = state.user.full_name || state.user.email;
    userRoleElem.innerText = state.user.role || "Member";
    avatarElem.innerText = (state.user.full_name || state.user.email)[0].toUpperCase();
    closeAuthModal();
  } else {
    userBox.style.display = "none";
    openAuthModal();
  }
}

function openAuthModal() {
  document.getElementById("auth-modal").classList.add("active");
}

function closeAuthModal() {
  document.getElementById("auth-modal").classList.remove("active");
}

// Navigation & View Routing
function switchView(viewName) {
  state.currentView = viewName;
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === viewName);
  });
  document.querySelectorAll(".view-panel").forEach((panel) => {
    panel.style.display = panel.id === `view-${viewName}` ? "block" : "none";
  });

  if (viewName === "dashboard") loadDashboard();
  if (viewName === "files") loadFiles();
  if (viewName === "blocks") loadCasBlocks();
  if (viewName === "audit") loadAuditLogs();
}

// Load Telemetry & Dashboard Stats
async function loadDashboard() {
  try {
    const res = await apiRequest("/api/v1/telemetry/stats");
    if (!res.ok) return;
    const data = await res.json();
    state.stats = data;

    // Render Metric Cards
    document.getElementById("stat-total-files").innerText = data.total_files;
    document.getElementById("stat-unique-blocks").innerText = data.cas_storage.unique_blocks_count;
    document.getElementById("stat-raw-bytes").innerText = formatBytes(data.cas_storage.virtual_bytes_referenced);
    document.getElementById("stat-physical-bytes").innerText = formatBytes(data.cas_storage.physical_bytes_stored);
    document.getElementById("stat-saved-bytes").innerText = formatBytes(data.cas_storage.bytes_saved);
    
    // Deduplication Progress Bar & Badge
    const dedupPct = data.cas_storage.deduplication_percentage || 0;
    document.getElementById("dedup-badge-text").innerText = `${dedupPct}% Storage Saved`;
    document.getElementById("dedup-progress-fill").style.width = `${Math.min(100, Math.max(5, dedupPct))}%`;
    document.getElementById("dedup-ratio-text").innerText = `${data.cas_storage.deduplication_ratio}x`;

    // Latency Percentiles
    document.getElementById("lat-p50").innerText = `${data.latency.p50} ms`;
    document.getElementById("lat-p95").innerText = `${data.latency.p95} ms`;
    document.getElementById("lat-p99").innerText = `${data.latency.p99} ms`;
    document.getElementById("lat-avg").innerText = `${data.latency.avg} ms`;

    // Render Hot Blocks
    renderHotBlocks(data.hot_blocks);
  } catch (err) {
    console.error(err);
  }
}

function renderHotBlocks(hotBlocks) {
  const tbody = document.getElementById("hot-blocks-tbody");
  tbody.innerHTML = "";
  if (!hotBlocks || hotBlocks.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No CAS blocks stored yet.</td></tr>`;
    return;
  }

  hotBlocks.forEach((b) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="hash-pill" title="${b.hash}">${truncateHash(b.hash, 10)}</span></td>
      <td>${formatBytes(b.size_bytes)}</td>
      <td><span class="badge badge-purple">${b.ref_count} References</span></td>
      <td style="color: var(--accent-emerald); font-weight: 600;">+${formatBytes(b.savings_bytes)}</td>
    `;
    tbody.appendChild(tr);
  });
}

// Load Files List
async function loadFiles() {
  try {
    const search = document.getElementById("file-search-input").value;
    const url = search ? `/api/v1/files?search=${encodeURIComponent(search)}` : "/api/v1/files";
    const res = await apiRequest(url);
    if (!res.ok) return;
    const data = await res.json();
    state.files = data.items;
    renderFilesTable(data.items);
  } catch (err) {
    console.error(err);
  }
}

function renderFilesTable(files) {
  const tbody = document.getElementById("files-tbody");
  tbody.innerHTML = "";
  if (!files || files.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 32px;">No files uploaded yet. Drag & drop files in the Upload Studio!</td></tr>`;
    return;
  }

  files.forEach((f) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>
        <div class="file-name-cell">
          <svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
          <span>${f.filename}</span>
        </div>
      </td>
      <td>${formatBytes(f.raw_size_bytes)}</td>
      <td><span class="badge badge-cyan">${f.chunk_count} Chunks</span></td>
      <td><span class="hash-pill" title="${f.file_hash}">${truncateHash(f.file_hash, 6)}</span></td>
      <td>${new Date(f.created_at).toLocaleDateString()}</td>
      <td>
        <div style="display: flex; gap: 6px;">
          <button class="btn btn-secondary btn-sm" onclick="downloadFile('${f.id}')" title="Download Reconstructed File">
            <svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
          </button>
          <button class="btn btn-secondary btn-sm" onclick="inspectChunks('${f.id}')" title="View CAS Chunks Manifest">
            <svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
          </button>
          <button class="btn btn-secondary btn-sm" onclick="openShareModal('${f.id}', '${f.filename}')" title="Share Link">
            <svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z"/></svg>
          </button>
          <button class="btn btn-danger btn-sm" onclick="deleteFile('${f.id}', '${f.filename}')" title="Delete File & Release CAS Blocks">
            <svg fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
          </button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

// Download File Stream
async function downloadFile(fileId) {
  try {
    showToast("Starting chunk reconstruction and download...", "info");
    const res = await apiRequest(`/api/v1/files/${fileId}/download`);
    if (!res.ok) throw new Error("Download failed");
    
    // Extract filename from header if possible
    const disposition = res.headers.get("Content-Disposition");
    let filename = "downloaded_file";
    if (disposition && disposition.includes("filename=")) {
      filename = disposition.split("filename=")[1].replace(/"/g, "");
    }

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
    showToast(`File "${filename}" downloaded successfully!`, "success");
  } catch (err) {
    showToast(err.message, "error");
  }
}

// Inspect File Chunks Manifest Modal
async function inspectChunks(fileId) {
  try {
    const res = await apiRequest(`/api/v1/files/${fileId}`);
    if (!res.ok) return;
    const file = await res.json();

    document.getElementById("modal-chunk-title").innerText = `Chunk Manifest: ${file.filename}`;
    document.getElementById("modal-chunk-meta").innerHTML = `
      <span>Total Size: <strong>${formatBytes(file.raw_size_bytes)}</strong></span> | 
      <span>Total Chunks: <strong>${file.chunk_count}</strong></span> | 
      <span>SHA-256: <strong class="hash-pill">${truncateHash(file.file_hash, 12)}</strong></span>
    `;

    const container = document.getElementById("modal-chunk-list");
    container.innerHTML = "";

    file.chunks.forEach((c) => {
      const div = document.createElement("div");
      div.className = "card";
      div.style.padding = "10px 14px";
      div.style.marginBottom = "8px";
      div.style.display = "flex";
      div.style.justifyContent = "space-between";
      div.style.alignItems = "center";
      div.innerHTML = `
        <div>
          <strong>#${c.chunk_index}</strong>: <span class="hash-pill">${c.block_hash}</span>
          <span style="font-size: 0.8rem; color: var(--text-muted); margin-left: 8px;">(${formatBytes(c.size_bytes)})</span>
        </div>
        <div>
          ${c.is_deduplicated ? `<span class="badge badge-emerald">Deduplicated (${c.block_ref_count} refs)</span>` : `<span class="badge badge-cyan">Unique Block</span>`}
        </div>
      `;
      container.appendChild(div);
    });

    document.getElementById("chunk-details-modal").classList.add("active");
  } catch (err) {
    showToast(err.message, "error");
  }
}

// Delete File
async function deleteFile(fileId, filename) {
  if (!confirm(`Are you sure you want to delete "${filename}"? Underlying unreferenced CAS blocks will be purged.`)) return;

  try {
    const res = await apiRequest(`/api/v1/files/${fileId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Delete failed");
    const data = await res.json();
    showToast(`Deleted "${filename}". Purged ${data.purged_blocks} blocks, retained ${data.retained_blocks} shared blocks.`, "success");
    loadFiles();
    loadDashboard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// Share Modal
function openShareModal(fileId, filename) {
  document.getElementById("share-file-id").value = fileId;
  document.getElementById("share-file-name").innerText = filename;
  document.getElementById("share-result-container").style.display = "none";
  document.getElementById("share-modal").classList.add("active");
}

async function generateShareLink() {
  const fileId = document.getElementById("share-file-id").value;
  const hours = parseInt(document.getElementById("share-hours").value) || 72;

  try {
    const res = await apiRequest(`/api/v1/files/${fileId}/share`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expires_in_hours: hours }),
    });
    if (!res.ok) throw new Error("Failed to generate share link");
    const data = await res.json();
    
    const fullUrl = window.location.origin + data.share_url;
    document.getElementById("share-link-input").value = fullUrl;
    document.getElementById("share-result-container").style.display = "block";
    showToast("Share link generated!", "success");
  } catch (err) {
    showToast(err.message, "error");
  }
}

function copyShareLink() {
  const copyText = document.getElementById("share-link-input");
  copyText.select();
  navigator.clipboard.writeText(copyText.value);
  showToast("Share URL copied to clipboard!", "success");
}

// Upload & Real-Time Deduplication Visualizer Studio
async function handleFileUpload(file) {
  if (!file) return;

  const visualizerGrid = document.getElementById("upload-chunks-grid");
  const statusBox = document.getElementById("upload-status-text");
  const savingsLiveBox = document.getElementById("upload-savings-live");

  visualizerGrid.innerHTML = "";
  statusBox.innerText = `Ingesting "${file.name}" (${formatBytes(file.size)})... Streaming to CAS...`;
  savingsLiveBox.innerText = "Processing chunks...";

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await apiRequest("/api/v1/files/upload", {
      method: "POST",
      body: formData,
    });

    if (!res.ok) throw new Error("Upload failed");
    const data = await res.json();

    // Visualize Chunks Simulation
    visualizerGrid.innerHTML = "";
    const totalChunks = data.chunk_count;
    const dedupHits = data.dedup_hits;
    const misses = data.dedup_misses;

    for (let i = 0; i < totalChunks; i++) {
      const block = document.createElement("div");
      const isHit = i < dedupHits; // First N visually as hits
      block.className = `chunk-block ${isHit ? "dedup" : "new"}`;
      block.innerText = i + 1;
      block.title = `Chunk ${i + 1}: ${isHit ? "Deduplicated Hit" : "New Block Stored"}`;
      visualizerGrid.appendChild(block);
    }

    statusBox.innerText = `Completed "${data.filename}" | SHA-256: ${truncateHash(data.file_hash, 8)}`;
    savingsLiveBox.innerHTML = `
      Saved: <strong style="color: var(--accent-emerald);">${formatBytes(data.savings_bytes)} (${data.savings_percentage}%)</strong> | 
      Hits: <strong style="color: var(--accent-emerald);">${data.dedup_hits}</strong> | 
      New: <strong style="color: var(--accent-blue);">${data.dedup_misses}</strong>
    `;

    showToast(`Upload complete! Saved ${data.savings_percentage}% disk space through CAS deduplication!`, "success");
    loadDashboard();
  } catch (err) {
    statusBox.innerText = `Upload failed: ${err.message}`;
    showToast(err.message, "error");
  }
}

// Load CAS Blocks Registry
async function loadCasBlocks() {
  try {
    const res = await apiRequest("/api/v1/telemetry/blocks");
    if (!res.ok) return;
    const blocks = await res.json();
    state.casBlocks = blocks;

    const tbody = document.getElementById("blocks-tbody");
    tbody.innerHTML = "";

    if (!blocks || blocks.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 24px;">No CAS blocks found.</td></tr>`;
      return;
    }

    blocks.forEach((b) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><span class="hash-pill" title="${b.hash}">${b.hash}</span></td>
        <td>${formatBytes(b.size_bytes)}</td>
        <td><span class="badge ${b.ref_count > 1 ? "badge-emerald" : "badge-cyan"}">${b.ref_count} Refs</span></td>
        <td>${b.is_encrypted ? `<span class="badge badge-purple">AES-256</span>` : `<span class="badge badge-amber">Plain</span>`}</td>
        <td>${new Date(b.last_accessed_at || b.created_at).toLocaleString()}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error(err);
  }
}

// Trigger Garbage Collection
async function triggerGC() {
  try {
    showToast("Running CAS garbage collection...", "info");
    const res = await apiRequest("/api/v1/telemetry/gc", { method: "POST" });
    if (!res.ok) throw new Error("GC failed or requires Admin role");
    const data = await res.json();
    showToast(`GC completed: Purged ${data.details.orphaned_blocks_deleted} orphan blocks (${formatBytes(data.details.bytes_reclaimed)} reclaimed)`, "success");
    loadCasBlocks();
    loadDashboard();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// Load Audit Logs
async function loadAuditLogs() {
  try {
    const res = await apiRequest("/api/v1/telemetry/audit");
    if (!res.ok) return;
    const logs = await res.json();

    const tbody = document.getElementById("audit-tbody");
    tbody.innerHTML = "";

    if (!logs || logs.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No audit logs yet.</td></tr>`;
      return;
    }

    logs.forEach((l) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><span class="badge badge-cyan">${l.action}</span></td>
        <td>${l.resource_type} (${truncateHash(l.resource_id || "", 6)})</td>
        <td><span class="badge ${l.status === "SUCCESS" ? "badge-emerald" : "badge-amber"}">${l.status}</span></td>
        <td>${l.latency_ms ? `${l.latency_ms.toFixed(1)} ms` : "-"}</td>
        <td>${new Date(l.created_at).toLocaleTimeString()}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error(err);
  }
}

// Event Listeners Initialization
document.addEventListener("DOMContentLoaded", () => {
  // Navigation
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      switchView(item.dataset.view);
    });
  });

  // Auth Form Handlers
  document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = document.getElementById("login-email").value;
    const password = document.getElementById("login-password").value;

    try {
      const res = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Login failed");
      }

      const data = await res.json();
      setAuthState(data.access_token, data.refresh_token, data.user);
      showToast(`Welcome back, ${data.user.full_name}!`, "success");
      loadDashboard();
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  // Drag & Drop Dropzone
  const dropzone = document.getElementById("upload-dropzone");
  const fileInput = document.getElementById("file-input");

  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) {
      handleFileUpload(e.target.files[0]);
    }
  });

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });

  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("dragover");
  });

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files.length > 0) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  });

  // File Search
  document.getElementById("file-search-input").addEventListener("input", () => {
    loadFiles();
  });

  // Auto-login or initial setup
  if (state.token && state.user) {
    updateUserUI();
    switchView("dashboard");
  } else {
    // Fill default demo admin credentials in login form
    document.getElementById("login-email").value = "admin@storage.local";
    document.getElementById("login-password").value = "AdminSecure2026!";
    openAuthModal();
  }

  // Periodic Telemetry Refresh every 10 seconds
  setInterval(() => {
    if (state.token && state.currentView === "dashboard") {
      loadDashboard();
    }
  }, 10000);
});
