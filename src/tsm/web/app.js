// ==============================================================================
// TSM-Net SG - Cyber-HUD Client Application & Spectrum Canvas Engine
// Pure Vanilla ES6 Javascript - Zero dependencies - Client-Side GPU Rendering
// ==============================================================================

(function() {
  'use strict';

  // Canvas elements
  const specCanvas = document.getElementById('spectrum-canvas');
  const specCtx = specCanvas.getContext('2d');

  const wfCanvas = document.getElementById('waterfall-canvas');
  const wfCtx = wfCanvas.getContext('2d');

  // DOM Elements
  const nodeIdEl = document.getElementById('node-id');
  const meshIpEl = document.getElementById('mesh-ip');
  const memUsageEl = document.getElementById('mem-usage');
  const uptimeEl = document.getElementById('uptime-display');
  const connIndicator = document.getElementById('conn-indicator');
  const sdrBadge = document.getElementById('sdr-status-badge');
  const rssiValEl = document.getElementById('rssi-val');
  const rssiBarEl = document.getElementById('rssi-bar');
  const tempValEl = document.getElementById('temp-val');
  const rxgainEl = document.getElementById('rxgain-val');
  const txgainEl = document.getElementById('txgain-val');
  const txFramesEl = document.getElementById('tx-frames');
  const rxFramesEl = document.getElementById('rx-frames');
  const crcDropsEl = document.getElementById('crc-drops');
  const neighborsCountEl = document.getElementById('neighbors-count-badge');
  const neighborsListEl = document.getElementById('neighbors-list');
  const chatFeedEl = document.getElementById('chat-feed');
  const chatForm = document.getElementById('chat-form');
  const chatTargetInput = document.getElementById('chat-target');
  const chatTextInput = document.getElementById('chat-text');
  const pingBtn = document.getElementById('btn-ping');
  const pingTargetInput = document.getElementById('ping-target');
  const pingLossEl = document.getElementById('ping-loss');
  const pingRttEl = document.getElementById('ping-rtt');
  const pingStatusEl = document.getElementById('ping-status');
  const peakFreqEl = document.getElementById('peak-freq');
  const peakPowerEl = document.getElementById('peak-power');

  // State
  let peakHold = new Float32Array(256).fill(-105);
  let isPaused = false;

  // ----------------------------------------------------------------------------
  // 1. Colormap for Waterfall (SDR Reference Palette)
  // Maps 0..255 uint8 power levels to Deep Navy -> Cyan -> Green -> Amber -> Crimson -> White
  // ----------------------------------------------------------------------------
  const colormap = new Uint32Array(256);
  (function initColormap() {
    for (let i = 0; i < 256; i++) {
      const norm = i / 255.0;
      let r = 0, g = 0, b = 0;

      if (norm < 0.22) {
        // Deep Navy / Midnight Blue to Royal Blue (Noise floor)
        const t = norm / 0.22;
        r = Math.floor(4 + t * 6);
        g = Math.floor(18 + t * 45);
        b = Math.floor(55 + t * 125);
      } else if (norm < 0.38) {
        // Royal Blue to Cyan
        const t = (norm - 0.22) / 0.16;
        r = Math.floor(10 * (1 - t));
        g = Math.floor(63 + t * 155);
        b = Math.floor(180 + t * 75);
      } else if (norm < 0.54) {
        // Cyan to Vivid Emerald / Chartreuse Green
        const t = (norm - 0.38) / 0.16;
        r = Math.floor(t * 50);
        g = Math.floor(218 + t * 37);
        b = Math.floor(255 * (1 - t));
      } else if (norm < 0.72) {
        // Green to Golden Amber / Warm Orange (Flanking channels)
        const t = (norm - 0.54) / 0.18;
        r = Math.floor(50 + t * 205);
        g = Math.floor(255 - t * 115);
        b = 0;
      } else if (norm < 0.90) {
        // Orange to Intense Fiery Crimson Red (Center multicarrier comb)
        const t = (norm - 0.72) / 0.18;
        r = 255;
        g = Math.floor(140 * (1 - t));
        b = Math.floor(t * 12);
      } else {
        // Deep Red to White-Hot Core (> -15 dBm)
        const t = (norm - 0.90) / 0.10;
        r = 255;
        g = Math.floor(t * 240);
        b = Math.floor(t * 220);
      }

      // Little-endian ABGR 32-bit integer for direct ImageData write
      colormap[i] = (255 << 24) | (b << 16) | (g << 8) | r;
    }
  })();

  // ----------------------------------------------------------------------------
  // 2. Canvas Spectrum Drawing
  // ----------------------------------------------------------------------------
  function drawSpectrum(powersUint8) {
    if (isPaused) return;

    const w = specCanvas.width;
    const h = specCanvas.height;
    specCtx.fillStyle = '#070b10';
    specCtx.fillRect(0, 0, w, h);

    // Draw dBm Horizontal Gridlines
    specCtx.lineWidth = 1;
    specCtx.font = '10px monospace';

    const dbTicks = [0, -20, -40, -60, -80, -100];
    for (let db of dbTicks) {
      const y = Math.round(((0 - db) / 105.0) * h);
      specCtx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
      specCtx.beginPath();
      specCtx.moveTo(0, y);
      specCtx.lineTo(w, y);
      specCtx.stroke();

      specCtx.fillStyle = 'rgba(148, 163, 184, 0.45)';
      specCtx.fillText(`${db} dBm`, 8, y - 4);
    }

    // Draw Vertical Frequency Gridlines (matching frequency axis ticks)
    const vertRatios = [0.2, 0.4, 0.5, 0.6, 0.8];
    for (let r of vertRatios) {
      const x = Math.round(w * r);
      specCtx.strokeStyle = (r === 0.5) ? 'rgba(0, 243, 255, 0.25)' : 'rgba(255, 255, 255, 0.08)';
      specCtx.beginPath();
      specCtx.moveTo(x, 0);
      specCtx.lineTo(x, h);
      specCtx.stroke();
    }

    const numBins = powersUint8.length;
    const step = w / (numBins - 1);

    // Draw subtle gradient fill under curve
    specCtx.beginPath();
    specCtx.moveTo(0, h);

    for (let i = 0; i < numBins; i++) {
      const normVal = powersUint8[i] / 255.0; // 0..1
      const y = h - (normVal * (h - 10));
      const x = i * step;
      specCtx.lineTo(x, y);

      // Peak-hold calculation
      const dbm = -110.0 + (normVal * 105.0);
      if (dbm > peakHold[i]) {
        peakHold[i] = dbm;
      } else {
        peakHold[i] = Math.max(-110, peakHold[i] - 0.35);
      }
    }
    specCtx.lineTo(w, h);
    specCtx.closePath();

    const grad = specCtx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, 'rgba(56, 189, 248, 0.12)');
    grad.addColorStop(0.7, 'rgba(0, 255, 136, 0.04)');
    grad.addColorStop(1, 'rgba(0, 0, 0, 0.0)');
    specCtx.fillStyle = grad;
    specCtx.fill();

    // Draw Peak Hold Line (Golden Yellow)
    specCtx.beginPath();
    specCtx.strokeStyle = 'rgba(255, 183, 3, 0.45)';
    specCtx.lineWidth = 1;
    for (let i = 0; i < numBins; i++) {
      const normPeak = (peakHold[i] + 110.0) / 105.0;
      const y = h - (normPeak * (h - 10));
      const x = i * step;
      if (i === 0) specCtx.moveTo(x, y);
      else specCtx.lineTo(x, y);
    }
    specCtx.stroke();

    // Draw Crisp Sky-Blue Spectrum Waveform Line (Matching Reference Screenshot)
    specCtx.beginPath();
    specCtx.strokeStyle = '#38bdf8';
    specCtx.lineWidth = 1.5;
    specCtx.shadowColor = '#00f3ff';
    specCtx.shadowBlur = 3;
    for (let i = 0; i < numBins; i++) {
      const normVal = powersUint8[i] / 255.0;
      const y = h - (normVal * (h - 10));
      const x = i * step;
      if (i === 0) specCtx.moveTo(x, y);
      else specCtx.lineTo(x, y);
    }
    specCtx.stroke();
    specCtx.shadowBlur = 0;
  }

  // ----------------------------------------------------------------------------
  // 3. Canvas Waterfall Drawing (Spectrogram)
  // ----------------------------------------------------------------------------
  let wfScanline = wfCtx.createImageData(wfCanvas.width, 1);
  let wfScanBuf = new Uint32Array(wfScanline.data.buffer);

  function drawWaterfall(powersUint8) {
    if (isPaused) return;

    const w = wfCanvas.width;
    const h = wfCanvas.height;

    // Shift waterfall down 1 scanline
    wfCtx.drawImage(wfCanvas, 0, 0, w, h - 1, 0, 1, w, h - 1);

    // Map bins across canvas width
    const numBins = powersUint8.length;
    for (let x = 0; x < w; x++) {
      const binIdx = Math.floor((x / w) * numBins);
      const val = powersUint8[binIdx];
      wfScanBuf[x] = colormap[val];
    }

    // Blit new top scanline
    wfCtx.putImageData(wfScanline, 0, 0);
  }

  // ----------------------------------------------------------------------------
  // 4. Server-Sent Events (SSE) Real-Time Bridge
  // ----------------------------------------------------------------------------
  function connectSSE() {
    const sse = new EventSource('/api/events');

    sse.onopen = () => {
      connIndicator.classList.add('connected');
      connIndicator.title = 'SSE Real-Time Stream Active';
    };

    sse.onerror = () => {
      connIndicator.classList.remove('connected');
      connIndicator.title = 'Disconnected. Reconnecting...';
    };

    // Telemetry Event
    sse.addEventListener('telemetry', (e) => {
      try {
        const data = JSON.parse(e.data);
        nodeIdEl.textContent = data.node_id;
        meshIpEl.textContent = data.mesh_ip;
        memUsageEl.textContent = `${data.mem_used_mb} / ${data.mem_total_mb} MB`;
        uptimeEl.textContent = `${data.uptime_sec}s`;
        rssiValEl.textContent = `${data.rssi_db.toFixed(1)} dB`;
        tempValEl.textContent = `${data.fpga_temp_c.toFixed(1)} °C`;
        txFramesEl.textContent = data.tx_frames;
        rxFramesEl.textContent = data.rx_frames;

        // RSSI gauge bar (-110 dB to -40 dB)
        const rssiPct = Math.max(5, Math.min(100, ((data.rssi_db - 30) / 80) * 100));
        rssiBarEl.style.width = `${rssiPct}%`;

        // Update target IP default based on local node IP
        if (data.mesh_ip.endsWith('.2') && (!chatTargetInput.value || chatTargetInput.value === '10.10.0.2')) {
          chatTargetInput.value = '10.10.0.1';
          pingTargetInput.value = '10.10.0.1';
        } else if (data.mesh_ip.endsWith('.1') && (!chatTargetInput.value || chatTargetInput.value === '10.10.0.1')) {
          chatTargetInput.value = '10.10.0.2';
          pingTargetInput.value = '10.10.0.2';
        }

        // Neighbors update
        neighborsCountEl.textContent = `${data.neighbors_count} PEERS`;
        if (data.neighbors && data.neighbors.length > 0) {
          neighborsListEl.innerHTML = data.neighbors.map(n => {
            const pct = Math.round((n.tq / 255) * 100);
            return `
              <div class="neighbor-card">
                <div>
                  <div class="n-mac">${n.mac}</div>
                  <div class="n-info">Last seen: ${n.last_seen.toFixed(1)}s ago</div>
                </div>
                <div class="n-tq">${n.tq}/255 (${pct}%)</div>
              </div>
            `;
          }).join('');
        } else {
          neighborsListEl.innerHTML = '<div class="empty-state">No B.A.T.M.A.N. peers in radio range yet.</div>';
        }

        sdrBadge.textContent = data.sdr_connected ? 'SDR LOCKED' : 'PEER MODE';
        sdrBadge.className = data.sdr_connected ? 'badge-status ok' : 'badge-status';
      } catch (err) {
        console.error('Telemetry parse error:', err);
      }
    });

    // Chat Event
    sse.addEventListener('chat', (e) => {
      try {
        const msg = JSON.parse(e.data);
        appendChatMessage(msg);
      } catch (err) {
        console.error('Chat parse error:', err);
      }
    });

    // Spectrum Event (5 Hz)
    sse.addEventListener('spectrum', (e) => {
      try {
        const scan = JSON.parse(e.data);
        peakFreqEl.textContent = `${scan.peak_freq_mhz.toFixed(3)} MHz`;
        peakPowerEl.textContent = `${scan.peak_power_dbm.toFixed(1)} dBm`;

        // Decode base64 uint8 powers
        const binaryStr = atob(scan.powers_b64);
        const len = binaryStr.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) {
          bytes[i] = binaryStr.charCodeAt(i);
        }

        drawSpectrum(bytes);
        drawWaterfall(bytes);
      } catch (err) {
        console.error('Spectrum decode error:', err);
      }
    });
  }

  // ----------------------------------------------------------------------------
  // 5. Chat UI Handling
  // ----------------------------------------------------------------------------
  const renderedMsgIds = new Set();

  function appendChatMessage(msg) {
    if (!msg) return;
    const msgId = msg.id || `${msg.sender_ip || msg.sender}:${msg.timestamp}:${msg.text}`;
    if (renderedMsgIds.has(msgId)) return;
    renderedMsgIds.add(msgId);

    const isOutbound = msg.sender_ip === meshIpEl.textContent;
    const timeStr = new Date(msg.timestamp * 1000).toLocaleTimeString();

    const msgEl = document.createElement('div');
    msgEl.className = `chat-msg ${isOutbound ? 'outbound' : 'inbound'}`;
    msgEl.innerHTML = `
      <span class="time">[${timeStr}]</span>
      <span class="sender">[${escapeHtml(msg.sender_callsign || msg.sender || msg.sender_ip)}]</span>
      <span class="text">${escapeHtml(msg.text)}</span>
    `;

    chatFeedEl.appendChild(msgEl);
    chatFeedEl.scrollTop = chatFeedEl.scrollHeight;
  }

  function escapeHtml(str) {
    return (str || '').replace(/[&<>"']/g, (m) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[m]);
  }

  chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = chatTextInput.value.trim();
    const target = chatTargetInput.value.trim();
    if (!text) return;

    try {
      chatTextInput.value = '';
      await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_ip: target, text: text })
      });
    } catch (err) {
      console.error('Failed to send chat:', err);
    }
  });

  // ----------------------------------------------------------------------------
  // 6. Ping Diagnostic Handling
  // ----------------------------------------------------------------------------
  pingBtn.addEventListener('click', async () => {
    const target = pingTargetInput.value.trim();
    if (!target) return;

    pingBtn.disabled = true;
    pingBtn.textContent = 'PINGING...';
    pingStatusEl.textContent = 'RUNNING';
    pingStatusEl.className = 'neon-amber';

    try {
      const res = await fetch('/api/ping', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_ip: target, count: 3 })
      });
      const data = await res.json();

      pingLossEl.textContent = `${data.packets_recv}/${data.packets_sent} (${data.packet_loss_pct}% Loss)`;
      if (data.success) {
        pingRttEl.textContent = `${data.rtt_avg_ms.toFixed(2)} ms (Min: ${data.rtt_min_ms.toFixed(1)} / Max: ${data.rtt_max_ms.toFixed(1)})`;
        pingStatusEl.textContent = 'PASS (LINK HEALTHY)';
        pingStatusEl.className = 'neon-green';
      } else {
        pingRttEl.textContent = '-- ms';
        pingStatusEl.textContent = 'FAIL (UNREACHABLE)';
        pingStatusEl.className = 'neon-amber';
      }
    } catch (err) {
      pingStatusEl.textContent = 'ERROR';
      pingStatusEl.className = 'neon-amber';
    } finally {
      pingBtn.disabled = false;
      pingBtn.textContent = 'RUN PING TEST';
    }
  });

  // ----------------------------------------------------------------------------
  // 7. Spectrum Throttle Buttons
  // ----------------------------------------------------------------------------
  document.querySelectorAll('.spectrum-controls .btn-ctrl').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.spectrum-controls .btn-ctrl').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const fps = parseInt(btn.getAttribute('data-fps'), 10);
      isPaused = (fps === 0);
    });
  });

  // Resize canvas to match display pixel ratio
  function resizeCanvases() {
    const rect = specCanvas.getBoundingClientRect();
    if (rect.width > 0) {
      specCanvas.width = rect.width;
      wfCanvas.width = rect.width;
      wfScanline = wfCtx.createImageData(wfCanvas.width, 1);
      wfScanBuf = new Uint32Array(wfScanline.data.buffer);
    }
  }
  window.addEventListener('resize', resizeCanvases);
  resizeCanvases();

  // Load initial history and background polling sync fallback
  function syncHistory() {
    fetch('/api/history')
      .then(r => r.json())
      .then(messages => {
        if (messages && messages.length > 0) {
          messages.forEach(appendChatMessage);
        }
      })
      .catch(() => {});
  }

  syncHistory();
  setInterval(syncHistory, 2500);

  connectSSE();
})();
