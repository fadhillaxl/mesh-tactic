/**
 * Tactical Railway AIS Mission Control Client Application.
 * Integrates Leaflet Dark Mode Map, GeoJSON Railway Corridors,
 * Live SSE Telemetry Ingestion, Dynamic HUD Cards, and Log Streaming.
 */

// ==============================================================================
// 1. APPLICATION STATE & CONSTANTS
// ==============================================================================
const STATE = {
  gateway: {
    station_name: "Titik Tengah Utama (Stasiun Rendeh)",
    lat: -6.58025,
    lon: 107.24695,
  },
  trains: {},          // Map of train_id -> latest telemetry object
  packets: [],         // Array of ingested packets
  autoScroll: true,
  filterTrain: "ALL",
  eventSource: null,
  pollTimer: null,
  map: null,
  layers: {
    tracks: null,
    vectors: null,
    station: null,
    trains: {},
    trails: {},
    vectorLines: {},
  },
  showTracks: true,
  showVectors: true,
};

const TRAIN_COLORS = {
  "0x0002": "#00f0ff", // Tactical Cyan (Train 2 - Pi 5)
  "0x0003": "#ffb800", // Amber (Train 3 - AML)
  "DEFAULT": "#3a86ff",
};

// ==============================================================================
// 2. INITIALIZATION
// ==============================================================================
document.addEventListener("DOMContentLoaded", () => {
  initMap();
  initUIControls();
  loadInitialData();
  startLiveStream();
});

// ==============================================================================
// 3. MAP SUBSYSTEM (LEAFLET)
// ==============================================================================
function initMap() {
  // Create map centered on Titik Tengah Utama (Stasiun Rendeh)
  STATE.map = L.map("tactical-map", {
    zoomControl: false,
    attributionControl: false,
  }).setView([STATE.gateway.lat, STATE.gateway.lon], 10);

  // Position custom zoom control on top-right
  L.control.zoom({ position: "topright" }).addTo(STATE.map);

  // Dark Matter Tiles from CartoDB
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    subdomains: "abcd",
  }).addTo(STATE.map);

  // Layer groups
  STATE.layers.tracks = L.layerGroup().addTo(STATE.map);
  STATE.layers.vectors = L.layerGroup().addTo(STATE.map);

  // Add Gateway Station Marker
  renderGatewayMarker();

  // Load Railway Track Corridors from GeoJSON
  loadRailwayCorridors();
}

function renderGatewayMarker() {
  const stationIcon = L.divIcon({
    className: "custom-station-icon",
    html: `
      <div class="station-pulse-marker" title="${STATE.gateway.station_name}"></div>
    `,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  });

  STATE.layers.station = L.marker([STATE.gateway.lat, STATE.gateway.lon], { icon: stationIcon })
    .addTo(STATE.map)
    .bindPopup(`
      <div style="font-family: 'Inter', sans-serif; font-size: 12px; color: #111;">
        <strong style="color: #0088cc;">${STATE.gateway.station_name}</strong><br/>
        Titik Tengah Utama Pembagi Koridor<br/>
        GPS: ${STATE.gateway.lat}, ${STATE.gateway.lon}<br/>
        <em>Gateway Ingestion: Active</em>
      </div>
    `);
}

async function loadRailwayCorridors() {
  try {
    // 1. Load Conventional & Whoosh tracks
    const res1 = await fetch("/api/geojson/jalur_kereta_jakarta_bandung.json");
    if (res1.ok) {
      const geo1 = await res1.json();
      L.geoJSON(geo1, {
        style: (feature) => {
          const isWhoosh = feature.properties?.type === "High Speed Rail";
          return {
            color: isWhoosh ? "#b5179e" : "#00f0ff",
            weight: isWhoosh ? 4 : 3,
            opacity: 0.85,
            dashArray: isWhoosh ? "8, 6" : "4, 4",
          };
        },
        onEachFeature: (feature, layer) => {
          layer.bindTooltip(feature.properties?.name || "Jalur Kereta", {
            sticky: true,
            className: "tactical-tooltip",
          });
        },
      }).addTo(STATE.layers.tracks);
    }

    // 2. Load Segmen 2 (Titik Tengah ke Bandung)
    const res2 = await fetch("/api/geojson/tengah_bandung_ke_bandung.json");
    if (res2.ok) {
      const geo2 = await res2.json();
      L.geoJSON(geo2, {
        style: {
          color: "#ffb800",
          weight: 4,
          opacity: 0.9,
          dashArray: "6, 6",
        },
        onEachFeature: (feature, layer) => {
          layer.bindTooltip(feature.properties?.name || "Segmen 2", {
            sticky: true,
            className: "tactical-tooltip",
          });
        },
      }).addTo(STATE.layers.tracks);
    }
  } catch (err) {
    console.warn("[WARN] Could not load GeoJSON tracks from server:", err);
  }
}

