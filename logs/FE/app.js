/**
 * 168Railway & AIS // Live Train Tracking Client Application.
 * Clean White / Light Grey Minimalist Interface with High-Contrast
 * Satellite Imagery Map, Circular Blue Location Markers, and Real-Time SSE Telemetry.
 */

// ==============================================================================
// 1. STATE STORE & CONFIGURATION
// ==============================================================================
const STATE = {
  gateway: {
    station_name: "Titik Tengah Utama (Stasiun Rendeh)",
    lat: -6.58025,
    lon: 107.24695,
  },
  trains: {},          // Map of train_id -> latest telemetry object
  packets: [],         // Array of all received telemetry packets
  autoScroll: true,
  filterTrain: "ALL",
  eventSource: null,
  pollTimer: null,
  map: null,
  activeBasemap: "satellite", // "satellite" or "dark"
  baseLayers: {},
  layers: {
    tracks: null,
    vectors: null,
    station: null,
    trains: {},
    trails: {},
    vectorLines: {},
  },
};

const ROUTE_NAMES = {
  "0x0002": "Gambir → Bandung (Jalur Konvensional)",
  "0x0003": "Stasiun Rendeh → Bandung (Segmen 2)",
  "DEFAULT": "Rute Kereta Api Lintas Jawa",
};

// ==============================================================================
// 2. INITIALIZATION
// ==============================================================================
document.addEventListener("DOMContentLoaded", () => {
  initLiveClock();
  initMap();
  initUIControls();
  loadInitialData();
  startLiveStream();
});

// Real-Time Clock in Top-Right Widget
function initLiveClock() {
  const clockEl = document.getElementById("clock-display");
  function tick() {
    const now = new Date();
    if (clockEl) {
      clockEl.innerText = now.toTimeString().substring(0, 8);
    }
  }
  tick();
  setInterval(tick, 1000);
}

// ==============================================================================
// 3. MAP SUBSYSTEM (HIGH-CONTRAST SATELLITE MAP)
// ==============================================================================
function initMap() {
  // Center on Titik Tengah (Stasiun Rendeh)
  STATE.map = L.map("tactical-map", {
    zoomControl: false,
    attributionControl: false,
  }).setView([STATE.gateway.lat, STATE.gateway.lon], 10);

  // Position custom zoom control on bottom-right (above floating log button)
  L.control.zoom({ position: "bottomright" }).addTo(STATE.map);

  // High-Contrast Geographic Satellite Imagery Layer (Esri World Imagery)
  STATE.baseLayers.satellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 18 }
  );

  // Fallback / Alternative Dark Matter Layer (CartoDB)
  STATE.baseLayers.dark = L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    { maxZoom: 19, subdomains: "abcd" }
  );

  // Set default basemap to Satellite
  STATE.baseLayers.satellite.addTo(STATE.map);

  // Layer groups
  STATE.layers.tracks = L.layerGroup().addTo(STATE.map);
  STATE.layers.vectors = L.layerGroup().addTo(STATE.map);

  // Add Gateway Station Marker (Stasiun Rendeh)
  renderGatewayMarker();

  // Load Railway Track Corridors from GeoJSON
  loadRailwayCorridors();
}

function renderGatewayMarker() {
  const stationIcon = L.divIcon({
    className: "custom-station-wrapper",
    html: `
      <div class="circular-station-marker" title="${STATE.gateway.station_name}">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M4 11a8 8 0 0 1 16 0c0 4.418-8 11-8 11s-8-6.582-8-11z"></path>
          <circle cx="12" cy="11" r="2.5"></circle>
        </svg>
      </div>
    `,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
  });

  STATE.layers.station = L.marker([STATE.gateway.lat, STATE.gateway.lon], { icon: stationIcon })
    .addTo(STATE.map)
    .bindPopup(`
      <div style="font-family: 'Inter', sans-serif; font-size: 12px; color: #0f172a; min-width: 180px;">
        <div style="font-weight: 800; color: #16a34a; font-size: 13px; margin-bottom: 2px;">
          ${STATE.gateway.station_name}
        </div>
        <div style="color: #64748b; font-size: 11px; margin-bottom: 6px;">Titik Tengah Utama Gateway</div>
        <div style="background: #f8fafc; padding: 6px; border-radius: 6px; border: 1px solid #e2e8f0; font-family: monospace;">
          GPS: ${STATE.gateway.lat}, ${STATE.gateway.lon}<br/>
          Status: Ingest LoRa SDR Aktif
        </div>
      </div>
    `);
}

