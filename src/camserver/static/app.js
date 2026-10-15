"use strict";

const state = {
  csrfToken: null,
  cameras: [],
  discovered: [],
  editingId: null,
};

const $ = (id) => document.getElementById(id);

async function init() {
  bindEvents();
  try {
    const resp = await fetch("/api/csrf");
    const data = await resp.json();
    state.csrfToken = data.token;
  } catch (err) {
    showStatus("Failed to initialize security token", true);
    return;
  }
  await loadCameras();
}

async function api(path, options = {}) {
  const opts = { ...options };
  opts.headers = { ...(opts.headers || {}) };
  if (!["GET", "HEAD"].includes((opts.method || "GET").toUpperCase())) {
    opts.headers["X-CSRF-Token"] = state.csrfToken || "";
  }
  if (opts.body && typeof opts.body !== "string") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(path, opts);
  if (resp.status === 204) return null;
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.detail || `Request failed (${resp.status})`);
  }
  return data;
}

function bindEvents() {
  $("scan-btn").addEventListener("click", scanNetwork);
  $("add-btn").addEventListener("click", () => openModal(null));
  $("cancel-btn").addEventListener("click", closeModal);
  $("save-btn").addEventListener("click", saveCamera);
  $("test-btn").addEventListener("click", testConnection);
  $("modal-backdrop").addEventListener("click", (e) => {
    if (e.target === $("modal-backdrop")) closeModal();
  });
}

async function loadCameras() {
  try {
    state.cameras = await api("/api/cameras");
  } catch (err) {
    showStatus(err.message, true);
    state.cameras = [];
  }
  renderCameras();
}

function renderCameras() {
  const grid = $("camera-grid");
  grid.innerHTML = "";
  $("empty-hint").classList.toggle("hidden", state.cameras.length > 0);

  for (const cam of state.cameras) {
    const card = document.createElement("article");
    card.className = "camera-card" + (cam.enabled ? "" : " disabled");

    const video = document.createElement("img");
    video.className = "camera-view";
    video.alt = cam.name;
    video.loading = "lazy";
    video.src = `/stream/${cam.id}`;

    const body = document.createElement("div");
    body.className = "camera-body";

    const title = document.createElement("h3");
    title.textContent = cam.name;

    const meta = document.createElement("p");
    meta.className = "camera-meta";
    meta.textContent = [cam.manufacturer, cam.model].filter(Boolean).join(" ") ||
      cam.ip;

    const ip = document.createElement("p");
    ip.className = "camera-ip";
    ip.textContent = cam.ip;

    const actions = document.createElement("div");
    actions.className = "camera-actions";

    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => openModal(cam));

    const del = document.createElement("button");
    del.type = "button";
    del.className = "danger";
    del.textContent = "Delete";
    del.addEventListener("click", () => deleteCamera(cam));

    actions.append(edit, del);
    body.append(title, meta, ip, actions);
    card.append(video, body);
    grid.append(card);
  }
}

async function scanNetwork() {
  const btn = $("scan-btn");
  btn.disabled = true;
  btn.textContent = "Scanning...";
  showStatus("Scanning the network for ONVIF cameras...", false);
  try {
    const data = await api("/api/scan", { method: "POST" });
    state.discovered = data.devices;
    renderDiscovery();
    if (data.devices.length === 0) {
      showStatus("No ONVIF cameras found.", false);
    } else {
      showStatus(`Found ${data.devices.length} camera(s).`, false);
    }
  } catch (err) {
    showStatus(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Scan network";
  }
}

function renderDiscovery() {
  const section = $("discovery");
  const list = $("discovery-list");
  list.innerHTML = "";
  section.classList.remove("hidden");

  for (const d of state.discovered) {
    const item = document.createElement("li");
    item.className = "discovery-item";

    const info = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = d.name || d.hardware || "Unknown camera";
    const detail = document.createElement("span");
    detail.className = "discovery-detail";
    detail.textContent = `${d.ip}:${d.port}`;
    info.append(name, document.createElement("br"), detail);

    item.append(info);

    if (d.known) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "Already added";
      item.append(badge);
    } else {
      const add = document.createElement("button");
      add.type = "button";
      add.className = "primary";
      add.textContent = "Add";
      add.addEventListener("click", () => openModal({ discovered: d }));
      item.append(add);
    }
    list.append(item);
  }
}