function updateTrainMarker(trainId, lat, lon, heading, speed) {
  const color = TRAIN_COLORS[trainId] || TRAIN_COLORS["DEFAULT"];

  // 1. Trail Breadcrumb
  if (!STATE.layers.trails[trainId]) {
    STATE.layers.trails[trainId] = L.polyline([[lat, lon]], {
      color: color,
      weight: 2,
      opacity: 0.6,
      dashArray: "3, 6",
    }).addTo(STATE.map);
  } else {
    const latlngs = STATE.layers.trails[trainId].getLatLngs();
    latlngs.push([lat, lon]);
    if (latlngs.length > 50) latlngs.shift(); // Keep last 50 points
    STATE.layers.trails[trainId].setLatLngs(latlngs);
  }

  // 2. Train Icon with Heading Rotation
  const rot = Math.round(heading || 0);
  const trainSvg = `
    <div class="train-arrow-marker" style="transform: rotate(${rot}deg); color: ${color};" title="${trainId} (${speed} km/h)">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="${color}" stroke="#ffffff" stroke-width="1.5">
        <path d="M12 2L4 20l8-4 8 4z"/>
      </svg>
    </div>
  `;

  const customIcon = L.divIcon({
    className: "custom-train-marker",
    html: trainSvg,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
  });

  if (!STATE.layers.trains[trainId]) {
    STATE.layers.trains[trainId] = L.marker([lat, lon], { icon: customIcon })
      .addTo(STATE.map)
      .bindPopup(`
        <div style="font-family: 'Inter', sans-serif; font-size: 12px; color: #111;">
          <strong style="color: ${color};">Train ${trainId}</strong><br/>
          GPS: ${lat.toFixed(5)}, ${lon.toFixed(5)}<br/>
          Kecepatan: ${speed} km/h<br/>
          Arah: ${heading.toFixed(1)}°
        </div>
      `);
  } else {
    STATE.layers.trains[trainId].setLatLng([lat, lon]);
    STATE.layers.trains[trainId].setIcon(customIcon);
  }

  // 3. Distance Vector Line to Gateway Station
  if (STATE.showVectors) {
    if (!STATE.layers.vectorLines[trainId]) {
      STATE.layers.vectorLines[trainId] = L.polyline(
        [[lat, lon], [STATE.gateway.lat, STATE.gateway.lon]],
        {
          color: color,
          weight: 1.5,
          opacity: 0.45,
          dashArray: "5, 8",
        }
      ).addTo(STATE.layers.vectors);
    } else {
      STATE.layers.vectorLines[trainId].setLatLngs([
        [lat, lon],
        [STATE.gateway.lat, STATE.gateway.lon],
      ]);
    }
  }
}

// ==============================================================================
// 4. DATA INGESTION & SSE STREAMING
// ==============================================================================
async function loadInitialData() {
  try {
    const res = await fetch("/api/telemetry");
    if (!res.ok) return;
    const data = await res.json();

    if (data.gateway) {
      STATE.gateway.station_name = data.gateway.station_name || STATE.gateway.station_name;
      STATE.gateway.lat = data.gateway.gps?.latitude || STATE.gateway.lat;
      STATE.gateway.lon = data.gateway.gps?.longitude || STATE.gateway.lon;
      updateGatewayUI();
    }

    // Ingest recent historical packets
    if (Array.isArray(data.recent_packets)) {
      data.recent_packets.forEach((p) => processTelemetryPacket(p, false));
    }
  } catch (err) {
    console.warn("[WARN] Could not fetch initial telemetry:", err);
  }
}

