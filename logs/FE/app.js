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
    corridors: null,
    tracks: null,
    points: {},
    vectors: null,
    station: null,
    trains: {},
    trails: {},
    vectorLines: {},
  },
  trainPointHistory: {},
};

const ROUTE_NAMES = {
  "0x0002": "Gambir → Bandung (Jalur Konvensional)",
  "0x0003": "Stasiun Rendeh → Bandung (Segmen 2)",
  "DEFAULT": "Rute Kereta Api Lintas Jawa",
};

const TRAIN_PALETTE = [
  { hex: "#0284c7", badgeClass: "row-node-blue" },
  { hex: "#f59e0b", badgeClass: "row-node-amber" },
  { hex: "#10b981", badgeClass: "row-node-green" },
  { hex: "#8b5cf6", badgeClass: "row-node-purple" },
  { hex: "#ec4899", badgeClass: "row-node-pink" },
  { hex: "#06b6d4", badgeClass: "row-node-teal" },
];

function getTrainColor(trainId) {
  if (trainId === "0x0002") return TRAIN_PALETTE[0];
  if (trainId === "0x0003") return TRAIN_PALETTE[1];
  let hash = 0;
  for (let i = 0; i < trainId.length; i++) {
    hash = (hash * 31 + trainId.charCodeAt(i)) & 0xffffffff;
  }
  const idx = Math.abs(hash) % TRAIN_PALETTE.length;
  return TRAIN_PALETTE[idx];
}

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
  STATE.layers.corridors = L.layerGroup().addTo(STATE.map);
  STATE.layers.tracks = L.layerGroup().addTo(STATE.map);
  STATE.layers.vectors = L.layerGroup().addTo(STATE.map);

  // Dedicated Pane for Route Paths so RF telemetry links always render above tracks
  const routePane = STATE.map.createPane("routePane");
  routePane.style.zIndex = "450";

  // Dedicated Pane for Dynamic Waypoint Dots & Progressive Trails
  const dynamicTrackPane = STATE.map.createPane("dynamicTrackPane");
  dynamicTrackPane.style.zIndex = "440";

  // Add Gateway Station Marker (Stasiun Rendeh)
  renderGatewayMarker();

  // Load Railway Track Corridors from GeoJSON (as clean background reference)
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
    // 1. Load Track Koridor Konvensional (Gambir - Bandung)
    const res1 = await fetch("/api/geojson/jalur_kereta_jakarta_bandung.json");
    if (res1.ok) {
      const geo1 = await res1.json();
      L.geoJSON(geo1, {
        filter: (feature) => feature.properties?.type !== "High Speed Rail",
        style: () => ({
          color: "#94a3b8",
          weight: 2.5,
          opacity: 0.35,
          dashArray: "4, 4",
        }),
        onEachFeature: (feature, layer) => {
          layer.bindTooltip("Koridor Rel Fisik: Jakarta - Bandung (Referensi Jalur)", {
            sticky: true,
            className: "tactical-tooltip",
          });
        },
      }).addTo(STATE.layers.corridors);
    }

    // 2. Load Track Segmen 2 (Titik Tengah ke Bandung)
    const res2 = await fetch("/api/geojson/tengah_bandung_ke_bandung.json");
    if (res2.ok) {
      const geo2 = await res2.json();
      L.geoJSON(geo2, {
        style: () => ({
          color: "#94a3b8",
          weight: 2.5,
          opacity: 0.35,
          dashArray: "4, 4",
        }),
        onEachFeature: (feature, layer) => {
          layer.bindTooltip("Koridor Rel Fisik: Rendeh - Bandung (Referensi Jalur)", {
            sticky: true,
            className: "tactical-tooltip",
          });
        },
      }).addTo(STATE.layers.corridors);
    }
  } catch (err) {
    console.warn("[WARN] Could not load GeoJSON tracks from server:", err);
  }
}