function openModal(prefill) {
  state.editingId = prefill && prefill.id ? prefill.id : null;
  $("test-result").classList.add("hidden");
  $("meta-info").classList.add("hidden");
  $("modal-title").textContent = state.editingId ? "Edit camera" : "Add camera";

  $("f-name").value = "";
  $("f-ip").value = "";
  $("f-port").value = 80;
  $("f-service").value = "";
  $("f-user").value = "";
  $("f-pass").value = "";
  $("f-rtsp").value = "";

  if (prefill) {
    if (prefill.discovered) {
      const d = prefill.discovered;
      $("f-name").value = d.name || d.hardware || `Camera ${d.ip}`;
      $("f-ip").value = d.ip;
      $("f-port").value = d.port;
      $("f-service").value = d.service_url || "";
      $("f-pass").setAttribute("required", "required");
    } else {
      $("f-name").value = prefill.name || "";
      $("f-ip").value = prefill.ip || "";
      $("f-service").value = prefill.onvif_service_url || "";
      $("f-user").value = prefill.username || "";
      $("f-rtsp").value = prefill.rtsp_url || "";
      $("f-pass").removeAttribute("required");
      if (prefill.has_password) {
        $("f-pass").placeholder = "Leave blank to keep current";
      }
    }
  }

  $("modal-backdrop").classList.remove("hidden");
  $("f-name").focus();
}

function closeModal() {
  $("modal-backdrop").classList.add("hidden");
  $("f-pass").removeAttribute("required");
  $("f-pass").placeholder = "";
  state.editingId = null;
}

function currentForm() {
  return {
    name: $("f-name").value.trim(),
    ip: $("f-ip").value.trim(),
    port: parseInt($("f-port").value, 10) || 80,
    service_url: $("f-service").value.trim() || null,
    username: $("f-user").value.trim() || null,
    password: $("f-pass").value || null,
    rtsp_url: $("f-rtsp").value.trim() || null,
  };
}

async function testConnection() {
  const form = currentForm();
  if (!form.ip || !form.username || !form.password) {
    showStatus("IP, username and password are required to test", true);
    return;
  }
  const result = $("test-result");
  const meta = $("meta-info");
  result.classList.remove("hidden");
  result.classList.remove("ok", "err");
  result.textContent = "Testing ONVIF connection...";

  const btn = $("test-btn");
  btn.disabled = true;
  try {
    const data = await api("/api/cameras/test", {
      method: "POST",
      body: {
        ip: form.ip,
        port: form.port,
        service_url: form.service_url,
        username: form.username,
        password: form.password,
      },
    });
    if (data.ok) {
      result.classList.add("ok");
      result.textContent = "Connection OK";
      if (data.rtsp_url) $("f-rtsp").value = data.rtsp_url;
      const bits = [data.manufacturer, data.model].filter(Boolean);
      if (data.serial) bits.push(`S/N ${data.serial}`);
      if (bits.length) {
        meta.classList.remove("hidden");
        meta.textContent = bits.join(" \u2014 ");
      }
    } else {
      result.classList.add("err");
      result.textContent = data.error || "Connection failed";
    }
  } catch (err) {
    result.classList.add("err");
    result.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

async function saveCamera() {
  const form = currentForm();
  if (!form.name || !form.ip) {
    showStatus("Name and IP address are required", true);
    return;
  }
  const payload = {
    name: form.name,
    ip: form.ip,
    manufacturer: null,
    model: null,
    serial: null,
    onvif_service_url: form.service_url,
    rtsp_url: form.rtsp_url,
    username: form.username,
    enabled: true,
  };
  if (form.password) payload.password = form.password;

  const saveBtn = $("save-btn");
  saveBtn.disabled = true;
  try {
    if (state.editingId) {
      await api(`/api/cameras/${state.editingId}`, { method: "PUT", body: payload });
    } else {
      await api("/api/cameras", { method: "POST", body: payload });
    }
    closeModal();
    await loadCameras();
  } catch (err) {
    showStatus(err.message, true);
  } finally {
    saveBtn.disabled = false;
  }
}

async function deleteCamera(cam) {
  if (!window.confirm(`Delete camera "${cam.name}"?`)) return;
  try {
    await api(`/api/cameras/${cam.id}`, { method: "DELETE" });
    await loadCameras();
  } catch (err) {
    showStatus(err.message, true);
  }
}

function showStatus(message, isError) {
  const el = $("status");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.remove("hidden");
  clearTimeout(showStatus._t);
  showStatus._t = setTimeout(() => el.classList.add("hidden"), 6000);
}

init();