function startLiveStream() {
  if (STATE.eventSource) {
    STATE.eventSource.close();
  }

  const connStatusEl = document.getElementById("stat-conn-status");

  try {
    STATE.eventSource = new EventSource("/api/stream");

    STATE.eventSource.addEventListener("telemetry", (e) => {
      try {
        const packet = JSON.parse(e.data);
        processTelemetryPacket(packet, true);
      } catch (err) {
        console.error("[ERROR] Failed parsing SSE payload:", err);
      }
    });

    STATE.eventSource.onopen = () => {
      connStatusEl.innerHTML = '<span class="live-dot"></span> ONLINE (SSE)';
      connStatusEl.className = "stat-value status-online";
    };

    STATE.eventSource.onerror = () => {
      connStatusEl.innerHTML = '<span class="badge-dot" style="background:#ffaa00"></span> RECONNECTING...';
      connStatusEl.className = "stat-value";
      // Fallback to polling if stream is disrupted
      triggerPollingFallback();
    };
  } catch (err) {
    triggerPollingFallback();
  }
}

function triggerPollingFallback() {
  if (STATE.pollTimer) return;
  STATE.pollTimer = setInterval(async () => {
    try {
      const res = await fetch("/api/telemetry");
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.recent_packets) && data.recent_packets.length > 0) {
          const latest = data.recent_packets[data.recent_packets.length - 1];
          processTelemetryPacket(latest, true);
        }
      }
    } catch (_) {}
  }, 2000);
}

// ==============================================================================
// 5. PACKET PROCESSING & UI UPDATES
// ==============================================================================
function processTelemetryPacket(packet, isLive = true) {
  if (!packet || !packet.telemetry) return;

  const telem = packet.telemetry;
  const tid = telem.train_id;
  if (!tid) return;

  // Add to internal packet array
  STATE.packets.push(packet);
  if (STATE.packets.length > 500) STATE.packets.shift();

  // Update train latest state
  STATE.trains[tid] = {
    train_id: tid,
    last_packet: packet,
    lat: telem.gps?.latitude || 0,
    lon: telem.gps?.longitude || 0,
    speed: telem.motion?.speed_kmh || 0,
    heading: telem.motion?.heading_deg || 0,
    health: telem.device_health || {},
    dist_gw: packet.gateway?.distance_to_train_km || 0,
    received_time: packet.received_time_iso || "",
    correlation: packet.gateway?.rx_correlation,
  };

  // Update map marker
  updateTrainMarker(
    tid,
    STATE.trains[tid].lat,
    STATE.trains[tid].lon,
    STATE.trains[tid].heading,
    STATE.trains[tid].speed
  );

  // Update DOM components
  updateHeaderStats();
  renderTrainCards();
  appendTableRow(packet, isLive);
}

function updateGatewayUI() {
  const coordsEl = document.getElementById("gw-coords");
  if (coordsEl) {
    coordsEl.innerText = `GPS: ${STATE.gateway.lat.toFixed(5)}, ${STATE.gateway.lon.toFixed(5)} | SDR Ingest: Active`;
  }
}

function updateHeaderStats() {
  const totalEl = document.getElementById("stat-total-packets");
  const trainsEl = document.getElementById("stat-active-trains");
  const badgeEl = document.getElementById("train-counter-badge");

  if (totalEl) totalEl.innerText = STATE.packets.length.toLocaleString();
  const count = Object.keys(STATE.trains).length;
  if (trainsEl) trainsEl.innerText = count;
  if (badgeEl) badgeEl.innerText = `${count} LIVE`;
}

