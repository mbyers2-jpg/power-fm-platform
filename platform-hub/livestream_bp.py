"""
Power FM Livestream Blueprint — Integrates livestream functionality into the
platform-hub dashboard. Provides broadcast, listener, replay, admin pages and
Socket.IO signaling for mediasoup WebRTC.
"""

import os
import sys
import uuid
import time
from datetime import datetime

from flask import Blueprint, render_template_string, jsonify, request

# Add livestream-agent to path for database + SFU access
LIVESTREAM_AGENT_DIR = os.path.join(os.path.expanduser('~'), 'Agents', 'livestream-agent')
if LIVESTREAM_AGENT_DIR not in sys.path:
    sys.path.insert(0, LIVESTREAM_AGENT_DIR)

from flask_socketio import SocketIO, emit, join_room, leave_room

# Lazy imports from livestream-agent (may fail if agent not installed)
_ls_db = None
_sfu = None
_ls_config = None


def _get_ls_db():
    global _ls_db
    if _ls_db is None:
        try:
            import database as ls_database
            _ls_db = ls_database
        except ImportError:
            _ls_db = False
    return _ls_db if _ls_db else None


def _get_sfu():
    global _sfu
    if _sfu is None:
        try:
            import sfu_client
            _sfu = sfu_client
        except ImportError:
            _sfu = False
    return _sfu if _sfu else None


def _get_ls_config():
    global _ls_config
    if _ls_config is None:
        try:
            from config import ICE_SERVERS, ROOM_PREFIX, SFU_SOCKET
            _ls_config = {
                'ICE_SERVERS': ICE_SERVERS,
                'ROOM_PREFIX': ROOM_PREFIX,
                'SFU_SOCKET': SFU_SOCKET,
            }
        except ImportError:
            _ls_config = {
                'ICE_SERVERS': [
                    {'urls': 'stun:stun.l.google.com:19302'},
                    {'urls': 'stun:stun1.l.google.com:19302'},
                ],
                'ROOM_PREFIX': 'live-',
                'SFU_SOCKET': os.path.join(os.path.expanduser('~'), 'Agents', 'secure-call', 'sfu', 'mediasoup.sock'),
            }
    return _ls_config


# In-memory state for active streams (supplements DB)
active_streams = {}  # stream_id -> {host_sid, room_id, producers: {sid: [producer_ids]}, listeners: {sid: peer_info}}

livestream_bp = Blueprint('livestream', __name__)
socketio = SocketIO()

# ─── Color scheme constants ───
BG = '#1a1a2e'
PANEL = '#16213e'
ACCENT = '#e94560'
TEXT = '#eee'
MUTED = '#999'

# ─── Spotlight & Tip config ───
SPOTLIGHT_TIERS = {
    '2min': {'price': 500, 'duration': 120},
    '5min': {'price': 1000, 'duration': 300},
    '10min': {'price': 2500, 'duration': 600},
}

TIP_PRESETS = [200, 500, 1000, 2000, 5000]  # cents

# Stripe client lazy loader
_stripe_client = None
STRIPE_AGENT_DIR = os.path.join(os.path.expanduser('~'), 'Agents', 'stripe-agent')


def _get_stripe_client():
    global _stripe_client
    if _stripe_client is None:
        try:
            if STRIPE_AGENT_DIR not in sys.path:
                sys.path.insert(0, STRIPE_AGENT_DIR)
            from api_client import StripeClient
            _stripe_client = StripeClient()
            if not _stripe_client.is_configured():
                _stripe_client = False
        except Exception:
            _stripe_client = False
    return _stripe_client if _stripe_client else None


PAYMENT_SUCCESS_HTML = """
<!DOCTYPE html>
<html><head><title>Payment Complete</title>
<style>
body{background:""" + BG + """;color:""" + TEXT + """;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{text-align:center;padding:40px}
h2{color:""" + ACCENT + """;margin-bottom:12px}
</style></head><body>
<div class="box">
<h2>Payment Successful!</h2>
<p>Returning to stream...</p>
</div>
<script>
const params = new URLSearchParams(window.location.search);
const sessionId = params.get('session_id');
const payType = params.get('type') || 'spotlight';
const customerId = params.get('customer_id');
if (sessionId) {
    localStorage.setItem('pfm_payment', JSON.stringify({
        session_id: sessionId,
        type: payType,
        ts: Date.now()
    }));
}
if (customerId) {
    localStorage.setItem('pfm_customer_id', customerId);
}
setTimeout(() => window.close(), 1500);
</script>
</body></html>
"""


# ══════════════════════════════════════════════════════════════════════════════
# HTML PAGE TEMPLATES
# ══════════════════════════════════════════════════════════════════════════════