async function loadRailwayCorridors() {
  try {
    // 1. Load Track Kereta 0x0002 (Gambir - Bandung)
    // Filter out Whoosh (High Speed Rail) so only the active train track is shown
    const res1 = await fetch("/api/geojson/jalur_kereta_jakarta_bandung.json");
    if (res1.ok) {
      const geo1 = await res1.json();
      L.geoJSON(geo1, {
        filter: (feature) => feature.properties?.type !== "High Speed Rail",
        style: () => ({
          color: "#38bdf8",
          weight: 4,
          opacity: 0.9,
          dashArray: "6, 4",
        }),
        onEachFeature: (feature, layer) => {
          layer.bindTooltip("Track Kereta 0x0002 (Gambir - Bandung)", {
            sticky: true,
            className: "tactical-tooltip",
          });
        },
      }).addTo(STATE.layers.tracks);
    }

    // 2. Load Track Kereta 0x0003: Segmen 2 (Titik Tengah ke Bandung)
    const res2 = await fetch("/api/geojson/tengah_bandung_ke_bandung.json");
    if (res2.ok) {
      const geo2 = await res2.json();
      L.geoJSON(geo2, {
        style: () => ({
          color: "#facc15",
          weight: 4,
          opacity: 0.9,
          dashArray: "6, 6",
        }),
        onEachFeature: (feature, layer) => {
          layer.bindTooltip("Track Kereta 0x0003 (Stasiun Rendeh - Bandung)", {
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

// ==============================================================================
// 4. CIRCULAR BLUE LOCATION MARKERS & ROUTE PATH
// ==============================================================================
function updateTrainMarker(trainId, lat, lon, heading, speed, packet = null) {
  const isAmber = trainId === "0x0003";
  const markerClass = isAmber ? "circular-blue-train-marker amber-train" : "circular-blue-train-marker";
  const trailColor = isAmber ? "#eab308" : "#0284c7";

  // 1. Circular Blue Location Marker with Train Silhouette Icon
  const markerHtml = `
    <div class="${markerClass}" title="Train ${trainId} (${speed} km/h)">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="4" y="3" width="16" height="16" rx="3"></rect>
        <path d="M4 11h16"></path>
        <circle cx="8" cy="15" r="1.5" fill="currentColor"></circle>
        <circle cx="16" cy="15" r="1.5" fill="currentColor"></circle>
        <path d="M8 19l-2 3M16 19l2 3"></path>
      </svg>
    </div>
  `;

  const customIcon = L.divIcon({
    className: "custom-marker-wrapper",
    html: markerHtml,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
  });

  const popupContent = `
    <div style="font-family: 'Inter', sans-serif; font-size: 12px; color: #0f172a; min-width: 190px;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
        <strong style="color: ${trailColor}; font-size: 13px;">Train ${trainId}</strong>
        <span style="background: #dcfce7; color: #15803d; font-weight: 700; font-size: 10px; padding: 2px 6px; border-radius: 9999px;">ON TRACK</span>
      </div>
      <div style="color: #64748b; font-size: 11px; margin-bottom: 8px;">
        ${ROUTE_NAMES[trainId] || ROUTE_NAMES["DEFAULT"]}
      </div>
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; background: #f8fafc; padding: 6px; border-radius: 6px; border: 1px solid #e2e8f0; font-size: 11px;">
        <div><strong>Kecepatan:</strong><br/>${speed} km/h</div>
        <div><strong>Arah Haluan:</strong><br/>${heading.toFixed(1)}°</div>
      </div>
    </div>
  `;

  if (!STATE.layers.trains[trainId]) {
    STATE.layers.trains[trainId] = L.marker([lat, lon], { icon: customIcon })
      .addTo(STATE.map)
      .bindPopup(popupContent)
      .bindTooltip(`<strong>Train ${trainId}</strong> • ${speed.toFixed(0)} km/h`, {
        direction: "top",
        offset: [0, -18],
        className: "train-map-label",
      });
  } else {
    STATE.layers.trains[trainId].setLatLng([lat, lon]);
    STATE.layers.trains[trainId].setIcon(customIcon);
    STATE.layers.trains[trainId].setPopupContent(popupContent);
    STATE.layers.trains[trainId].setTooltipContent(`<strong>Train ${trainId}</strong> • ${speed.toFixed(0)} km/h`);
  }

  // 2. Jalur Route Path (RF Telemetri Link ke Gateway / Hopping)
  updateRoutePathLine(trainId, lat, lon, packet);
}

function updateRoutePathLine(trainId, lat, lon, packet) {
  const isAmber = trainId === "0x0003";
  const pathColor = isAmber ? "#eab308" : "#0284c7";
  const isRelayed = Boolean(packet?.packet?.relayed || packet?.mesh_routing?.relayed);

  let coords = [[lat, lon]];
  let routeLabel = `Jalur Route Path: Train ${trainId} ➔ Gateway (Direct)`;

  if (isRelayed) {
    const relayId = trainId === "0x0003" ? "0x0002" : "0x0003";
    if (STATE.trains[relayId] && STATE.trains[relayId].lat && STATE.trains[relayId].lon) {
      coords.push([STATE.trains[relayId].lat, STATE.trains[relayId].lon]);
      routeLabel = `Jalur Route Path (Hopping): Train ${trainId} ➔ Relay ${relayId} ➔ Gateway (1 Hop)`;
    }
  }
  coords.push([STATE.gateway.lat, STATE.gateway.lon]);

  if (!STATE.layers.vectorLines[trainId]) {
    STATE.layers.vectorLines[trainId] = L.polyline(coords, {
      color: isRelayed ? "#f59e0b" : pathColor,
      weight: isRelayed ? 2.5 : 2.0,
      opacity: 0.8,
      dashArray: isRelayed ? "4, 6" : "6, 6",
    }).addTo(STATE.layers.vectors);

    STATE.layers.vectorLines[trainId].bindTooltip(routeLabel, {
      sticky: true,
      className: "tactical-tooltip",
    });
  } else {
    STATE.layers.vectorLines[trainId].setLatLngs(coords);
    STATE.layers.vectorLines[trainId].setStyle({
      color: isRelayed ? "#f59e0b" : pathColor,
      weight: isRelayed ? 2.5 : 2.0,
      dashArray: isRelayed ? "4, 6" : "6, 6",
    });
    STATE.layers.vectorLines[trainId].setTooltipContent(routeLabel);
  }
}

// ==============================================================================
// 5. DATA INGESTION & SSE REAL-TIME STREAMING
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
    }

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
      updateStreamIndicator(true);
    };

    STATE.eventSource.onerror = () => {
      updateStreamIndicator(false);
      triggerPollingFallback();
    };
  } catch (err) {
    triggerPollingFallback();
  }
}

function updateStreamIndicator(online) {
  const btn = document.getElementById("btn-live-stream-toggle");
  if (!btn) return;
  if (online) {
    btn.innerHTML = '<span class="live-dot-green"></span> STREAM ACTIVE';
    btn.style.background = "#22c55e";
  } else {
    btn.innerHTML = '<span class="live-dot-green" style="background:#f59e0b"></span> RECONNECTING';
    btn.style.background = "#d97706";
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
// 6. TELEMETRY PACKET PROCESSING & UI UPDATES
// ==============================================================================
function processTelemetryPacket(packet, isLive = true) {
  if (!packet || !packet.telemetry) return;

  const telem = packet.telemetry;
  const tid = telem.train_id;
  if (!tid) return;

  STATE.packets.push(packet);
  if (STATE.packets.length > 500) STATE.packets.shift();

  const isRelayed = Boolean(packet.packet?.relayed || packet.mesh_routing?.relayed);
  const routeStr = packet.packet?.route_str || packet.mesh_routing?.route_str || (isRelayed ? `${tid} ➔ 0x0002 ➔ GW` : `${tid} ➔ GW`);

  // Save latest state
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
    relayed: isRelayed,
    route_str: routeStr,
  };

  // Update map marker & route path line
  updateTrainMarker(
    tid,
    STATE.trains[tid].lat,
    STATE.trains[tid].lon,
    STATE.trains[tid].heading,
    STATE.trains[tid].speed,
    packet
  );

  // Update DOM badges & counters
  updateBadgesAndCounters();

  // Render floating Active Journeys card
  renderActiveJourneysCard();

  // Append row to slideup telemetry drawer
  appendDrawerTableRow(packet, isLive);
}

function updateBadgesAndCounters() {
  const trainCount = Object.keys(STATE.trains).length;
  const packetCount = STATE.packets.length;

  // Header & sidebar badges
  const badgeTrains = document.getElementById("nav-badge-trains");
  const badgeEvents = document.getElementById("nav-badge-events");
  const sidebarTrains = document.getElementById("sidebar-badge-trains");
  const sidebarPackets = document.getElementById("sidebar-badge-packets");
  const topCounter = document.getElementById("trains-active-count");
  const cardCounter = document.getElementById("card-trains-count");

  if (badgeTrains) badgeTrains.innerText = trainCount;
  if (sidebarTrains) sidebarTrains.innerText = trainCount;
  if (topCounter) topCounter.innerText = `${trainCount} trains`;
  if (cardCounter) cardCounter.innerText = trainCount;

  if (badgeEvents) badgeEvents.innerText = packetCount;
  if (sidebarPackets) sidebarPackets.innerText = packetCount;
}

function renderActiveJourneysCard() {
  const container = document.getElementById("journeys-list-container");
  if (!container) return;

  const trainKeys = Object.keys(STATE.trains);
  if (trainKeys.length === 0) {
    container.innerHTML = `
      <div style="padding: 20px; text-align: center; color: #94a3b8; font-size: 12px;">
        Menunggu siaran telemetri dari armada kereta...
      </div>
    `;
    return;
  }

  container.innerHTML = "";

  trainKeys.forEach((tid) => {
    const t = STATE.trains[tid];
    const isAmber = tid === "0x0003";
    const dotClass = isAmber ? "dot-amber-journey" : "dot-blue-journey";
    const route = ROUTE_NAMES[tid] || ROUTE_NAMES["DEFAULT"];
    const isEmerg = t.health?.is_emergency;
    const timeShort = t.received_time ? t.received_time.substring(11, 19) : "--:--:--";

    const item = document.createElement("div");
    item.className = "journey-item-card";
    item.innerHTML = `
      <div class="journey-header">
        <div>
          <div class="journey-train-title">
            <span class="journey-indicator-dot ${dotClass}"></span>
            <span>Train ${tid}</span>
          </div>
          <div class="journey-route-text">${route}</div>
        </div>
        <span class="journey-status-pill ${isEmerg ? 'emergency' : ''}">
          ${isEmerg ? 'EMERGENCY' : 'On Track'}
        </span>
      </div>

      <div class="journey-metrics-row">
        <div class="metric-col">
          <span class="m-label">KECEPATAN</span>
          <span class="m-val">${t.speed.toFixed(1)} <span>km/h</span></span>
        </div>
        <div class="metric-col">
          <span class="m-label">JARAK KE GATEWAY</span>
          <span class="m-val" style="color: ${isAmber ? '#b45309' : '#0284c7'};">
            ${t.dist_gw.toFixed(1)} <span>km</span>
          </span>
        </div>
      </div>

      <div class="journey-health-pills">
        <span class="health-pill-tag">Bat: ${(t.health?.battery_v || 0).toFixed(2)}V</span>
        <span class="health-pill-tag">Suhu: ${t.health?.temperature_c || 0}°C</span>
        <span class="health-pill-tag">CPU: ${t.health?.cpu_load_pct || 0}%</span>
        <span class="health-pill-tag" title="Jalur transmisi: ${t.route_str}" style="${t.relayed ? 'color: #b45309; font-weight: 700;' : ''}">${t.relayed ? '1 Hop' : 'Direct'}</span>
        <span class="health-pill-tag" style="margin-left: auto;">${timeShort} UTC</span>
      </div>
    `;

    item.onclick = () => {
      STATE.map.setView([t.lat, t.lon], 13);
      if (STATE.layers.trains[tid]) {
        STATE.layers.trains[tid].openPopup();
      }
    };

    container.appendChild(item);
  });
}

function appendDrawerTableRow(packet, isLive = true) {
  const tbody = document.getElementById("drawer-logs-body");
  if (!tbody) return;

  const telem = packet.telemetry || {};
  const gw = packet.gateway || {};
  const tid = telem.train_id || "UNKNOWN";

  if (STATE.filterTrain !== "ALL" && STATE.filterTrain !== tid) {
    return;
  }

  const row = document.createElement("tr");
  if (isLive) row.className = "new-row";

  const timeStr = (packet.received_time_iso || "").substring(11, 19) || "--:--:--";
  const nodeClass = tid === "0x0002" ? "row-node-blue" : (tid === "0x0003" ? "row-node-amber" : "");
  const isEmerg = telem.device_health?.is_emergency;
  const isRelayed = Boolean(packet.packet?.relayed || packet.mesh_routing?.relayed);
  const routeStr = packet.packet?.route_str || packet.mesh_routing?.route_str || (isRelayed ? `${tid} ➔ 0x0002 ➔ GW` : `${tid} ➔ GW`);
  const routeBadge = isRelayed
    ? `<span style="background:#fef3c7; color:#b45309; font-weight:700; padding:2px 6px; border-radius:4px;" title="${routeStr}">HOPPED</span>`
    : `<span style="background:#dcfce7; color:#15803d; font-weight:700; padding:2px 6px; border-radius:4px;" title="${routeStr}">DIRECT</span>`;

  row.innerHTML = `
    <td>${timeStr}</td>
    <td class="${nodeClass}">${tid}</td>
    <td>${(telem.gps?.latitude || 0).toFixed(5)}, ${(telem.gps?.longitude || 0).toFixed(5)}</td>
    <td><strong>${(gw.distance_to_train_km || 0).toFixed(1)} km</strong></td>
    <td>${(telem.motion?.speed_kmh || 0).toFixed(1)} km/h</td>
    <td>${(telem.motion?.heading_deg || 0).toFixed(1)}°</td>
    <td>${(telem.device_health?.battery_v || 0).toFixed(2)} V</td>
    <td>${telem.device_health?.temperature_c || 0} °C</td>
    <td>${telem.device_health?.cpu_load_pct || 0}%</td>
    <td style="${isEmerg ? 'color:#ef4444; font-weight:700;' : ''}">${telem.device_health?.status_summary || 'NORMAL'}</td>
    <td>${gw.rx_correlation !== null && gw.rx_correlation !== undefined ? gw.rx_correlation.toFixed(2) : '-'}</td>
    <td>${routeBadge}</td>
    <td style="color:#64748b; font-size:11px;" title="${packet.raw_hex || ''}">${packet.raw_hex || '-'}</td>
  `;

  row.onclick = () => showDetailModal(packet);

  tbody.appendChild(row);

  if (tbody.children.length > 250) {
    tbody.removeChild(tbody.firstChild);
  }

  if (STATE.autoScroll) {
    const wrapper = document.getElementById("drawer-table-container");
    if (wrapper) wrapper.scrollTop = wrapper.scrollHeight;
  }
}

// ==============================================================================
// 7. UI CONTROLS & INTERACTIVE LISTENERS
// ==============================================================================
function initUIControls() {
  // 1. Toggle Satellite vs Dark Basemap
  document.getElementById("btn-toggle-basemap")?.addEventListener("click", () => {
    if (STATE.activeBasemap === "satellite") {
      STATE.map.removeLayer(STATE.baseLayers.satellite);
      STATE.baseLayers.dark.addTo(STATE.map);
      STATE.activeBasemap = "dark";
    } else {
      STATE.map.removeLayer(STATE.baseLayers.dark);
      STATE.baseLayers.satellite.addTo(STATE.map);
      STATE.activeBasemap = "satellite";
    }
  });

  // 2. Collapse / Expand / Close Active Journeys Card
  const cardWidget = document.getElementById("trains-floating-card");
  const btnCollapse = document.getElementById("btn-collapse-journeys");
  const btnCloseJourneys = document.getElementById("btn-close-journeys");
  const btnDinasan = document.getElementById("sidebar-btn-dinasan");

  btnCollapse?.addEventListener("click", () => {
    cardWidget?.classList.toggle("collapsed");
    btnCollapse.innerText = cardWidget?.classList.contains("collapsed") ? "+" : "−";
  });

  btnCloseJourneys?.addEventListener("click", () => {
    cardWidget?.classList.add("hidden");
    btnDinasan?.classList.remove("active");
  });

  // 3. Sidebar Button: Dinasan (Toggle Card Visibility)
  btnDinasan?.addEventListener("click", () => {
    if (!cardWidget) return;
    const isHidden = cardWidget.classList.contains("hidden");
    if (isHidden) {
      cardWidget.classList.remove("hidden");
      btnDinasan.classList.add("active");
    } else {
      cardWidget.classList.add("hidden");
      btnDinasan.classList.remove("active");
    }
  });

  // 4. Sidebar Button: Info Lintas -> Opens Slideup Drawer
  document.getElementById("sidebar-btn-info")?.addEventListener("click", toggleDrawer);
  document.getElementById("btn-toggle-log-drawer")?.addEventListener("click", toggleDrawer);
  document.getElementById("nav-telemetry-log")?.addEventListener("click", toggleDrawer);
  document.getElementById("nav-info-lintas")?.addEventListener("click", toggleDrawer);
  document.getElementById("btn-close-drawer")?.addEventListener("click", closeDrawer);

  // 5. Sidebar Button: Legenda Dropdown (Flyout toggle)
  const legendaPanel = document.getElementById("legenda-panel");
  const btnLegenda = document.getElementById("sidebar-btn-legenda");
  btnLegenda?.addEventListener("click", () => {
    legendaPanel?.classList.toggle("open");
    btnLegenda.classList.toggle("active");
  });

  // 6. Fit All Overview
  document.getElementById("btn-fit-overview")?.addEventListener("click", fitAllPoints);
  document.getElementById("nav-live-map")?.addEventListener("click", fitAllPoints);
  document.getElementById("nav-koridor")?.addEventListener("click", fitAllPoints);

  // 7. Focus Gateway (Stasiun Rendeh)
  document.getElementById("btn-focus-gateway")?.addEventListener("click", () => {
    STATE.map.setView([STATE.gateway.lat, STATE.gateway.lon], 13);
    STATE.layers.station?.openPopup();
  });
  document.getElementById("btn-toggle-gateway")?.addEventListener("click", () => {
    STATE.map.setView([STATE.gateway.lat, STATE.gateway.lon], 13);
    STATE.layers.station?.openPopup();
  });
  document.getElementById("nav-stasiun")?.addEventListener("click", () => {
    STATE.map.setView([STATE.gateway.lat, STATE.gateway.lon], 13);
    STATE.layers.station?.openPopup();
  });

  // 8. Close Bottom Service Pill
  document.getElementById("btn-close-pill")?.addEventListener("click", () => {
    const pill = document.querySelector(".bottom-service-pill");
    if (pill) pill.style.display = "none";
  });

  // 9. Drawer Autoscroll Toggle
  const btnScroll = document.getElementById("btn-toggle-autoscroll");
  const dotScroll = document.getElementById("autoscroll-dot");
  btnScroll?.addEventListener("click", () => {
    STATE.autoScroll = !STATE.autoScroll;
    btnScroll.innerHTML = `<span class="${STATE.autoScroll ? 'dot-active' : ''}"></span> Autoscroll: ${STATE.autoScroll ? 'ON' : 'OFF'}`;
  });

  // 10. Drawer Filter
  document.getElementById("filter-train-drawer")?.addEventListener("change", (e) => {
    STATE.filterTrain = e.target.value;
    rebuildDrawerTable();
  });

  // 11. Search Box
  document.getElementById("train-search-box")?.addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    if (!q) return;
    for (const tid of Object.keys(STATE.trains)) {
      if (tid.toLowerCase().includes(q)) {
        const t = STATE.trains[tid];
        STATE.map.setView([t.lat, t.lon], 13);
        STATE.layers.trains[tid]?.openPopup();
        break;
      }
    }
  });

  // 12. Export JSON
  document.getElementById("btn-export-nav")?.addEventListener("click", exportDataAsJson);
  document.getElementById("btn-drawer-export")?.addEventListener("click", exportDataAsJson);

  // 13. Modal Close
  document.getElementById("modal-close-btn")?.addEventListener("click", closeModal);
  document.getElementById("detail-modal")?.addEventListener("click", (e) => {
    if (e.target.id === "detail-modal") closeModal();
  });
}

function toggleDrawer() {
  document.getElementById("telemetry-drawer")?.classList.toggle("open");
}

function closeDrawer() {
  document.getElementById("telemetry-drawer")?.classList.remove("open");
}

function fitAllPoints() {
  const points = [[STATE.gateway.lat, STATE.gateway.lon]];
  Object.values(STATE.trains).forEach((t) => {
    if (t.lat && t.lon) points.push([t.lat, t.lon]);
  });

  if (points.length > 0) {
    const bounds = L.latLngBounds(points);
    STATE.map.fitBounds(bounds, { padding: [60, 60], maxZoom: 14 });
  }
}

function rebuildDrawerTable() {
  const tbody = document.getElementById("drawer-logs-body");
  if (!tbody) return;
  tbody.innerHTML = "";
  STATE.packets.forEach((p) => appendDrawerTableRow(p, false));
}

function showDetailModal(packet) {
  const modal = document.getElementById("detail-modal");
  const title = document.getElementById("modal-title");
  const content = document.getElementById("modal-content");
  if (!modal || !content) return;

  const tid = packet.telemetry?.train_id || "TRAIN";
  title.innerText = `TELEMETRI AIS // ${tid} @ ${packet.received_time_iso || ''}`;
  content.innerHTML = `<pre class="clean-json-pre">${escapeHtml(JSON.stringify(packet, null, 2))}</pre>`;

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