function renderTrainCards() {
  const container = document.getElementById("train-cards-container");
  const emptyState = document.getElementById("trains-empty-state");

  const trainKeys = Object.keys(STATE.trains);
  if (trainKeys.length === 0) {
    if (emptyState) emptyState.style.display = "flex";
    return;
  }
  if (emptyState) emptyState.style.display = "none";

  trainKeys.forEach((tid) => {
    const t = STATE.trains[tid];
    let card = document.getElementById(`card-${tid}`);

    const color = TRAIN_COLORS[tid] || TRAIN_COLORS["DEFAULT"];
    const isEmerg = t.health?.is_emergency;
    const timeShort = t.received_time ? t.received_time.substring(11, 19) : "--:--:--";

    if (!card) {
      card = document.createElement("div");
      card.id = `card-${tid}`;
      card.className = "train-card";
      card.style.setProperty("--card-accent", color);
      container.appendChild(card);
    }

    card.innerHTML = `
      <div class="card-top">
        <div class="card-train-id">
          <span style="color: ${color};">Train ${tid}</span>
          <span class="card-train-pill">NODE ${t.last_packet?.packet?.src_id || tid}</span>
        </div>
        <div class="card-time">${timeShort} UTC</div>
      </div>

      <div class="card-hero-metrics">
        <div class="hero-metric">
          <span class="hero-label">SPEED</span>
          <span class="hero-val">${t.speed.toFixed(1)} <span class="hero-unit">km/h</span></span>
        </div>
        <div class="hero-metric">
          <span class="hero-label">DIST TO GATEWAY</span>
          <span class="hero-val" style="color: ${color};">${t.dist_gw.toFixed(1)} <span class="hero-unit">km</span></span>
        </div>
      </div>

      <div class="card-health-grid">
        <div class="health-item">
          <span>BATTERY</span>
          <span>${(t.health?.battery_v || 0).toFixed(2)} V</span>
        </div>
        <div class="health-item">
          <span>TEMP</span>
          <span>${t.health?.temperature_c || 0} °C</span>
        </div>
        <div class="health-item">
          <span>CPU LOAD</span>
          <span>${t.health?.cpu_load_pct || 0} %</span>
        </div>
      </div>

      <div class="card-flags">
        <span class="flag-badge ${t.health?.flags?.gps_locked ? 'ok' : 'off'}">GPS: 3D-FIX</span>
        <span class="flag-badge ${t.health?.flags?.engine_active ? 'ok' : 'off'}">ENG: ${t.health?.flags?.engine_active ? 'ON' : 'OFF'}</span>
        <span class="flag-badge ${t.health?.flags?.sdr_healthy ? 'ok' : 'off'}">SDR: OK</span>
        <span class="flag-badge ${isEmerg ? 'emergency' : 'ok'}">${isEmerg ? 'BRAKE: EMERGENCY' : 'BRAKE: NORMAL'}</span>
      </div>
    `;

    card.onclick = () => {
      STATE.map.setView([t.lat, t.lon], 13);
      if (STATE.layers.trains[tid]) {
        STATE.layers.trains[tid].openPopup();
      }
    };
  });
}

function appendTableRow(packet, isLive = true) {
  const tbody = document.getElementById("logs-table-body");
  if (!tbody) return;

  const telem = packet.telemetry || {};
  const gw = packet.gateway || {};
  const tid = telem.train_id || "UNKNOWN";

  // Filter check
  if (STATE.filterTrain !== "ALL" && STATE.filterTrain !== tid) {
    return;
  }

  const row = document.createElement("tr");
  if (isLive) row.className = "new-row";

  const timeStr = (packet.received_time_iso || "").substring(11, 19) || "--:--:--";
  const nodeClass = tid === "0x0002" ? "node-2" : (tid === "0x0003" ? "node-3" : "");
  const isEmerg = telem.device_health?.is_emergency;

  row.innerHTML = `
    <td>${timeStr}</td>
    <td class="cell-node ${nodeClass}">${tid}</td>
    <td>${(telem.gps?.latitude || 0).toFixed(5)}, ${(telem.gps?.longitude || 0).toFixed(5)}</td>
    <td><strong>${(gw.distance_to_train_km || 0).toFixed(1)} km</strong></td>
    <td>${(telem.motion?.speed_kmh || 0).toFixed(1)} km/h</td>
    <td>${(telem.motion?.heading_deg || 0).toFixed(1)}°</td>
    <td>${(telem.device_health?.battery_v || 0).toFixed(2)} V</td>
    <td>${telem.device_health?.temperature_c || 0} °C</td>
    <td>${telem.device_health?.cpu_load_pct || 0}%</td>
    <td class="${isEmerg ? 'cell-emergency' : ''}">${telem.device_health?.status_summary || 'NORMAL'}</td>
    <td>${gw.rx_correlation !== null && gw.rx_correlation !== undefined ? gw.rx_correlation.toFixed(2) : '-'}</td>
    <td class="cell-hex" title="${packet.raw_hex || ''}">${packet.raw_hex || '-'}</td>
  `;

  // Row click opens inspection modal
  row.onclick = () => showDetailModal(packet);

  tbody.appendChild(row);

  // Keep table within 200 items in DOM
  if (tbody.children.length > 200) {
    tbody.removeChild(tbody.firstChild);
  }

  // Auto-scroll
  if (STATE.autoScroll) {
    const wrapper = document.getElementById("table-container");
    if (wrapper) {
      wrapper.scrollTop = wrapper.scrollHeight;
    }
  }
}