DIRECTORY_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Power FM LIVE</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:""" + BG + """;color:""" + TEXT + """;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh}
.container{max-width:960px;margin:0 auto;padding:20px}
h1{font-size:28px;margin-bottom:6px;color:""" + ACCENT + """}
.subtitle{color:""" + MUTED + """;margin-bottom:24px;font-size:15px}
.stream-card{background:""" + PANEL + """;border-radius:12px;padding:20px;margin-bottom:16px;border-left:4px solid """ + ACCENT + """;cursor:pointer;transition:transform .15s,box-shadow .15s}
.stream-card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(233,69,96,.2)}
.stream-card h3{font-size:18px;margin-bottom:4px}
.stream-card .meta{color:""" + MUTED + """;font-size:13px;display:flex;gap:16px;margin-top:8px}
.stream-card .live-badge{background:""" + ACCENT + """;color:#fff;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:700;letter-spacing:1px;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
.empty{text-align:center;padding:60px 20px;color:""" + MUTED + """}
.empty h2{margin-bottom:10px}
.btn-broadcast{display:inline-block;background:""" + ACCENT + """;color:#fff;padding:12px 28px;border-radius:10px;text-decoration:none;font-weight:700;font-size:16px;transition:transform .15s}
.btn-broadcast:hover{transform:scale(1.05)}
.section-title{font-size:20px;margin:32px 0 16px;color:#ccc;border-bottom:1px solid #333;padding-bottom:8px}
a{color:""" + ACCENT + """;text-decoration:none}
</style>
</head>
<body>
<div class="container">
    <h1>POWER FM LIVE</h1>
    <p class="subtitle">Live broadcasts from Power FM DJs and artists</p>
    <div style="margin-bottom:24px">
        <a href="/live/broadcast" class="btn-broadcast">Start Broadcasting</a>
    </div>

    <div id="live-streams"></div>
    <div id="recent-streams"></div>
</div>
<script>
async function loadStreams() {
    try {
        const res = await fetch('/api/livestream/streams');
        const data = await res.json();
        const liveDiv = document.getElementById('live-streams');
        const recentDiv = document.getElementById('recent-streams');

        const live = (data.streams || []).filter(s => s.status === 'live');
        const recent = (data.streams || []).filter(s => s.status === 'ended');

        if (live.length > 0) {
            liveDiv.innerHTML = '<h2 class="section-title">Live Now</h2>' +
                live.map(s => `
                    <a href="/live/${s.id}" style="text-decoration:none;color:inherit">
                    <div class="stream-card">
                        <div style="display:flex;justify-content:space-between;align-items:center">
                            <h3>${s.title}</h3>
                            <span class="live-badge">LIVE</span>
                        </div>
                        <div class="meta">
                            <span>DJ ${s.host_name}</span>
                            <span>${s.listener_count || 0} listeners</span>
                            <span>${s.stream_type || 'audio'}</span>
                        </div>
                    </div></a>
                `).join('');
        } else {
            liveDiv.innerHTML = '<div class="empty"><h2>No live streams right now</h2><p>Check back soon or start your own broadcast!</p></div>';
        }

        if (recent.length > 0) {
            recentDiv.innerHTML = '<h2 class="section-title">Recent Broadcasts</h2>' +
                recent.slice(0, 10).map(s => `
                    <div class="stream-card" style="border-left-color:#555;cursor:default">
                        <h3>${s.title}</h3>
                        <div class="meta">
                            <span>DJ ${s.host_name}</span>
                            <span>${s.ended_at ? new Date(s.ended_at).toLocaleDateString() : ''}</span>
                            <span>Peak: ${s.max_listeners || 0} listeners</span>
                        </div>
                    </div>
                `).join('');
        }
    } catch(e) { console.error('Failed to load streams:', e); }
}
loadStreams();
setInterval(loadStreams, 15000);
</script>
</body>
</html>
"""


BROADCAST_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Power FM — Go Live</title>
<script src="/static/js/mediasoup-client.bundle.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:""" + BG + """;color:""" + TEXT + """;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh}
.container{max-width:800px;margin:0 auto;padding:20px}
h1{font-size:24px;margin-bottom:20px;color:""" + ACCENT + """}
.panel{background:""" + PANEL + """;border-radius:12px;padding:20px;margin-bottom:16px}
label{display:block;color:""" + MUTED + """;font-size:13px;margin-bottom:6px;font-weight:600}
input,select,textarea{width:100%;padding:10px 14px;background:#0d1b36;border:1px solid #333;border-radius:8px;color:""" + TEXT + """;font-size:14px;margin-bottom:12px}
input:focus,select:focus,textarea:focus{outline:none;border-color:""" + ACCENT + """}
.btn{padding:12px 28px;border-radius:10px;border:none;font-weight:700;font-size:16px;cursor:pointer;transition:all .15s}
.btn-go{background:""" + ACCENT + """;color:#fff}.btn-go:hover{transform:scale(1.05)}
.btn-stop{background:#c0392b;color:#fff}.btn-stop:hover{background:#e74c3c}
.btn-mute{background:#555;color:#fff;padding:8px 16px;font-size:13px}
.btn-mute.active{background:""" + ACCENT + """}
#status{padding:10px;border-radius:8px;margin-bottom:16px;font-weight:600;text-align:center;display:none}
.status-live{background:rgba(233,69,96,.2);color:""" + ACCENT + """;display:block!important}
.status-error{background:rgba(231,76,60,.2);color:#e74c3c;display:block!important}
.status-ready{background:rgba(46,204,113,.15);color:#2ecc71;display:block!important}

/* Self-view video */
.self-view-container{position:fixed;top:80px;right:20px;width:320px;z-index:1000;border-radius:12px;overflow:hidden;background:#000;box-shadow:0 8px 32px rgba(0,0,0,.5);border:2px solid """ + ACCENT + """}
.self-view-container video{width:100%;display:block}
.self-view-container .self-view-label{position:absolute;bottom:8px;left:8px;background:rgba(0,0,0,.7);color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.self-view-container .self-view-close{position:absolute;top:6px;right:6px;background:rgba(0,0,0,.6);color:#fff;border:none;border-radius:50%;width:24px;height:24px;cursor:pointer;font-size:14px;line-height:24px;text-align:center}
.self-view-container .self-view-audio-bar{position:absolute;bottom:0;left:0;right:0;height:4px;background:#333}
.self-view-container .self-view-audio-fill{height:100%;background:linear-gradient(90deg,#2ecc71,""" + ACCENT + """);width:0%;transition:width 50ms}

/* Audio-only waveform canvas */
.self-view-container canvas#self-view-waveform{width:100%;height:180px;display:none}

/* Mic level meter */
.mic-meter{height:6px;background:#333;border-radius:3px;margin-top:4px;overflow:hidden}
.mic-meter-fill{height:100%;background:linear-gradient(90deg,#2ecc71,#e94560);width:0%;transition:width 50ms}

/* Chat */
.chat-box{height:250px;overflow-y:auto;background:#0d1b36;border-radius:8px;padding:10px;margin-bottom:10px}
.chat-msg{margin-bottom:6px;font-size:13px}.chat-msg .name{color:""" + ACCENT + """;font-weight:700}
.chat-msg .tipper-badge{background:""" + ACCENT + """;color:#fff;padding:1px 5px;border-radius:4px;font-size:10px;font-weight:700;margin-right:4px}
.chat-input-row{display:flex;gap:8px}
.chat-input-row input{flex:1;margin-bottom:0}
.chat-input-row button{padding:8px 16px;background:""" + ACCENT + """;color:#fff;border:none;border-radius:8px;font-weight:700;cursor:pointer}

.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px}
.listener-count{color:""" + MUTED + """;font-size:14px}

/* Guest Queue */
.queue-item{display:flex;justify-content:space-between;align-items:center;padding:10px;background:#0d1b36;border-radius:8px;margin-bottom:8px}
.queue-item .qi-info{flex:1}
.queue-item .qi-name{font-weight:700;font-size:14px}
.queue-item .qi-tier{color:""" + MUTED + """;font-size:12px}
.queue-item .qi-actions{display:flex;gap:6px}
.btn-sm{padding:6px 14px;border-radius:6px;border:none;font-weight:700;font-size:12px;cursor:pointer}
.btn-approve{background:#2ecc71;color:#fff}
.btn-reject{background:#555;color:#ccc}
.btn-remove-guest{background:#c0392b;color:#fff;padding:8px 16px;border-radius:8px;border:none;font-weight:700;cursor:pointer;font-size:13px}

/* Active guest */
.active-guest-bar{display:flex;align-items:center;gap:12px;padding:12px;background:rgba(233,69,96,.15);border-radius:8px;margin-bottom:12px}
.active-guest-bar .ag-name{font-weight:700;font-size:15px}
.active-guest-bar .ag-timer{font-family:'SF Mono',Consolas,monospace;font-variant-numeric:tabular-nums;font-size:18px;color:""" + ACCENT + """;font-weight:700}
.active-guest-bar .ag-spacer{flex:1}

/* Tip overlay */
.tip-overlay{position:fixed;top:0;right:0;bottom:0;width:300px;pointer-events:none;z-index:999;overflow:hidden}
.tip-bubble{position:absolute;bottom:-60px;right:20px;background:linear-gradient(135deg,#e94560,#ff6b6b);color:#fff;padding:10px 18px;border-radius:20px;font-weight:700;white-space:nowrap;animation:tipFloat 3s ease-out forwards;pointer-events:none}
@keyframes tipFloat{0%{opacity:1;transform:translateY(0) scale(1)}80%{opacity:1}100%{opacity:0;transform:translateY(-400px) scale(1.2)}}

/* Leaderboard */
.lb-list{list-style:none}
.lb-list li{display:flex;align-items:center;gap:10px;padding:6px 0;font-size:14px;border-bottom:1px solid #1a1a2e}
.lb-rank{width:24px;text-align:center;font-weight:700;color:""" + MUTED + """}
.lb-rank.gold{color:#ffd700}.lb-rank.silver{color:#c0c0c0}.lb-rank.bronze{color:#cd7f32}
.lb-name{flex:1}
.lb-amount{font-weight:700;color:""" + ACCENT + """}
</style>
</head>
<body>
<div class="container">
    <h1>Go Live on Power FM</h1>

    <div id="setup-panel" class="panel">
        <label>Stream Title</label>
        <input id="stream-title" type="text" placeholder="e.g. Friday Night Mix" value="">
        <label>Your DJ Name</label>
        <input id="host-name" type="text" placeholder="e.g. DJ Marc" value="">
        <label>Stream Type</label>
        <select id="stream-type">
            <option value="audio+video">Audio + Video (Camera)</option>
            <option value="audio">Audio Only (Mic)</option>
        </select>
        <label>Description (optional)</label>
        <textarea id="stream-desc" rows="2" placeholder="What's the vibe tonight?"></textarea>
        <div style="margin-top:12px">
            <button class="btn btn-go" id="btn-go-live" onclick="goLive()">Go Live</button>
        </div>
    </div>

    <div id="status"></div>

    <div id="live-panel" style="display:none">
        <div class="panel">
            <h3 id="live-title" style="margin-bottom:8px"></h3>
            <div class="mic-meter"><div class="mic-meter-fill" id="mic-meter"></div></div>
            <div class="controls">
                <button class="btn btn-mute" id="btn-mute" onclick="toggleMute()">Mute Mic</button>
                <button class="btn btn-mute" id="btn-cam" onclick="toggleCam()" style="display:none">Camera Off</button>
                <button class="btn btn-stop" onclick="stopBroadcast()">End Broadcast</button>
                <span class="listener-count" id="listener-count">0 listeners</span>
            </div>
        </div>

        <!-- Active Guest -->
        <div id="active-guest-panel" class="panel" style="display:none">
            <h3 style="margin-bottom:10px">On Screen Now</h3>
            <div class="active-guest-bar">
                <span class="ag-name" id="ag-name"></span>
                <span class="ag-spacer"></span>
                <span class="ag-timer" id="ag-timer">0:00</span>
                <button class="btn-remove-guest" onclick="endSpotlight()">Remove Guest</button>
            </div>
        </div>

        <!-- Guest Queue -->
        <div id="guest-queue-panel" class="panel" style="display:none">
            <h3 style="margin-bottom:10px">Guest Queue <span id="queue-count" style="color:""" + MUTED + """;font-size:13px;font-weight:400"></span></h3>
            <div id="guest-queue-list"></div>
        </div>

        <!-- Leaderboard -->
        <div id="dj-leaderboard-panel" class="panel" style="display:none">
            <h3 style="margin-bottom:10px">Tip Leaderboard <span id="dj-total-tips" style="color:""" + ACCENT + """;font-size:14px;font-weight:400;margin-left:8px"></span></h3>
            <ul class="lb-list" id="dj-lb-list"></ul>
        </div>

        <div class="panel">
            <h3 style="margin-bottom:10px">Chat</h3>
            <div class="chat-box" id="chat-box"></div>
            <div class="chat-input-row">
                <input id="chat-input" type="text" placeholder="Type a message..." onkeydown="if(event.key==='Enter')sendChat()">
                <button onclick="sendChat()">Send</button>
            </div>
        </div>
    </div>
</div>

<!-- Self-view floating window -->
<div class="self-view-container" id="self-view-container" style="display:none">
    <video id="self-view" autoplay muted playsinline></video>
    <canvas id="self-view-waveform"></canvas>
    <div class="self-view-audio-bar"><div class="self-view-audio-fill" id="self-view-audio-fill"></div></div>
    <span class="self-view-label" id="self-view-label">You</span>
    <button class="self-view-close" onclick="toggleSelfView()">&times;</button>
</div>

<!-- Tip overlay -->
<div class="tip-overlay" id="tip-overlay"></div>

<script>
// --- State ---
let socket = null;
let device = null;
let sendTransport = null;
let audioProducer = null;
let videoProducer = null;
let localStream = null;
let streamId = null;
let roomId = null;
let peerId = null;
let micMuted = false;
let camOff = false;
let audioContext = null;
let analyser = null;
let selfViewVisible = true;

// --- Go Live ---
async function goLive() {
    const title = document.getElementById('stream-title').value.trim() || 'Power FM Live';
    const hostName = document.getElementById('host-name').value.trim() || 'DJ';
    const streamType = document.getElementById('stream-type').value;
    const desc = document.getElementById('stream-desc').value.trim();

    setStatus('Requesting media access...', 'ready');
    document.getElementById('btn-go-live').disabled = true;

    try {
        // Get media
        const constraints = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } };
        if (streamType === 'audio+video') {
            constraints.video = { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 24 } };
        }
        localStream = await navigator.mediaDevices.getUserMedia(constraints);

        // Set up self-view
        setupSelfView(localStream, streamType);

        // Set up mic meter
        setupMicMeter(localStream);

        // Connect Socket.IO
        socket = io({ transports: ['websocket', 'polling'] });

        socket.on('connect', () => {
            peerId = 'host-' + Date.now();
            socket.emit('join-as-host', {
                title: title,
                hostName: hostName,
                streamType: streamType,
                description: desc,
                peerId: peerId
            });
        });

        socket.on('host-joined', async (data) => {
            streamId = data.streamId;
            roomId = data.roomId;

            setStatus('LIVE — ' + title, 'live');
            document.getElementById('setup-panel').style.display = 'none';
            document.getElementById('live-panel').style.display = 'block';
            document.getElementById('live-title').textContent = title;

            if (streamType === 'audio+video') {
                document.getElementById('btn-cam').style.display = 'inline-block';
            }

            // Load mediasoup Device
            if (typeof mediasoupClient !== 'undefined') {
                try {
                    device = new mediasoupClient.Device();
                    await device.load({ routerRtpCapabilities: data.rtpCapabilities });

                    // Create send transport
                    const transportData = data.sendTransport;
                    sendTransport = device.createSendTransport({
                        id: transportData.id,
                        iceParameters: transportData.iceParameters,
                        iceCandidates: transportData.iceCandidates,
                        dtlsParameters: transportData.dtlsParameters,
                        iceServers: data.iceServers || []
                    });

                    sendTransport.on('connect', ({ dtlsParameters }, callback, errback) => {
                        socket.emit('connect-transport', {
                            transportId: sendTransport.id,
                            dtlsParameters: dtlsParameters,
                            roomId: roomId,
                            peerId: peerId
                        });
                        socket.once('transport-connected', () => callback());
                        socket.once('transport-connect-error', (err) => errback(new Error(err.message)));
                    });

                    sendTransport.on('produce', ({ kind, rtpParameters, appData }, callback, errback) => {
                        socket.emit('produce', {
                            transportId: sendTransport.id,
                            kind: kind,
                            rtpParameters: rtpParameters,
                            appData: appData,
                            roomId: roomId,
                            peerId: peerId
                        });
                        socket.once('produced', (data) => callback({ id: data.producerId }));
                        socket.once('produce-error', (err) => errback(new Error(err.message)));
                    });

                    // Produce audio
                    const audioTrack = localStream.getAudioTracks()[0];
                    if (audioTrack) {
                        audioProducer = await sendTransport.produce({ track: audioTrack });
                    }

                    // Produce video if applicable
                    const videoTrack = localStream.getVideoTracks()[0];
                    if (videoTrack) {
                        videoProducer = await sendTransport.produce({ track: videoTrack });
                    }

                } catch(e) {
                    console.error('mediasoup setup error:', e);
                    setStatus('Live (media relay error: ' + e.message + ')', 'live');
                }
            }
        });

        socket.on('listener-joined', (data) => {
            updateListenerCount(data.listenerCount);
            appendChat('System', data.name + ' joined');
        });

        socket.on('listener-left', (data) => {
            updateListenerCount(data.listenerCount);
            appendChat('System', data.name + ' left');
        });

        socket.on('chat-message', (data) => {
            appendChat(data.name, data.message, data.isTipper);
        });

        socket.on('stream-ended', () => {
            setStatus('Broadcast ended', 'ready');
        });

        socket.on('connect_error', (err) => {
            setStatus('Connection error: ' + err.message, 'error');
            document.getElementById('btn-go-live').disabled = false;
        });

        // --- Spotlight events for DJ ---
        socket.on('guest-queue-updated', (data) => {
            renderGuestQueue(data.queue);
        });

        socket.on('spotlight-started', (data) => {
            showActiveGuest(data.name, data.duration);
        });

        socket.on('spotlight-tick', (data) => {
            updateGuestTimer(data.remaining);
        });

        socket.on('spotlight-expired', (data) => {
            hideActiveGuest();
        });

        // --- Tip events for DJ ---
        socket.on('tip-received', (data) => {
            showTipBubble(data.name, data.amount_cents);
            appendChat('System', data.name + ' sent a $' + (data.amount_cents / 100).toFixed(2) + ' Super Tip!');
        });

        socket.on('leaderboard-update', (data) => {
            renderLeaderboard(data.leaderboard, data.total_cents);
        });

    } catch(e) {
        setStatus('Error: ' + e.message, 'error');
        document.getElementById('btn-go-live').disabled = false;
    }
}

// --- Guest Queue (DJ view) ---
function renderGuestQueue(queue) {
    const panel = document.getElementById('guest-queue-panel');
    const list = document.getElementById('guest-queue-list');
    const count = document.getElementById('queue-count');
    if (!queue || queue.length === 0) {
        panel.style.display = 'none';
        return;
    }
    panel.style.display = 'block';
    count.textContent = '(' + queue.length + ')';
    list.innerHTML = queue.map((g, i) => '<div class="queue-item">' +
        '<div class="qi-info"><div class="qi-name">' + escHtml(g.name) + '</div>' +
        '<div class="qi-tier">' + escHtml(g.tier) + ' — $' + (g.price / 100).toFixed(2) + '</div></div>' +
        '<div class="qi-actions">' +
        '<button class="btn-sm btn-approve" onclick="approveGuest(' + i + ')">Approve</button>' +
        '<button class="btn-sm btn-reject" onclick="rejectGuest(' + i + ')">Reject</button>' +
        '</div></div>').join('');
}

function approveGuest(index) {
    if (socket && streamId) {
        socket.emit('approve-guest', { streamId: streamId, index: index });
    }
}
function rejectGuest(index) {
    if (socket && streamId) {
        socket.emit('reject-guest', { streamId: streamId, index: index });
    }
}
function endSpotlight() {
    if (socket && streamId) {
        socket.emit('end-spotlight', { streamId: streamId });
    }
}

function showActiveGuest(name, duration) {
    document.getElementById('active-guest-panel').style.display = 'block';
    document.getElementById('ag-name').textContent = name;
    updateGuestTimer(duration);
}
function updateGuestTimer(remaining) {
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    document.getElementById('ag-timer').textContent = m + ':' + String(s).padStart(2, '0');
}
function hideActiveGuest() {
    document.getElementById('active-guest-panel').style.display = 'none';
}

// --- Tips & Leaderboard ---
function showTipBubble(name, cents) {
    const overlay = document.getElementById('tip-overlay');
    const bubble = document.createElement('div');
    bubble.className = 'tip-bubble';
    const scale = Math.min(2, 0.8 + (cents / 2000));
    bubble.style.fontSize = (14 * scale) + 'px';
    bubble.textContent = name + ' $' + (cents / 100).toFixed(2);
    overlay.appendChild(bubble);
    setTimeout(() => bubble.remove(), 3200);
}

function renderLeaderboard(lb, totalCents) {
    const panel = document.getElementById('dj-leaderboard-panel');
    const list = document.getElementById('dj-lb-list');
    const total = document.getElementById('dj-total-tips');
    if (!lb || lb.length === 0) { panel.style.display = 'none'; return; }
    panel.style.display = 'block';
    total.textContent = '$' + (totalCents / 100).toFixed(2) + ' total';
    const icons = ['gold', 'silver', 'bronze'];
    const medals = ['&#128081;', '&#129352;', '&#129353;'];
    list.innerHTML = lb.map((entry, i) => '<li>' +
        '<span class="lb-rank ' + (icons[i] || '') + '">' + (i < 3 ? medals[i] : (i + 1)) + '</span>' +
        '<span class="lb-name">' + escHtml(entry[0]) + '</span>' +
        '<span class="lb-amount">$' + (entry[1] / 100).toFixed(2) + '</span></li>').join('');
}

// --- Self-View ---
function setupSelfView(stream, streamType) {
    const container = document.getElementById('self-view-container');
    const video = document.getElementById('self-view');
    const canvas = document.getElementById('self-view-waveform');

    container.style.display = 'block';

    if (streamType === 'audio+video') {
        video.srcObject = stream;
        video.style.display = 'block';
        canvas.style.display = 'none';
        document.getElementById('self-view-label').textContent = 'You (Camera)';
        startSelfViewAudioMeter(stream);
    } else {
        video.style.display = 'none';
        canvas.style.display = 'block';
        document.getElementById('self-view-label').textContent = 'You (Audio)';
        drawAudioWaveform(stream, canvas);
        startSelfViewAudioMeter(stream);
    }
}

function startSelfViewAudioMeter(stream) {
    const actx = new (window.AudioContext || window.webkitAudioContext)();
    const src = actx.createMediaStreamSource(stream);
    const a = actx.createAnalyser();
    a.fftSize = 256;
    src.connect(a);
    const buf = new Uint8Array(a.frequencyBinCount);
    const fill = document.getElementById('self-view-audio-fill');

    function tick() {
        requestAnimationFrame(tick);
        a.getByteFrequencyData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i];
        const avg = sum / buf.length;
        fill.style.width = Math.min(100, (avg / 128) * 100) + '%';
    }
    tick();
}

function drawAudioWaveform(stream, canvas) {
    const ctx = canvas.getContext('2d');
    const actx = new (window.AudioContext || window.webkitAudioContext)();
    const src = actx.createMediaStreamSource(stream);
    const a = actx.createAnalyser();
    a.fftSize = 256;
    src.connect(a);
    const bufLen = a.frequencyBinCount;
    const data = new Uint8Array(bufLen);

    function draw() {
        requestAnimationFrame(draw);
        a.getByteFrequencyData(data);
        const w = canvas.width = canvas.clientWidth * (window.devicePixelRatio || 1);
        const h = canvas.height = canvas.clientHeight * (window.devicePixelRatio || 1);
        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, w, h);
        const barW = (w / bufLen) * 2.5;
        let x = 0;
        for (let i = 0; i < bufLen; i++) {
            const barH = (data[i] / 255) * h;
            const r = 233, g = 69 + Math.floor((data[i] / 255) * 60), b = 96;
            ctx.fillStyle = 'rgb(' + r + ',' + g + ',' + b + ')';
            ctx.fillRect(x, h - barH, barW, barH);
            x += barW + 1;
        }
    }
    draw();
}

function toggleSelfView() {
    const c = document.getElementById('self-view-container');
    selfViewVisible = !selfViewVisible;
    c.style.display = selfViewVisible ? 'block' : 'none';
}

// --- Mic Meter ---
function setupMicMeter(stream) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioContext.createMediaStreamSource(stream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    const meter = document.getElementById('mic-meter');

    function updateMeter() {
        requestAnimationFrame(updateMeter);
        analyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
        const avg = sum / dataArray.length;
        const pct = Math.min(100, (avg / 128) * 100);
        meter.style.width = pct + '%';
    }
    updateMeter();
}

// --- Controls ---
function toggleMute() {
    if (!localStream) return;
    micMuted = !micMuted;
    localStream.getAudioTracks().forEach(t => t.enabled = !micMuted);
    const btn = document.getElementById('btn-mute');
    btn.textContent = micMuted ? 'Unmute Mic' : 'Mute Mic';
    btn.classList.toggle('active', micMuted);
    if (audioProducer) {
        if (micMuted) {
            socket.emit('pause-producer', { producerId: audioProducer.id, roomId, peerId });
        } else {
            socket.emit('resume-producer', { producerId: audioProducer.id, roomId, peerId });
        }
    }
}

function toggleCam() {
    if (!localStream) return;
    camOff = !camOff;
    localStream.getVideoTracks().forEach(t => t.enabled = !camOff);
    const btn = document.getElementById('btn-cam');
    btn.textContent = camOff ? 'Camera On' : 'Camera Off';
    btn.classList.toggle('active', camOff);
    if (videoProducer) {
        if (camOff) {
            socket.emit('pause-producer', { producerId: videoProducer.id, roomId, peerId });
        } else {
            socket.emit('resume-producer', { producerId: videoProducer.id, roomId, peerId });
        }
    }
}

function stopBroadcast() {
    if (socket && streamId) {
        socket.emit('end-broadcast', { streamId: streamId, roomId: roomId, peerId: peerId });
    }
    cleanup();
    setStatus('Broadcast ended', 'ready');
    document.getElementById('live-panel').style.display = 'none';
    document.getElementById('setup-panel').style.display = 'block';
    document.getElementById('btn-go-live').disabled = false;
    document.getElementById('self-view-container').style.display = 'none';
}

function cleanup() {
    if (localStream) { localStream.getTracks().forEach(t => t.stop()); localStream = null; }
    if (sendTransport) { try { sendTransport.close(); } catch(e){} sendTransport = null; }
    if (audioContext) { try { audioContext.close(); } catch(e){} audioContext = null; }
    audioProducer = null;
    videoProducer = null;
    device = null;
}

// --- Chat ---
function appendChat(name, msg, isTipper) {
    const box = document.getElementById('chat-box');
    const div = document.createElement('div');
    div.className = 'chat-msg';
    const badge = isTipper ? '<span class="tipper-badge">Tipper</span>' : '';
    div.innerHTML = badge + '<span class="name">' + escHtml(name) + ':</span> ' + escHtml(msg);
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
}

function sendChat() {
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg || !socket) return;
    socket.emit('chat-message', {
        streamId: streamId,
        message: msg,
        name: document.getElementById('host-name').value.trim() || 'DJ'
    });
    appendChat(document.getElementById('host-name').value.trim() || 'DJ', msg);
    input.value = '';
}

function updateListenerCount(n) {
    document.getElementById('listener-count').textContent = n + ' listener' + (n !== 1 ? 's' : '');
}

function setStatus(msg, type) {
    const el = document.getElementById('status');
    el.textContent = msg;
    el.className = '';
    if (type === 'live') el.className = 'status-live';
    else if (type === 'error') el.className = 'status-error';
    else if (type === 'ready') el.className = 'status-ready';
}

function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

window.addEventListener('beforeunload', cleanup);
</script>
</body>
</html>
"""


LISTENER_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Power FM LIVE</title>
<script src="/static/js/mediasoup-client.bundle.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:""" + BG + """;color:""" + TEXT + """;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh}
.container{max-width:960px;margin:0 auto;padding:20px}
h1{font-size:24px;margin-bottom:6px;color:""" + ACCENT + """}
.subtitle{color:""" + MUTED + """;margin-bottom:20px;font-size:14px}
.panel{background:""" + PANEL + """;border-radius:12px;padding:20px;margin-bottom:16px}
.live-badge{background:""" + ACCENT + """;color:#fff;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:700;letter-spacing:1px;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
.meta{color:""" + MUTED + """;font-size:13px;display:flex;gap:16px;margin:8px 0}
#status{padding:10px;border-radius:8px;margin-bottom:16px;font-weight:600;text-align:center}
.status-connected{background:rgba(46,204,113,.15);color:#2ecc71}
.status-connecting{background:rgba(241,196,15,.15);color:#f1c40f}
.status-error{background:rgba(231,76,60,.2);color:#e74c3c}

/* Video grid — solo or duo layout */
.video-grid{display:grid;gap:8px;margin-bottom:16px;border-radius:8px;overflow:hidden;background:#000}
.video-grid.solo{grid-template-columns:1fr}
.video-grid.duo{grid-template-columns:70fr 30fr}
.video-grid video{width:100%;background:#000;display:block}
.video-tile{position:relative;background:#000;min-height:200px}
.video-tile video{width:100%;height:100%;object-fit:cover}
.video-tile .tile-label{position:absolute;bottom:8px;left:8px;background:rgba(0,0,0,.7);color:#fff;padding:3px 10px;border-radius:6px;font-size:12px;font-weight:600}
.video-tile .tile-timer{position:absolute;top:8px;right:8px;background:rgba(233,69,96,.85);color:#fff;padding:3px 10px;border-radius:6px;font-size:14px;font-weight:700;font-family:'SF Mono',Consolas,monospace;font-variant-numeric:tabular-nums}

/* Audio player (hidden) */
.audio-player-hidden{display:none}

/* Chat */
.chat-box{height:250px;overflow-y:auto;background:#0d1b36;border-radius:8px;padding:10px;margin-bottom:10px}
.chat-msg{margin-bottom:6px;font-size:13px}.chat-msg .name{color:""" + ACCENT + """;font-weight:700}
.chat-msg .tipper-badge{background:""" + ACCENT + """;color:#fff;padding:1px 5px;border-radius:4px;font-size:10px;font-weight:700;margin-right:4px}
.chat-input-row{display:flex;gap:8px}
.chat-input-row input{flex:1;padding:10px 14px;background:#0d1b36;border:1px solid #333;border-radius:8px;color:""" + TEXT + """;font-size:14px}
.chat-input-row input:focus{outline:none;border-color:""" + ACCENT + """}
.chat-input-row button{padding:8px 16px;background:""" + ACCENT + """;color:#fff;border:none;border-radius:8px;font-weight:700;cursor:pointer}
.listener-count{color:""" + MUTED + """;font-size:14px}
.name-input{display:flex;gap:8px;margin-bottom:16px;align-items:center}
.name-input input{padding:8px 12px;background:#0d1b36;border:1px solid #333;border-radius:8px;color:""" + TEXT + """;font-size:14px;width:200px}
.name-input button{padding:8px 16px;background:""" + ACCENT + """;color:#fff;border:none;border-radius:8px;font-weight:700;cursor:pointer}
a{color:""" + ACCENT + """}

/* Fan actions panel */
.fan-actions{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.fan-actions h4{width:100%;font-size:13px;color:""" + MUTED + """;text-transform:uppercase;letter-spacing:1px;margin-bottom:2px}
.btn-tier{padding:10px 16px;border-radius:8px;border:2px solid """ + ACCENT + """;background:transparent;color:""" + ACCENT + """;font-weight:700;font-size:13px;cursor:pointer;transition:all .15s}
.btn-tier:hover{background:""" + ACCENT + """;color:#fff;transform:scale(1.05)}
.btn-tier:disabled{opacity:.4;cursor:not-allowed;transform:none}
.btn-tip{padding:8px 14px;border-radius:8px;border:2px solid #f39c12;background:transparent;color:#f39c12;font-weight:700;font-size:13px;cursor:pointer;transition:all .15s}
.btn-tip:hover{background:#f39c12;color:#fff;transform:scale(1.05)}

/* Spotlight status message */
.spotlight-status{padding:10px 16px;border-radius:8px;background:rgba(46,204,113,.15);color:#2ecc71;font-weight:600;margin-bottom:12px;display:none}
.spotlight-status.active{display:block}

/* Tip overlay */
.tip-overlay{position:fixed;top:0;right:0;bottom:0;width:300px;pointer-events:none;z-index:999;overflow:hidden}
.tip-bubble{position:absolute;bottom:-60px;right:20px;background:linear-gradient(135deg,#f39c12,#e74c3c);color:#fff;padding:10px 18px;border-radius:20px;font-weight:700;white-space:nowrap;animation:tipFloat 3s ease-out forwards;pointer-events:none}
@keyframes tipFloat{0%{opacity:1;transform:translateY(0) scale(1)}80%{opacity:1}100%{opacity:0;transform:translateY(-400px) scale(1.2)}}

/* Leaderboard */
.lb-header{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.lb-header h3{margin:0}
.lb-total{color:""" + ACCENT + """;font-size:14px;font-weight:400}
.lb-list{list-style:none}
.lb-list li{display:flex;align-items:center;gap:10px;padding:6px 0;font-size:14px;border-bottom:1px solid #1a1a2e}
.lb-rank{width:24px;text-align:center;font-weight:700;color:""" + MUTED + """}
.lb-rank.gold{color:#ffd700}.lb-rank.silver{color:#c0c0c0}.lb-rank.bronze{color:#cd7f32}
.lb-name{flex:1}
.lb-amount{font-weight:700;color:""" + ACCENT + """}

/* Two-column layout: main + sidebar */
.main-layout{display:grid;grid-template-columns:1fr 280px;gap:16px}
@media(max-width:768px){.main-layout{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="container">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px">
        <h1 id="stream-title">Power FM Live</h1>
        <span class="live-badge" id="live-badge">LIVE</span>
    </div>
    <div class="meta">
        <span id="host-name">DJ</span>
        <span id="listener-count">0 listeners</span>
        <span id="stream-type">audio</span>
    </div>

    <div id="status" class="status-connecting">Connecting...</div>

    <!-- Video grid -->
    <div class="video-grid solo" id="video-grid" style="display:none">
        <div class="video-tile" id="dj-tile">
            <video id="video-player" autoplay playsinline></video>
            <span class="tile-label" id="dj-label">DJ</span>
        </div>
    </div>
    <audio id="audio-player" class="audio-player-hidden" autoplay></audio>

    <div class="main-layout">
        <div class="main-col">
            <!-- Fan actions -->
            <div class="panel" id="fan-actions-panel" style="display:none">
                <div id="saved-card-indicator" style="display:none;padding:8px 14px;margin-bottom:12px;border-radius:8px;background:rgba(46,204,113,.12);color:#2ecc71;font-size:13px;font-weight:600">
                    <span id="saved-card-text"></span>
                    <a href="#" onclick="clearSavedCard();return false" style="color:""" + MUTED + """;margin-left:10px;font-weight:400;font-size:12px">Change card</a>
                </div>
                <div class="fan-actions">
                    <h4>Join the DJ on screen</h4>
                    <button class="btn-tier" onclick="handlePurchase('spotlight','2min')">Join DJ — $5 (2min)</button>
                    <button class="btn-tier" onclick="handlePurchase('spotlight','5min')">Join DJ — $10 (5min)</button>
                    <button class="btn-tier" onclick="handlePurchase('spotlight','10min')">Join DJ — $25 (10min)</button>
                </div>
                <div class="fan-actions">
                    <h4>Send a Super Tip</h4>
                    <button class="btn-tip" onclick="handlePurchase('tip',null,200)">$2</button>
                    <button class="btn-tip" onclick="handlePurchase('tip',null,500)">$5</button>
                    <button class="btn-tip" onclick="handlePurchase('tip',null,1000)">$10</button>
                    <button class="btn-tip" onclick="handlePurchase('tip',null,2000)">$20</button>
                    <button class="btn-tip" onclick="handlePurchase('tip',null,5000)">$50</button>
                </div>
            </div>

            <!-- Spotlight status -->
            <div class="spotlight-status" id="spotlight-status"></div>

            <!-- Chat -->
            <div class="panel">
                <h3 style="margin-bottom:10px">Chat</h3>
                <div class="name-input" id="name-input">
                    <input id="display-name" type="text" placeholder="Your name" value="">
                    <button onclick="joinStream()">Join Chat</button>
                </div>
                <div class="chat-box" id="chat-box"></div>
                <div class="chat-input-row" id="chat-controls" style="display:none">
                    <input id="chat-input" type="text" placeholder="Type a message..." onkeydown="if(event.key==='Enter')sendChat()">
                    <button onclick="sendChat()">Send</button>
                </div>
            </div>
        </div>

        <!-- Sidebar: leaderboard -->
        <div class="sidebar-col">
            <div class="panel" id="leaderboard-panel" style="display:none">
                <div class="lb-header">
                    <h3>Leaderboard</h3>
                    <span class="lb-total" id="lb-total"></span>
                </div>
                <ul class="lb-list" id="lb-list"></ul>
            </div>
        </div>
    </div>

    <div style="margin-top:16px"><a href="/live">&larr; Back to all streams</a></div>
</div>

<!-- Tip overlay -->
<div class="tip-overlay" id="tip-overlay"></div>

<script>
const STREAM_ID = '{{ stream_id }}';
let socket = null;
let device = null;
let recvTransport = null;
let sendTransport = null;
let guestAudioProducer = null;
let guestVideoProducer = null;
let displayName = '';
let peerId = 'listener-' + Date.now();
let savedRoomId = null;
let isTipper = false;
let paymentPollTimer = null;

async function joinStream() {
    displayName = document.getElementById('display-name').value.trim() || 'Listener';
    document.getElementById('name-input').style.display = 'none';
    document.getElementById('chat-controls').style.display = 'flex';

    socket = io({ transports: ['websocket', 'polling'] });

    socket.on('connect', () => {
        socket.emit('join-as-listener', {
            streamId: STREAM_ID,
            name: displayName,
            peerId: peerId
        });
    });

    socket.on('listener-welcome', async (data) => {
        document.getElementById('stream-title').textContent = data.title || 'Power FM Live';
        document.getElementById('host-name').textContent = 'DJ ' + (data.hostName || '');
        document.getElementById('stream-type').textContent = data.streamType || 'audio';
        updateListenerCount(data.listenerCount || 0);
        setStatus('Connected', 'connected');
        savedRoomId = data.roomId;

        // Show fan actions
        document.getElementById('fan-actions-panel').style.display = 'block';

        // Load chat history
        if (data.recentChat) {
            data.recentChat.forEach(m => appendChat(m.name, m.message));
        }

        // Set up mediasoup consumer
        if (typeof mediasoupClient !== 'undefined' && data.rtpCapabilities) {
            try {
                device = new mediasoupClient.Device();
                await device.load({ routerRtpCapabilities: data.rtpCapabilities });

                const transportData = data.recvTransport;
                if (transportData) {
                    recvTransport = device.createRecvTransport({
                        id: transportData.id,
                        iceParameters: transportData.iceParameters,
                        iceCandidates: transportData.iceCandidates,
                        dtlsParameters: transportData.dtlsParameters,
                        iceServers: data.iceServers || []
                    });

                    recvTransport.on('connect', ({ dtlsParameters }, callback, errback) => {
                        socket.emit('connect-transport', {
                            transportId: recvTransport.id,
                            dtlsParameters: dtlsParameters,
                            roomId: data.roomId,
                            peerId: peerId
                        });
                        socket.once('transport-connected', () => callback());
                        socket.once('transport-connect-error', (err) => errback(new Error(err.message)));
                    });

                    // Consume existing producers
                    if (data.producers) {
                        for (const p of data.producers) {
                            await consumeProducer(p.producerId, p.kind, data.roomId);
                        }
                    }
                }
            } catch(e) {
                console.error('mediasoup consumer error:', e);
            }
        }
    });

    socket.on('new-producer', async (data) => {
        if (device && recvTransport) {
            await consumeProducer(data.producerId, data.kind, data.roomId);
        }
    });

    socket.on('chat-message', (data) => {
        appendChat(data.name, data.message, data.isTipper);
    });

    socket.on('listener-joined', (data) => {
        updateListenerCount(data.listenerCount);
        appendChat('System', data.name + ' joined');
    });

    socket.on('listener-left', (data) => {
        updateListenerCount(data.listenerCount);
        appendChat('System', data.name + ' left');
    });

    socket.on('stream-ended', () => {
        setStatus('Stream ended', 'error');
        document.getElementById('live-badge').textContent = 'ENDED';
        document.getElementById('live-badge').style.background = '#555';
        document.getElementById('live-badge').style.animation = 'none';
        document.getElementById('fan-actions-panel').style.display = 'none';
    });

    socket.on('connect_error', (err) => {
        setStatus('Connection error', 'error');
    });

    // --- Spotlight events ---
    socket.on('spotlight-pending', (data) => {
        const el = document.getElementById('spotlight-status');
        el.textContent = 'You are #' + data.position + ' in queue — waiting for DJ approval';
        el.classList.add('active');
    });

    socket.on('spotlight-approved', async (data) => {
        const el = document.getElementById('spotlight-status');
        el.textContent = 'Approved! Starting camera...';
        el.classList.add('active');
        try {
            const guestStream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true },
                video: { width: { ideal: 480 }, height: { ideal: 360 }, frameRate: { ideal: 24 } }
            });

            // Create send transport for guest
            if (device && data.sendTransport) {
                const td = data.sendTransport;
                sendTransport = device.createSendTransport({
                    id: td.id,
                    iceParameters: td.iceParameters,
                    iceCandidates: td.iceCandidates,
                    dtlsParameters: td.dtlsParameters,
                    iceServers: data.iceServers || []
                });

                sendTransport.on('connect', ({ dtlsParameters }, callback, errback) => {
                    socket.emit('connect-transport', {
                        transportId: sendTransport.id,
                        dtlsParameters: dtlsParameters,
                        roomId: savedRoomId,
                        peerId: peerId
                    });
                    socket.once('transport-connected', () => callback());
                    socket.once('transport-connect-error', (err) => errback(new Error(err.message)));
                });

                sendTransport.on('produce', ({ kind, rtpParameters, appData }, callback, errback) => {
                    socket.emit('produce', {
                        transportId: sendTransport.id,
                        kind: kind,
                        rtpParameters: rtpParameters,
                        appData: appData,
                        roomId: savedRoomId,
                        peerId: peerId
                    });
                    socket.once('produced', (d) => callback({ id: d.producerId }));
                    socket.once('produce-error', (err) => errback(new Error(err.message)));
                });

                const audioTrack = guestStream.getAudioTracks()[0];
                if (audioTrack) guestAudioProducer = await sendTransport.produce({ track: audioTrack });
                const videoTrack = guestStream.getVideoTracks()[0];
                if (videoTrack) guestVideoProducer = await sendTransport.produce({ track: videoTrack });
            }
            el.textContent = 'You are LIVE on screen!';
        } catch(e) {
            el.textContent = 'Camera/mic error: ' + e.message;
            el.style.background = 'rgba(231,76,60,.2)';
            el.style.color = '#e74c3c';
        }
    });

    socket.on('spotlight-started', (data) => {
        showGuestTile(data.name, data.duration);
    });

    socket.on('spotlight-tick', (data) => {
        updateGuestTimer(data.remaining);
    });

    socket.on('spotlight-expired', (data) => {
        removeGuestTile();
        const el = document.getElementById('spotlight-status');
        el.classList.remove('active');
        // Clean up guest send transport
        if (sendTransport) { try { sendTransport.close(); } catch(e){} sendTransport = null; }
        guestAudioProducer = null;
        guestVideoProducer = null;
    });

    // --- Tip events ---
    socket.on('tip-received', (data) => {
        showTipBubble(data.name, data.amount_cents);
        appendChat('System', data.name + ' sent a $' + (data.amount_cents / 100).toFixed(2) + ' Super Tip!');
    });

    socket.on('leaderboard-update', (data) => {
        renderLeaderboard(data.leaderboard, data.total_cents);
    });
}

// --- Video grid management ---
function showGuestTile(name, duration) {
    const grid = document.getElementById('video-grid');
    grid.classList.remove('solo');
    grid.classList.add('duo');
    // Add guest tile if not exists
    if (!document.getElementById('guest-tile')) {
        const tile = document.createElement('div');
        tile.className = 'video-tile';
        tile.id = 'guest-tile';
        tile.innerHTML = '<video id="guest-video" autoplay playsinline></video>' +
            '<span class="tile-label">' + escHtml(name) + '</span>' +
            '<span class="tile-timer" id="guest-timer"></span>';
        grid.appendChild(tile);
    }
    updateGuestTimer(duration);
}

function updateGuestTimer(remaining) {
    const el = document.getElementById('guest-timer');
    if (!el) return;
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    el.textContent = m + ':' + String(s).padStart(2, '0');
}

function removeGuestTile() {
    const tile = document.getElementById('guest-tile');
    if (tile) tile.remove();
    const grid = document.getElementById('video-grid');
    grid.classList.remove('duo');
    grid.classList.add('solo');
}

// --- Saved card helpers ---
function getSavedCustomerId() {
    return localStorage.getItem('pfm_customer_id');
}

function updateSavedCardIndicator(brand, last4) {
    const el = document.getElementById('saved-card-indicator');
    const txt = document.getElementById('saved-card-text');
    if (brand && last4) {
        txt.textContent = brand.charAt(0).toUpperCase() + brand.slice(1) + ' ****' + last4 + ' saved';
        el.style.display = 'block';
    } else if (getSavedCustomerId()) {
        txt.textContent = 'Saved card on file';
        el.style.display = 'block';
    } else {
        el.style.display = 'none';
    }
}

function clearSavedCard() {
    localStorage.removeItem('pfm_customer_id');
    document.getElementById('saved-card-indicator').style.display = 'none';
}

// Show saved card indicator on load
if (getSavedCustomerId()) {
    updateSavedCardIndicator();
}

// --- Purchase flow (quick-pay or checkout popup) ---
function handlePurchase(type, tier, tipCents) {
    const customerId = getSavedCustomerId();
    if (customerId) {
        quickPay(type, tier, tipCents, customerId);
    } else {
        openCheckout(type, tier, tipCents);
    }
}

function quickPay(type, tier, tipCents, customerId) {
    const body = {
        customer_id: customerId,
        stream_id: STREAM_ID,
        type: type,
        fan_name: displayName || 'Fan',
    };
    if (tier) body.tier = tier;
    if (tipCents) body.amount_cents = tipCents;

    fetch('/api/livestream/quick-pay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            // Update card indicator
            if (data.card_brand && data.card_last4) {
                updateSavedCardIndicator(data.card_brand, data.card_last4);
            }
            // Emit socket event immediately
            if (type === 'spotlight') {
                socket.emit('spotlight-request', {
                    streamId: STREAM_ID,
                    payment_intent_id: data.payment_intent_id,
                    tier: tier,
                    name: displayName || 'Fan',
                    peerId: peerId
                });
            } else if (type === 'tip') {
                socket.emit('super-tip', {
                    streamId: STREAM_ID,
                    payment_intent_id: data.payment_intent_id,
                    amount_cents: tipCents,
                    name: displayName || 'Fan'
                });
                isTipper = true;
            }
        } else if (data.need_checkout) {
            // Card declined or expired — fall back to full checkout
            clearSavedCard();
            openCheckout(type, tier, tipCents);
        } else {
            alert(data.error || 'Payment failed');
        }
    })
    .catch(e => {
        // Network error — fall back to checkout
        openCheckout(type, tier, tipCents);
    });
}

function openCheckout(type, tier, tipCents) {
    // Open popup immediately (in click context) so browser doesn't block it
    const popup = window.open('about:blank', 'stripe_checkout', 'width=500,height=700,scrollbars=yes');

    const body = { stream_id: STREAM_ID, type: type };
    if (tier) body.tier = tier;
    if (tipCents) body.amount_cents = tipCents;
    body.fan_name = displayName || 'Fan';
    const customerId = getSavedCustomerId();
    if (customerId) body.customer_id = customerId;

    fetch('/api/livestream/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) { if (popup) popup.close(); alert(data.error); return; }
        // Store customer_id from response (created server-side)
        if (data.customer_id) {
            localStorage.setItem('pfm_customer_id', data.customer_id);
        }
        // Redirect the already-open popup to Stripe Checkout
        if (popup) popup.location.href = data.checkout_url;
        // Poll localStorage for payment completion
        startPaymentPoll(type, tier, tipCents);
    })
    .catch(e => { if (popup) popup.close(); alert('Checkout error: ' + e.message); });
}

function startPaymentPoll(type, tier, tipCents) {
    if (paymentPollTimer) clearInterval(paymentPollTimer);
    localStorage.removeItem('pfm_payment');
    paymentPollTimer = setInterval(() => {
        const raw = localStorage.getItem('pfm_payment');
        if (!raw) return;
        clearInterval(paymentPollTimer);
        paymentPollTimer = null;
        const payment = JSON.parse(raw);
        localStorage.removeItem('pfm_payment');

        // Update saved card indicator
        updateSavedCardIndicator();

        if (payment.type === 'spotlight') {
            socket.emit('spotlight-request', {
                streamId: STREAM_ID,
                session_id: payment.session_id,
                tier: tier,
                name: displayName || 'Fan',
                peerId: peerId
            });
        } else if (payment.type === 'tip') {
            socket.emit('super-tip', {
                streamId: STREAM_ID,
                session_id: payment.session_id,
                amount_cents: tipCents,
                name: displayName || 'Fan'
            });
            isTipper = true;
        }
    }, 500);
}

// --- Tip bubbles ---
function showTipBubble(name, cents) {
    const overlay = document.getElementById('tip-overlay');
    const bubble = document.createElement('div');
    bubble.className = 'tip-bubble';
    const scale = Math.min(2, 0.8 + (cents / 2000));
    bubble.style.fontSize = (14 * scale) + 'px';
    bubble.textContent = name + ' $' + (cents / 100).toFixed(2);
    overlay.appendChild(bubble);
    setTimeout(() => bubble.remove(), 3200);
}

// --- Leaderboard ---
function renderLeaderboard(lb, totalCents) {
    const panel = document.getElementById('leaderboard-panel');
    const list = document.getElementById('lb-list');
    const total = document.getElementById('lb-total');
    if (!lb || lb.length === 0) { panel.style.display = 'none'; return; }
    panel.style.display = 'block';
    total.textContent = '$' + (totalCents / 100).toFixed(2) + ' total';
    const icons = ['gold', 'silver', 'bronze'];
    const medals = ['&#128081;', '&#129352;', '&#129353;'];
    list.innerHTML = lb.map((entry, i) => '<li>' +
        '<span class="lb-rank ' + (icons[i] || '') + '">' + (i < 3 ? medals[i] : (i + 1)) + '</span>' +
        '<span class="lb-name">' + escHtml(entry[0]) + '</span>' +
        '<span class="lb-amount">$' + (entry[1] / 100).toFixed(2) + '</span></li>').join('');
}

// --- Media consumers ---
async function consumeProducer(producerId, kind, roomId) {
    socket.emit('consume', {
        producerId: producerId,
        roomId: roomId,
        peerId: peerId,
        rtpCapabilities: device.rtpCapabilities
    });

    return new Promise((resolve) => {
        socket.once('consumed', async (data) => {
            const consumer = await recvTransport.consume({
                id: data.consumerId,
                producerId: data.producerId,
                kind: data.kind,
                rtpParameters: data.rtpParameters
            });

            const track = consumer.track;
            if (data.kind === 'video') {
                const grid = document.getElementById('video-grid');
                grid.style.display = 'grid';
                const video = document.getElementById('video-player');
                const stream = new MediaStream([track]);
                video.srcObject = stream;
            } else {
                const audio = document.getElementById('audio-player');
                const stream = audio.srcObject ? audio.srcObject : new MediaStream();
                stream.addTrack(track);
                audio.srcObject = stream;
            }

            socket.emit('resume-consumer', {
                consumerId: data.consumerId,
                roomId: roomId,
                peerId: peerId
            });

            resolve(consumer);
        });
    });
}

function appendChat(name, msg, tipBadge) {
    const box = document.getElementById('chat-box');
    const div = document.createElement('div');
    div.className = 'chat-msg';
    const badge = tipBadge ? '<span class="tipper-badge">Tipper</span>' : '';
    div.innerHTML = badge + '<span class="name">' + escHtml(name) + ':</span> ' + escHtml(msg);
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
}

function sendChat() {
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg || !socket) return;
    socket.emit('chat-message', { streamId: STREAM_ID, message: msg, name: displayName, isTipper: isTipper });
    appendChat(displayName, msg, isTipper);
    input.value = '';
}

function updateListenerCount(n) {
    document.getElementById('listener-count').textContent = n + ' listener' + (n !== 1 ? 's' : '');
}

function setStatus(msg, type) {
    const el = document.getElementById('status');
    el.textContent = msg;
    el.className = 'status-' + type;
}

function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// Auto-join on page load
joinStream();
</script>
</body>
</html>
"""


ADMIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Power FM LIVE — Admin</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:""" + BG + """;color:""" + TEXT + """;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh}
.container{max-width:960px;margin:0 auto;padding:20px}
h1{font-size:24px;margin-bottom:20px;color:""" + ACCENT + """}
.panel{background:""" + PANEL + """;border-radius:12px;padding:20px;margin-bottom:16px}
table{width:100%;border-collapse:collapse}
th{text-align:left;color:""" + MUTED + """;font-size:12px;text-transform:uppercase;letter-spacing:1px;padding:8px 12px;border-bottom:1px solid #333}
td{padding:10px 12px;border-bottom:1px solid #222;font-size:14px}
.badge{padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700}
.badge-live{background:""" + ACCENT + """;color:#fff}
.badge-ended{background:#555;color:#ccc}
.badge-scheduled{background:#2980b9;color:#fff}
a{color:""" + ACCENT + """}
.stat{text-align:center;padding:16px}
.stat h3{font-size:28px;color:""" + ACCENT + """;margin-bottom:4px}
.stat p{color:""" + MUTED + """;font-size:12px}
.stat-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
</style>
</head>
<body>
<div class="container">
    <h1>Livestream Admin</h1>

    <div class="stat-row" id="stats">
        <div class="panel stat"><h3 id="stat-live">0</h3><p>Live Now</p></div>
        <div class="panel stat"><h3 id="stat-total">0</h3><p>Total Streams</p></div>
        <div class="panel stat"><h3 id="stat-listeners">0</h3><p>Listeners Now</p></div>
        <div class="panel stat"><h3 id="stat-peak">0</h3><p>All-Time Peak</p></div>
    </div>

    <div class="panel">
        <h3 style="margin-bottom:12px">All Streams</h3>
        <table>
            <thead><tr><th>Title</th><th>Host</th><th>Status</th><th>Listeners</th><th>Peak</th><th>Started</th><th>Actions</th></tr></thead>
            <tbody id="streams-table"></tbody>
        </table>
    </div>
</div>
<script>
async function loadAdmin() {
    const res = await fetch('/api/livestream/streams');
    const data = await res.json();
    const streams = data.streams || [];

    const live = streams.filter(s => s.status === 'live');
    const peak = Math.max(0, ...streams.map(s => s.max_listeners || 0));
    const totalListeners = live.reduce((a, s) => a + (s.listener_count || 0), 0);

    document.getElementById('stat-live').textContent = live.length;
    document.getElementById('stat-total').textContent = streams.length;
    document.getElementById('stat-listeners').textContent = totalListeners;
    document.getElementById('stat-peak').textContent = peak;

    const tbody = document.getElementById('streams-table');
    tbody.innerHTML = streams.map(s => `
        <tr>
            <td><a href="/live/${s.id}">${esc(s.title)}</a></td>
            <td>${esc(s.host_name)}</td>
            <td><span class="badge badge-${s.status}">${s.status.toUpperCase()}</span></td>
            <td>${s.listener_count || 0}</td>
            <td>${s.max_listeners || 0}</td>
            <td>${s.started_at ? new Date(s.started_at).toLocaleString() : '-'}</td>
            <td>${s.status === 'live' ? '<a href="#" onclick="endStream(\\''+s.id+'\\');return false">End</a>' : ''}</td>
        </tr>
    `).join('');
}

async function endStream(id) {
    if (!confirm('End this stream?')) return;
    await fetch('/api/livestream/streams/' + id + '/end', { method: 'POST' });
    loadAdmin();
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

loadAdmin();
setInterval(loadAdmin, 10000);
</script>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@livestream_bp.route('/live')
def live_directory():
    return render_template_string(DIRECTORY_HTML)


@livestream_bp.route('/live/broadcast')
def live_broadcast():
    return render_template_string(BROADCAST_HTML)


@livestream_bp.route('/live/<stream_id>')
def live_listener(stream_id):
    return render_template_string(LISTENER_HTML, stream_id=stream_id)


@livestream_bp.route('/live/admin')
def live_admin():
    return render_template_string(ADMIN_HTML)


# ── API Routes ──

@livestream_bp.route('/api/livestream/status')
def api_livestream_status():
    sfu = _get_sfu()
    sfu_running = False
    if sfu:
        try:
            sfu_running = sfu.is_sfu_running()
        except Exception:
            pass
    return jsonify({
        'sfu_running': sfu_running,
        'active_streams': len([s for s in active_streams.values()]),
        'total_listeners': sum(len(s.get('listeners', {})) for s in active_streams.values()),
    })


@livestream_bp.route('/api/livestream/streams')
def api_livestream_streams():
    db = _get_ls_db()
    streams = []
    if db:
        try:
            conn = db.get_connection()
            rows = db.get_recent_livestreams(conn, limit=50)
            for r in rows:
                s = dict(r)
                # Add live listener count from memory
                if s['id'] in active_streams:
                    s['listener_count'] = len(active_streams[s['id']].get('listeners', {}))
                else:
                    s['listener_count'] = 0
                streams.append(s)
            conn.close()
        except Exception as e:
            return jsonify({'streams': [], 'error': str(e)})
    # Also include any in-memory-only streams not yet in DB
    for sid, info in active_streams.items():
        if not any(s['id'] == sid for s in streams):
            streams.insert(0, {
                'id': sid,
                'title': info.get('title', 'Live Stream'),
                'host_name': info.get('host_name', 'DJ'),
                'status': 'live',
                'stream_type': info.get('stream_type', 'audio'),
                'listener_count': len(info.get('listeners', {})),
                'max_listeners': info.get('max_listeners', 0),
                'started_at': info.get('started_at'),
            })
    return jsonify({'streams': streams})


@livestream_bp.route('/api/livestream/streams/<stream_id>')
def api_livestream_stream(stream_id):
    db = _get_ls_db()
    if db:
        try:
            conn = db.get_connection()
            row = db.get_livestream(conn, stream_id)
            conn.close()
            if row:
                s = dict(row)
                if stream_id in active_streams:
                    s['listener_count'] = len(active_streams[stream_id].get('listeners', {}))
                return jsonify(s)
        except Exception:
            pass
    if stream_id in active_streams:
        info = active_streams[stream_id]
        return jsonify({
            'id': stream_id,
            'title': info.get('title', 'Live Stream'),
            'host_name': info.get('host_name', 'DJ'),
            'status': 'live',
            'listener_count': len(info.get('listeners', {})),
        })
    return jsonify({'error': 'Stream not found'}), 404


@livestream_bp.route('/api/livestream/streams/<stream_id>/end', methods=['POST'])
def api_end_stream(stream_id):
    _end_stream(stream_id)
    return jsonify({'ok': True})


@livestream_bp.route('/api/livestream/checkout', methods=['POST'])
def api_livestream_checkout():
    """Create a Stripe Checkout session for spotlight or tip."""
    data = request.get_json(force=True, silent=True) or {}
    stream_id = data.get('stream_id')
    pay_type = data.get('type')  # 'spotlight' or 'tip'
    fan_name = data.get('fan_name', 'Fan')

    if not stream_id or pay_type not in ('spotlight', 'tip'):
        return jsonify({'error': 'Invalid request'}), 400

    stripe = _get_stripe_client()
    if not stripe:
        return jsonify({'error': 'Stripe not configured'}), 503

    base_url = request.host_url.rstrip('/')

    # Create or reuse Stripe Customer so card gets saved
    customer_id = data.get('customer_id')
    if not customer_id:
        customer = stripe.create_customer(
            email=f'{fan_name.lower().replace(" ", "")}@fan.powerfm.live',
            name=fan_name,
            metadata={'source': 'livestream', 'stream_id': stream_id},
        )
        if customer:
            customer_id = customer.get('id')

    if pay_type == 'spotlight':
        tier = data.get('tier')
        if tier not in SPOTLIGHT_TIERS:
            return jsonify({'error': 'Invalid tier'}), 400
        tier_info = SPOTLIGHT_TIERS[tier]
        product_name = f'Power FM Spotlight — {tier}'
        amount = tier_info['price']
        success_url = f'{base_url}/live/payment-success?session_id={{CHECKOUT_SESSION_ID}}&type=spotlight&customer_id={customer_id or ""}'
        cancel_url = f'{base_url}/live/{stream_id}'
    else:
        amount = data.get('amount_cents')
        if not amount or int(amount) not in TIP_PRESETS:
            return jsonify({'error': 'Invalid tip amount'}), 400
        amount = int(amount)
        product_name = f'Power FM Super Tip — ${amount / 100:.2f}'
        success_url = f'{base_url}/live/payment-success?session_id={{CHECKOUT_SESSION_ID}}&type=tip&customer_id={customer_id or ""}'
        cancel_url = f'{base_url}/live/{stream_id}'

    metadata = {
        'stream_id': stream_id,
        'type': pay_type,
        'fan_name': fan_name,
    }
    if pay_type == 'spotlight':
        metadata['tier'] = tier

    session = stripe.create_one_time_checkout(
        product_name=product_name,
        amount_cents=amount,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        customer_id=customer_id,
    )
    if not session:
        return jsonify({'error': 'Failed to create checkout session'}), 500

    return jsonify({
        'checkout_url': session.get('url'),
        'session_id': session.get('id'),
        'customer_id': customer_id,
    })


@livestream_bp.route('/api/livestream/quick-pay', methods=['POST'])
def api_livestream_quick_pay():
    """Charge a saved payment method instantly — no popup, no redirect."""
    data = request.get_json(force=True, silent=True) or {}
    customer_id = data.get('customer_id')
    stream_id = data.get('stream_id')
    pay_type = data.get('type')  # 'spotlight' or 'tip'

    if not customer_id or not stream_id or pay_type not in ('spotlight', 'tip'):
        return jsonify({'error': 'Invalid request'}), 400

    stripe = _get_stripe_client()
    if not stripe:
        return jsonify({'error': 'Stripe not configured'}), 503

    # Get saved payment methods for this customer
    pm_result = stripe.list_payment_methods(customer_id)
    if not pm_result or not pm_result.get('data'):
        return jsonify({'error': 'No saved payment method', 'need_checkout': True}), 400

    payment_method_id = pm_result['data'][0]['id']
    card_info = pm_result['data'][0].get('card', {})

    # Determine amount
    if pay_type == 'spotlight':
        tier = data.get('tier')
        if tier not in SPOTLIGHT_TIERS:
            return jsonify({'error': 'Invalid tier'}), 400
        amount = SPOTLIGHT_TIERS[tier]['price']
        description = f'Power FM Spotlight — {tier}'
    else:
        amount = data.get('amount_cents')
        if not amount or int(amount) not in TIP_PRESETS:
            return jsonify({'error': 'Invalid tip amount'}), 400
        amount = int(amount)
        description = f'Power FM Super Tip — ${amount / 100:.2f}'

    metadata = {
        'stream_id': stream_id,
        'type': pay_type,
        'fan_name': data.get('fan_name', 'Fan'),
    }
    if pay_type == 'spotlight':
        metadata['tier'] = data.get('tier')

    # Create and confirm PaymentIntent with saved card
    pi = stripe.create_payment_intent(
        amount_cents=amount,
        customer_id=customer_id,
        payment_method_id=payment_method_id,
        description=description,
        metadata=metadata,
    )
    if not pi:
        return jsonify({'error': 'Payment failed', 'need_checkout': True}), 402

    if pi.get('status') != 'succeeded':
        return jsonify({'error': 'Payment not completed', 'status': pi.get('status'), 'need_checkout': True}), 402

    return jsonify({
        'success': True,
        'payment_intent_id': pi.get('id'),
        'card_brand': card_info.get('brand', 'card'),
        'card_last4': card_info.get('last4', '****'),
    })


@livestream_bp.route('/live/payment-success')
def live_payment_success():
    """Popup success page — writes to localStorage and auto-closes."""
    return render_template_string(PAYMENT_SUCCESS_HTML)


def _end_stream(stream_id):
    """End a stream, clean up memory, update DB."""
    db = _get_ls_db()
    if db:
        try:
            conn = db.get_connection()
            db.end_livestream(conn, stream_id)
            conn.close()
        except Exception:
            pass
    info = active_streams.pop(stream_id, None)
    if info:
        # Clean up active guest SFU peer if any
        if info.get('active_guest'):
            guest = info['active_guest']
            sfu = _get_sfu()
            if sfu and guest.get('peer_id'):
                try:
                    sfu.leave_room(info['room_id'], guest['peer_id'])
                except Exception:
                    pass
        socketio.emit('stream-ended', {'streamId': stream_id}, room=stream_id)


# ══════════════════════════════════════════════════════════════════════════════
# SOCKET.IO EVENTS
# ══════════════════════════════════════════════════════════════════════════════

@socketio.on('join-as-host')
def handle_join_host(data):
    title = data.get('title', 'Power FM Live')
    host_name = data.get('hostName', 'DJ')
    stream_type = data.get('streamType', 'audio')
    description = data.get('description', '')
    peer_id = data.get('peerId', 'host-' + str(uuid.uuid4())[:8])

    stream_id = 'live-' + str(uuid.uuid4())[:8]
    room_id = 'live-' + stream_id
    now = datetime.utcnow().isoformat()

    # Store in DB
    db = _get_ls_db()
    if db:
        try:
            conn = db.get_connection()
            db.create_livestream(conn, stream_id, title, host_name, room_id,
                                 description=description, stream_type=stream_type)
            db.start_livestream(conn, stream_id, peer_id)
            conn.close()
        except Exception as e:
            print(f"[livestream] DB error creating stream: {e}")

    # Store in memory
    active_streams[stream_id] = {
        'title': title,
        'host_name': host_name,
        'host_sid': request.sid,
        'host_peer_id': peer_id,
        'room_id': room_id,
        'stream_type': stream_type,
        'producers': {},
        'listeners': {},
        'max_listeners': 0,
        'started_at': now,
        'guest_queue': [],
        'active_guest': None,
        'tips': [],
        'leaderboard': {},
        'total_tips_cents': 0,
    }

    join_room(stream_id)

    # Try to set up SFU transport for host
    response = {
        'streamId': stream_id,
        'roomId': room_id,
        'iceServers': _get_ls_config()['ICE_SERVERS'],
    }

    sfu = _get_sfu()
    if sfu:
        try:
            # Join SFU room
            sfu.join_room(room_id, peer_id, host_name)

            # Get router RTP capabilities
            rtp_caps = sfu.get_router_rtp_capabilities(room_id)
            response['rtpCapabilities'] = rtp_caps

            # Create send transport for host
            transport = sfu.create_webrtc_transport(room_id, peer_id, consuming=False)
            response['sendTransport'] = transport

        except Exception as e:
            print(f"[livestream] SFU error: {e}")
            response['sfuError'] = str(e)

    emit('host-joined', response)


@socketio.on('join-as-listener')
def handle_join_listener(data):
    stream_id = data.get('streamId')
    name = data.get('name', 'Listener')
    peer_id = data.get('peerId', 'listener-' + str(uuid.uuid4())[:8])

    if stream_id not in active_streams:
        emit('error', {'message': 'Stream not found'})
        return

    info = active_streams[stream_id]
    room_id = info['room_id']

    # Track listener
    info['listeners'][request.sid] = {'name': name, 'peerId': peer_id}
    count = len(info['listeners'])
    if count > info['max_listeners']:
        info['max_listeners'] = count

    join_room(stream_id)

    # DB: add listener session
    db = _get_ls_db()
    if db:
        try:
            conn = db.get_connection()
            db.add_listener_session(conn, stream_id, peer_id, request.sid, display_name=name)
            db.update_livestream_listeners(conn, stream_id, count, count)
            conn.close()
        except Exception:
            pass

    # Notify others
    emit('listener-joined', {'name': name, 'listenerCount': count}, room=stream_id, include_self=False)

    # Build welcome response
    response = {
        'title': info['title'],
        'hostName': info['host_name'],
        'streamType': info.get('stream_type', 'audio'),
        'listenerCount': count,
        'roomId': room_id,
        'iceServers': _get_ls_config()['ICE_SERVERS'],
        'recentChat': [],
    }

    # Get recent chat from DB
    if db:
        try:
            conn = db.get_connection()
            msgs = db.get_chat_messages(conn, stream_id, limit=50)
            response['recentChat'] = [{'name': m['sender_name'], 'message': m['message']} for m in msgs]
            conn.close()
        except Exception:
            pass

    # Set up SFU consumer transport
    sfu = _get_sfu()
    if sfu:
        try:
            sfu.join_room(room_id, peer_id, name)
            rtp_caps = sfu.get_router_rtp_capabilities(room_id)
            response['rtpCapabilities'] = rtp_caps

            transport = sfu.create_webrtc_transport(room_id, peer_id, consuming=True)
            response['recvTransport'] = transport

            # Get existing producers to consume
            producers = sfu.get_producers(room_id, peer_id)
            response['producers'] = producers if producers else []

        except Exception as e:
            print(f"[livestream] SFU consumer error: {e}")

    emit('listener-welcome', response)


@socketio.on('connect-transport')
def handle_connect_transport(data):
    transport_id = data.get('transportId')
    dtls_params = data.get('dtlsParameters')
    room_id = data.get('roomId')
    peer_id = data.get('peerId')

    sfu = _get_sfu()
    if sfu:
        try:
            sfu.connect_transport(room_id, peer_id, transport_id, dtls_params)
            emit('transport-connected', {})
        except Exception as e:
            emit('transport-connect-error', {'message': str(e)})
    else:
        emit('transport-connect-error', {'message': 'SFU not available'})


@socketio.on('produce')
def handle_produce(data):
    transport_id = data.get('transportId')
    kind = data.get('kind')
    rtp_params = data.get('rtpParameters')
    app_data = data.get('appData', {})
    room_id = data.get('roomId')
    peer_id = data.get('peerId')

    sfu = _get_sfu()
    if sfu:
        try:
            result = sfu.produce(room_id, peer_id, transport_id, kind, rtp_params, app_data)
            producer_id = result.get('id') if isinstance(result, dict) else result
            emit('produced', {'producerId': producer_id})

            # Notify listeners about new producer
            for sid, stream_info in active_streams.items():
                if stream_info.get('room_id') == room_id:
                    socketio.emit('new-producer', {
                        'producerId': producer_id,
                        'kind': kind,
                        'roomId': room_id,
                    }, room=sid, include_self=False)
                    break
        except Exception as e:
            emit('produce-error', {'message': str(e)})
    else:
        emit('produce-error', {'message': 'SFU not available'})


@socketio.on('consume')
def handle_consume(data):
    producer_id = data.get('producerId')
    room_id = data.get('roomId')
    peer_id = data.get('peerId')
    rtp_capabilities = data.get('rtpCapabilities')

    sfu = _get_sfu()
    if sfu:
        try:
            result = sfu.consume(room_id, peer_id, producer_id, rtp_capabilities)
            emit('consumed', {
                'consumerId': result.get('id'),
                'producerId': result.get('producerId', producer_id),
                'kind': result.get('kind'),
                'rtpParameters': result.get('rtpParameters'),
            })
        except Exception as e:
            emit('consume-error', {'message': str(e)})


@socketio.on('resume-consumer')
def handle_resume_consumer(data):
    consumer_id = data.get('consumerId')
    room_id = data.get('roomId')
    peer_id = data.get('peerId')

    sfu = _get_sfu()
    if sfu:
        try:
            sfu.resume_consumer(room_id, peer_id, consumer_id)
        except Exception:
            pass


@socketio.on('pause-producer')
def handle_pause_producer(data):
    producer_id = data.get('producerId')
    room_id = data.get('roomId')
    peer_id = data.get('peerId')

    sfu = _get_sfu()
    if sfu:
        try:
            sfu.pause_producer(room_id, peer_id, producer_id)
        except Exception:
            pass


@socketio.on('resume-producer')
def handle_resume_producer(data):
    producer_id = data.get('producerId')
    room_id = data.get('roomId')
    peer_id = data.get('peerId')

    sfu = _get_sfu()
    if sfu:
        try:
            sfu.resume_producer(room_id, peer_id, producer_id)
        except Exception:
            pass


@socketio.on('chat-message')
def handle_chat_message(data):
    stream_id = data.get('streamId')
    message = data.get('message', '').strip()
    name = data.get('name', 'Anonymous')

    if not message or not stream_id:
        return

    # Save to DB
    db = _get_ls_db()
    if db:
        try:
            conn = db.get_connection()
            db.add_chat_message(conn, stream_id, name, request.sid, message)
            conn.close()
        except Exception:
            pass

    # Check if sender is a tipper
    is_tipper = data.get('isTipper', False)

    # Broadcast to room (excluding sender — they already show it locally)
    emit('chat-message', {'name': name, 'message': message, 'isTipper': is_tipper}, room=stream_id, include_self=False)


@socketio.on('end-broadcast')
def handle_end_broadcast(data):
    stream_id = data.get('streamId')
    room_id = data.get('roomId')
    peer_id = data.get('peerId')

    if stream_id:
        # Leave SFU
        sfu = _get_sfu()
        if sfu and room_id and peer_id:
            try:
                sfu.leave_room(room_id, peer_id)
            except Exception:
                pass
        _end_stream(stream_id)


# ── Spotlight & Tip Socket.IO Events ──

def _emit_queue_update(stream_id, info):
    """Send updated guest queue to DJ."""
    host_sid = info.get('host_sid')
    if host_sid:
        queue_data = [{
            'name': g['name'],
            'tier': g['tier'],
            'duration': g['duration_seconds'],
            'price': SPOTLIGHT_TIERS.get(g['tier'], {}).get('price', 0),
        } for g in info.get('guest_queue', [])]
        socketio.emit('guest-queue-updated', {'queue': queue_data}, room=host_sid)


def _end_spotlight(stream_id):
    """End the active spotlight, clean up SFU peer, notify room."""
    if stream_id not in active_streams:
        return
    info = active_streams[stream_id]
    guest = info.get('active_guest')
    if not guest:
        return
    info['active_guest'] = None

    # Clean up SFU peer for guest
    sfu = _get_sfu()
    if sfu and guest.get('peer_id'):
        try:
            sfu.leave_room(info['room_id'], guest['peer_id'])
        except Exception:
            pass

    socketio.emit('spotlight-expired', {'streamId': stream_id}, room=stream_id)


def _spotlight_timer(stream_id, duration):
    """Background greenlet: emits spotlight-tick every 10s, spotlight-expired at end."""
    elapsed = 0
    while elapsed < duration:
        socketio.sleep(10)
        elapsed += 10
        remaining = max(0, duration - elapsed)
        if stream_id not in active_streams:
            return
        info = active_streams[stream_id]
        if not info.get('active_guest'):
            return
        socketio.emit('spotlight-tick', {
            'streamId': stream_id,
            'remaining': remaining,
        }, room=stream_id)
    # Time's up
    _end_spotlight(stream_id)


@socketio.on('spotlight-request')
def handle_spotlight_request(data):
    """Fan submits a paid spotlight request (server verifies Stripe session or PaymentIntent)."""
    stream_id = data.get('streamId')
    session_id = data.get('session_id')
    payment_intent_id = data.get('payment_intent_id')
    tier = data.get('tier')
    name = data.get('name', 'Fan')
    fan_peer_id = data.get('peerId')

    if stream_id not in active_streams or tier not in SPOTLIGHT_TIERS:
        emit('error', {'message': 'Invalid spotlight request'})
        return

    # Verify payment with Stripe
    stripe = _get_stripe_client()
    if not stripe:
        emit('error', {'message': 'Stripe not available'})
        return

    if payment_intent_id:
        pi = stripe.get_payment_intent(payment_intent_id)
        if not pi or pi.get('status') != 'succeeded':
            emit('error', {'message': 'Payment not verified'})
            return
    elif session_id:
        session = stripe.get_checkout_session(session_id)
        if not session or session.get('payment_status') != 'paid':
            emit('error', {'message': 'Payment not verified'})
            return
    else:
        emit('error', {'message': 'No payment reference provided'})
        return

    info = active_streams[stream_id]
    tier_info = SPOTLIGHT_TIERS[tier]

    # Add to queue
    info['guest_queue'].append({
        'sid': request.sid,
        'peer_id': fan_peer_id,
        'name': name,
        'tier': tier,
        'duration_seconds': tier_info['duration'],
        'session_id': session_id,
    })

    position = len(info['guest_queue'])
    emit('spotlight-pending', {'position': position, 'tier': tier})

    # Notify DJ
    _emit_queue_update(stream_id, info)


@socketio.on('approve-guest')
def handle_approve_guest(data):
    """DJ approves a guest — server creates SFU send transport for fan."""
    stream_id = data.get('streamId')
    index = data.get('index', 0)

    if stream_id not in active_streams:
        return

    info = active_streams[stream_id]

    # Only the host can approve
    if request.sid != info.get('host_sid'):
        return

    # Can't approve if someone is already on screen
    if info.get('active_guest'):
        emit('error', {'message': 'A guest is already on screen. Remove them first.'})
        return

    if index < 0 or index >= len(info['guest_queue']):
        return

    guest = info['guest_queue'].pop(index)

    # Create SFU send transport for the guest
    response = {'iceServers': _get_ls_config()['ICE_SERVERS']}
    sfu = _get_sfu()
    if sfu:
        try:
            transport = sfu.create_webrtc_transport(info['room_id'], guest['peer_id'], consuming=False)
            response['sendTransport'] = transport
        except Exception as e:
            print(f"[livestream] SFU transport error for guest: {e}")

    # Send approval to the fan
    socketio.emit('spotlight-approved', response, room=guest['sid'])

    # Set active guest
    info['active_guest'] = {
        'sid': guest['sid'],
        'peer_id': guest['peer_id'],
        'name': guest['name'],
        'duration_seconds': guest['duration_seconds'],
        'started_at': time.time(),
    }

    # Notify everyone that spotlight started
    socketio.emit('spotlight-started', {
        'streamId': stream_id,
        'name': guest['name'],
        'duration': guest['duration_seconds'],
    }, room=stream_id)

    # Update queue for DJ
    _emit_queue_update(stream_id, info)

    # Start countdown timer in background
    socketio.start_background_task(_spotlight_timer, stream_id, guest['duration_seconds'])


@socketio.on('reject-guest')
def handle_reject_guest(data):
    """DJ rejects a guest, removes from queue."""
    stream_id = data.get('streamId')
    index = data.get('index', 0)

    if stream_id not in active_streams:
        return

    info = active_streams[stream_id]
    if request.sid != info.get('host_sid'):
        return

    if index < 0 or index >= len(info['guest_queue']):
        return

    info['guest_queue'].pop(index)
    _emit_queue_update(stream_id, info)


@socketio.on('end-spotlight')
def handle_end_spotlight(data):
    """DJ ends spotlight early."""
    stream_id = data.get('streamId')
    if stream_id not in active_streams:
        return

    info = active_streams[stream_id]
    if request.sid != info.get('host_sid'):
        return

    _end_spotlight(stream_id)


@socketio.on('super-tip')
def handle_super_tip(data):
    """Fan sends a verified Super Tip (via checkout session or quick-pay PaymentIntent)."""
    stream_id = data.get('streamId')
    session_id = data.get('session_id')
    payment_intent_id = data.get('payment_intent_id')
    amount_cents = data.get('amount_cents', 0)
    name = data.get('name', 'Fan')

    if stream_id not in active_streams:
        emit('error', {'message': 'Stream not found'})
        return

    # Verify payment with Stripe
    stripe = _get_stripe_client()
    if not stripe:
        emit('error', {'message': 'Stripe not available'})
        return

    if payment_intent_id:
        pi = stripe.get_payment_intent(payment_intent_id)
        if not pi or pi.get('status') != 'succeeded':
            emit('error', {'message': 'Payment not verified'})
            return
    elif session_id:
        session = stripe.get_checkout_session(session_id)
        if not session or session.get('payment_status') != 'paid':
            emit('error', {'message': 'Payment not verified'})
            return
    else:
        emit('error', {'message': 'No payment reference provided'})
        return

    info = active_streams[stream_id]

    # Record tip
    info['tips'].append({
        'name': name,
        'amount_cents': amount_cents,
        'ts': time.time(),
    })
    info['total_tips_cents'] += amount_cents

    # Update leaderboard
    info['leaderboard'][name] = info['leaderboard'].get(name, 0) + amount_cents

    # Notify everyone
    socketio.emit('tip-received', {
        'name': name,
        'amount_cents': amount_cents,
    }, room=stream_id)

    # Send leaderboard update
    sorted_lb = sorted(info['leaderboard'].items(), key=lambda x: x[1], reverse=True)[:10]
    socketio.emit('leaderboard-update', {
        'leaderboard': sorted_lb,
        'total_cents': info['total_tips_cents'],
    }, room=stream_id)


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    for stream_id, info in list(active_streams.items()):
        # Check if this was a listener
        if sid in info.get('listeners', {}):
            listener = info['listeners'].pop(sid)
            count = len(info['listeners'])

            # If listener was in guest queue, remove them
            info['guest_queue'] = [g for g in info.get('guest_queue', []) if g.get('sid') != sid]
            # Notify DJ of updated queue
            if info.get('guest_queue') is not None:
                _emit_queue_update(stream_id, info)

            # If listener was the active guest, end their spotlight
            if info.get('active_guest') and info['active_guest'].get('sid') == sid:
                _end_spotlight(stream_id)

            # DB: end listener session
            db = _get_ls_db()
            if db:
                try:
                    conn = db.get_connection()
                    db.end_listener_session(conn, sid)
                    conn.close()
                except Exception:
                    pass

            socketio.emit('listener-left', {
                'name': listener['name'],
                'listenerCount': count,
            }, room=stream_id)
            break

        # Check if this was the host
        if sid == info.get('host_sid'):
            _end_stream(stream_id)
            break