// ==============================================================================
// 4. CIRCULAR BLUE LOCATION MARKERS & DYNAMIC WAYPOINT DOTS
// ==============================================================================
/**
 * Automatically records GPS telemetry points (titik-titik lintasan) dynamically
 * as the train moves, creating interactive dots and a progressive real-time polyline.
 */
function addTrainWaypointDot(trainId, lat, lon, packet) {
  if (!lat || !lon) return;

  const color = getTrainColor(trainId);
  const telem = packet?.telemetry || {};
  const speed = telem.motion?.speed_kmh || 0;
  const heading = telem.motion?.heading_deg || 0;
  const timeStr = (packet?.received_time_iso || "").substring(11, 19) || new Date().toISOString().substring(11, 19);

  // 1. LayerGroup for individual dots
  if (!STATE.layers.points[trainId]) {
    STATE.layers.points[trainId] = L.layerGroup().addTo(STATE.map);
  }

  // 2. Progressive Polyline Trail connecting the dots
  if (!STATE.layers.trails[trainId]) {
    STATE.layers.trails[trainId] = L.polyline([[lat, lon]], {
      pane: "dynamicTrackPane",
      color: color.hex,
      weight: 3.5,
      opacity: 0.95,
    }).addTo(STATE.map);
  } else {
    const latlngs = STATE.layers.trails[trainId].getLatLngs();
    const last = latlngs[latlngs.length - 1];
    // Check if new position has moved sufficiently to avoid redundant duplicate points
    if (!last || Math.abs(last.lat - lat) > 0.00003 || Math.abs(last.lng - lon) > 0.00003) {
      latlngs.push([lat, lon]);
      if (latlngs.length > 400) latlngs.shift();
      STATE.layers.trails[trainId].setLatLngs(latlngs);
    }
  }

  // 3. Add individual Dot (CircleMarker)
  if (!STATE.trainPointHistory[trainId]) STATE.trainPointHistory[trainId] = [];

  const hist = STATE.trainPointHistory[trainId];
  const lastPoint = hist[hist.length - 1];

  // Only add a new visual dot if distance moved is > ~15-20 meters (approx 0.00015 deg) or first point
  const distDelta = lastPoint ? Math.hypot(lat - lastPoint.lat, lon - lastPoint.lon) : 1;
  if (!lastPoint || distDelta > 0.00015) {
    const dot = L.circleMarker([lat, lon], {
      pane: "dynamicTrackPane",
      radius: 4.5,
      fillColor: color.hex,
      color: "#ffffff",
      weight: 1.5,
      opacity: 0.95,
      fillOpacity: 0.9,
    });

    dot.bindTooltip(`
      <div style="font-family: 'Inter', sans-serif; font-size: 11px; color: #0f172a; min-width: 150px;">
        <div style="font-weight: 800; color: ${color.hex}; font-size: 12px; margin-bottom: 2px;">
          Titik Telemetri Kereta ${trainId}
        </div>
        <div style="display: grid; grid-template-columns: 1fr; gap: 2px; background: #f8fafc; padding: 4px 6px; border-radius: 4px; border: 1px solid #e2e8f0; font-size: 10px;">
          <div><strong>Waktu:</strong> ${timeStr} UTC</div>
          <div><strong>Kecepatan:</strong> ${speed.toFixed(1)} km/h</div>
          <div><strong>Arah Haluan:</strong> ${heading.toFixed(1)}°</div>
          <div><strong>GPS:</strong> ${lat.toFixed(5)}, ${lon.toFixed(5)}</div>
        </div>
      </div>
    `, {
      sticky: true,
      className: "tactical-tooltip",
    });

    dot.addTo(STATE.layers.points[trainId]);
    hist.push({ lat, lon, marker: dot });

    // Keep up to 200 visual dots per train to maintain high performance
    if (hist.length > 200) {
      const oldest = hist.shift();
      if (oldest?.marker) {
        STATE.layers.points[trainId].removeLayer(oldest.marker);
      }
    }
  }
}