// ==============================================================================
// 6. UI CONTROLS & INTERACTIONS
// ==============================================================================
function initUIControls() {
  // 1. Center Gateway
  document.getElementById("btn-center-gw")?.addEventListener("click", () => {
    STATE.map.setView([STATE.gateway.lat, STATE.gateway.lon], 13);
  });

  // 2. Center Train 0x0002
  document.getElementById("btn-center-train2")?.addEventListener("click", () => {
    const t = STATE.trains["0x0002"];
    if (t) STATE.map.setView([t.lat, t.lon], 13);
  });

  // 3. Center Train 0x0003
  document.getElementById("btn-center-train3")?.addEventListener("click", () => {
    const t = STATE.trains["0x0003"];
    if (t) STATE.map.setView([t.lat, t.lon], 13);
  });

  // 4. Fit All
  document.getElementById("btn-fit-all")?.addEventListener("click", () => {
    fitAllPoints();
  });

  // 5. Toggle Tracks
  document.getElementById("toggle-tracks")?.addEventListener("change", (e) => {
    STATE.showTracks = e.target.checked;
    if (STATE.showTracks) {
      STATE.map.addLayer(STATE.layers.tracks);
    } else {
      STATE.map.removeLayer(STATE.layers.tracks);
    }
  });

  // 6. Toggle Vectors
  document.getElementById("toggle-vectors")?.addEventListener("change", (e) => {
    STATE.showVectors = e.target.checked;
    if (STATE.showVectors) {
      STATE.map.addLayer(STATE.layers.vectors);
    } else {
      STATE.map.removeLayer(STATE.layers.vectors);
    }
  });

  // 7. Auto-scroll Toggle
  const btnScroll = document.getElementById("btn-toggle-autoscroll");
  const dotScroll = document.getElementById("autoscroll-dot");
  btnScroll?.addEventListener("click", () => {
    STATE.autoScroll = !STATE.autoScroll;
    btnScroll.innerHTML = `<span class="btn-dot ${STATE.autoScroll ? 'dot-active' : ''}"></span> AUTOSCROLL: ${STATE.autoScroll ? 'ON' : 'OFF'}`;
  });

  // 8. Filter Train
  document.getElementById("filter-train")?.addEventListener("change", (e) => {
    STATE.filterTrain = e.target.value;
    rebuildTable();
  });

  // 9. Export JSON
  document.getElementById("btn-export-json")?.addEventListener("click", () => {
    exportDataAsJson();
  });

  // 10. Modal Close
  document.getElementById("modal-close-btn")?.addEventListener("click", closeModal);
  document.getElementById("detail-modal")?.addEventListener("click", (e) => {
    if (e.target.id === "detail-modal") closeModal();
  });
}

function fitAllPoints() {
  const points = [[STATE.gateway.lat, STATE.gateway.lon]];
  Object.values(STATE.trains).forEach((t) => {
    if (t.lat && t.lon) points.push([t.lat, t.lon]);
  });

  if (points.length > 0) {
    const bounds = L.latLngBounds(points);
    STATE.map.fitBounds(bounds, { padding: [50, 50], maxZoom: 14 });
  }
}

function rebuildTable() {
  const tbody = document.getElementById("logs-table-body");
  if (!tbody) return;
  tbody.innerHTML = "";
  STATE.packets.forEach((p) => appendTableRow(p, false));
}

function showDetailModal(packet) {
  const modal = document.getElementById("detail-modal");
  const title = document.getElementById("modal-title");
  const content = document.getElementById("modal-content");
  if (!modal || !content) return;

  const tid = packet.telemetry?.train_id || "TRAIN";
  title.innerText = `TELEMETRY RECORD // ${tid} @ ${packet.received_time_iso || ''}`;
  content.innerHTML = `<pre class="json-block">${escapeHtml(JSON.stringify(packet, null, 2))}</pre>`;

  modal.classList.add("active");
}

function closeModal() {
  document.getElementById("detail-modal")?.classList.remove("active");
}

function exportDataAsJson() {
  if (STATE.packets.length === 0) {
    alert("Belum ada data paket untuk di-export.");
    return;
  }
  const blob = new Blob([JSON.stringify(STATE.packets, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `railway_telemetry_${new Date().toISOString().substring(0, 19)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function escapeHtml(str) {
  return str.replace(/[&<>'"]/g, 
    tag => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[tag] || tag)
  );
}