function updateTrainMarker(trainId, lat, lon, heading, speed, packet = null) {
  const trainColor = getTrainColor(trainId);
  const isAmber = trainId === "0x0003";
  const markerClass = isAmber ? "circular-blue-train-marker amber-train" : "circular-blue-train-marker";
  const customBgStyle = (!isAmber && trainId !== "0x0002") ? `style="background: ${trainColor.hex};"` : "";

  // 1. Circular Blue Location Marker with Train Silhouette Icon
  const markerHtml = `
    <div class="${markerClass}" ${customBgStyle} title="Train ${trainId} (${speed} km/h)">
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
        <strong style="color: ${trainColor.hex}; font-size: 13px;">Train ${trainId}</strong>
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

  // 2. Auto nambah titik telemetri dan track dinamis (jangan static)
  addTrainWaypointDot(trainId, lat, lon, packet);

  // 3. Jalur Route Path (RF Telemetri Link ke Gateway / Hopping)
  updateRoutePathLine(trainId, lat, lon, packet);
}

/**
 * Calculates a curved path (quadratic Bezier) between two lat/lon points.
 * Simulates atmospheric RF radio propagation arc and ensures lines never
 * get buried underneath physical railway tracks.
 */
function computeCurvedPath(p1, p2, bend = 0.15, numPoints = 25) {
  const [lat1, lon1] = p1;
  const [lat2, lon2] = p2;

  const dLat = lat2 - lat1;
  const dLon = lon2 - lon1;
  const dist = Math.hypot(dLat, dLon);

  if (dist < 0.0001) return [p1, p2];

  // Midpoint
  const midLat = (lat1 + lat2) / 2;
  const midLon = (lon1 + lon2) / 2;

  // Perpendicular normal vector: (-dLon, dLat)
  const normLat = -dLon / dist;
  const normLon = dLat / dist;

  // Control point displaced perpendicularly proportional to distance
  const ctrlLat = midLat + normLat * dist * bend;
  const ctrlLon = midLon + normLon * dist * bend;

  const points = [];
  for (let i = 0; i <= numPoints; i++) {
    const t = i / numPoints;
    const inv = 1 - t;
    const bLat = inv * inv * lat1 + 2 * inv * t * ctrlLat + t * t * lat2;
    const bLon = inv * inv * lon1 + 2 * inv * t * ctrlLon + t * t * lon2;
    points.push([bLat, bLon]);
  }
  return points;
}

function updateRoutePathLine(trainId, lat, lon, packet) {
  const isRelayed = Boolean(packet?.packet?.relayed || packet?.mesh_routing?.relayed);

  // High-contrast blue matching Legenda for direct RF telemetry link,
  // or vibrant amber/orange when hopping through relay.
  const pathColor = isRelayed ? "#f59e0b" : "#0284c7";
  const gwPos = [STATE.gateway.lat, STATE.gateway.lon];
  const trainPos = [lat, lon];

  let curvedCoords = [];
  let routeLabel = `Jalur Route Path: Train ${trainId} ➔ Gateway (Direct RF Link)`;

  if (isRelayed) {
    const relayId = trainId === "0x0003" ? "0x0002" : "0x0003";
    if (STATE.trains[relayId] && STATE.trains[relayId].lat && STATE.trains[relayId].lon) {
      const relayPos = [STATE.trains[relayId].lat, STATE.trains[relayId].lon];
      const arc1 = computeCurvedPath(trainPos, relayPos, 0.12);
      const arc2 = computeCurvedPath(relayPos, gwPos, -0.12);
      curvedCoords = arc1.concat(arc2.slice(1));
      routeLabel = `Jalur Route Path (Hopping): Train ${trainId} ➔ Relay ${relayId} ➔ Gateway (1 Hop)`;
    } else {
      curvedCoords = computeCurvedPath(trainPos, gwPos, trainId === "0x0003" ? 0.20 : 0.10);
    }
  } else {
    // Bend outward so RF beam arches cleanly above the physical ground railway track
    const bendFactor = trainId === "0x0003" ? 0.20 : 0.10;
    curvedCoords = computeCurvedPath(trainPos, gwPos, bendFactor);
  }

  if (!STATE.layers.vectorLines[trainId]) {
    STATE.layers.vectorLines[trainId] = L.polyline(curvedCoords, {
      pane: "routePane",
      color: pathColor,
      weight: 3.0,
      opacity: 0.95,
      dashArray: isRelayed ? "4, 6" : "6, 6",
    }).addTo(STATE.layers.vectors);

    STATE.layers.vectorLines[trainId].bindTooltip(routeLabel, {
      sticky: true,
      className: "tactical-tooltip",
    });
  } else {
    STATE.layers.vectorLines[trainId].setLatLngs(curvedCoords);
    STATE.layers.vectorLines[trainId].setStyle({
      color: pathColor,
      weight: 3.0,
      opacity: 0.95,
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

  updateLegendaPanel();
  updateTrainFilterOptions();
}

function updateLegendaPanel() {
  const container = document.getElementById("legenda-trains-list");
  if (!container) return;

  const tids = Object.keys(STATE.trains);
  if (tids.length === 0) {
    container.innerHTML = `<div class="legenda-row" style="color: #94a3b8; font-size: 11px;">Menunggu titik telemetri armada...</div>`;
    return;
  }

  container.innerHTML = tids.map((tid) => {
    const color = getTrainColor(tid);
    return `
      <div class="legenda-row">
        <span class="legenda-marker-dot" style="background: ${color.hex};"></span>
        <span class="legenda-color" style="background: ${color.hex};"></span>
        <span>Titik & Track Kereta ${tid}</span>
      </div>
    `;
  }).join("");
}

function updateTrainFilterOptions() {
  const select = document.getElementById("filter-train-drawer");
  if (!select) return;

  const currentVal = select.value;
  const tids = Object.keys(STATE.trains);

  let html = `<option value="ALL">Semua Kereta</option>`;
  tids.forEach((tid) => {
    html += `<option value="${tid}" ${tid === currentVal ? "selected" : ""}>Train ${tid}</option>`;
  });
  select.innerHTML = html;
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
    const color = getTrainColor(tid);
    const dotStyle = `background: ${color.hex};`;
    const route = ROUTE_NAMES[tid] || ROUTE_NAMES["DEFAULT"];
    const isEmerg = t.health?.is_emergency;
    const timeShort = t.received_time ? t.received_time.substring(11, 19) : "--:--:--";

    const item = document.createElement("div");
    item.className = "journey-item-card";
    item.innerHTML = `
      <div class="journey-header">
        <div>
          <div class="journey-train-title">
            <span class="journey-indicator-dot" style="${dotStyle}"></span>
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
          <span class="m-val" style="color: ${color.hex};">
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
  const color = getTrainColor(tid);
  const nodeClass = color.badgeClass || (tid === "0x0002" ? "row-node-blue" : (tid === "0x0003" ? "row-node-amber" : ""));
  const nodeStyle = color.badgeClass ? "" : `style="color: ${color.hex}; font-weight: 700;"`;
  const isEmerg = telem.device_health?.is_emergency;
  const isRelayed = Boolean(packet.packet?.relayed || packet.mesh_routing?.relayed);
  const routeStr = packet.packet?.route_str || packet.mesh_routing?.route_str || (isRelayed ? `${tid} ➔ 0x0002 ➔ GW` : `${tid} ➔ GW`);
  const routeBadge = isRelayed
    ? `<span style="background:#fef3c7; color:#b45309; font-weight:700; padding:2px 6px; border-radius:4px;" title="${routeStr}">HOPPED</span>`
    : `<span style="background:#dcfce7; color:#15803d; font-weight:700; padding:2px 6px; border-radius:4px;" title="${routeStr}">DIRECT</span>`;

  row.innerHTML = `
    <td>${timeStr}</td>
    <td class="${nodeClass}" ${nodeStyle}>${tid}</td>
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
