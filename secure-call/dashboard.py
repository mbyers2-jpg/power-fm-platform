"""Ribbon — Secure Conference & Communication App
Flask server: signaling, room management, chat relay, file upload/download, web UI
"""
import os
import sys
import time
import json
import logging
import secrets
import math
from datetime import datetime

import requests as http_requests

from flask import (Flask, render_template, request, jsonify, send_file,
                   redirect, url_for, abort, session)
from flask_socketio import SocketIO, emit, join_room, leave_room

# Local modules
import database as db
import sfu_client
from crypto_utils import (hash_passphrase, verify_passphrase, generate_room_id,
                          generate_invite_token, get_ice_server_list, generate_peer_id)
from config import (PORT, HOST, SECRET_KEY, MAX_CONTENT_LENGTH, UPLOAD_DIR,
                    LOG_DIR, DATA_DIR, BASE_DIR,
                    AMADEUS_API_KEY, AMADEUS_API_SECRET, AMADEUS_BASE_URL,
                    AMADEUS_TOKEN_URL, AMADEUS_FLIGHT_URL, AMADEUS_HOTEL_URL,
                    AMADEUS_CACHE_TTL,
                    OSM_OVERPASS_URL, OSM_NOMINATIM_URL, OSM_USER_AGENT)

# --- App setup ---

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins='*',
                    max_http_buffer_size=MAX_CONTENT_LENGTH,
                    ping_timeout=60, ping_interval=25)

# Logging
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'dashboard.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('ribbon')

# Track connected peers: sid -> { peerId, roomId, displayName }
connected_peers = {}

# Amadeus token cache
_amadeus_token = {'token': None, 'expires_at': 0}

# --- Initialize ---

db.init_db()
log.info("Ribbon database initialized")

# ============================================================
# HTTP Routes
# ============================================================

@app.route('/')
def lobby():
    rooms = db.list_active_rooms()
    return render_template('lobby.html', rooms=rooms)


@app.route('/create', methods=['POST'])
def create_room():
    name = request.form.get('name', '').strip()
    passphrase = request.form.get('passphrase', '').strip()
    display_name = request.form.get('display_name', '').strip()
    is_private = request.form.get('is_private') == 'on'
    require_approval = request.form.get('require_approval') == 'on'
    max_participants = int(request.form.get('max_participants', 15))

    if not name:
        return redirect(url_for('lobby'))
    if not display_name:
        display_name = 'Host'

    room_id = generate_room_id()
    passphrase_hash = hash_passphrase(passphrase) if passphrase else None
    expires_at = time.time() + (24 * 3600)  # 24h

    db.create_room(
        room_id=room_id,
        name=name,
        passphrase_hash=passphrase_hash,
        created_by=display_name,
        expires_at=expires_at,
        max_participants=max_participants,
        is_private=is_private,
        require_approval=require_approval,
    )

    # Generate invite link
    invite_token = generate_invite_token()
    db.create_invite_link(room_id, invite_token, expires_at=expires_at, max_uses=0)

    session['display_name'] = display_name
    session['peer_id'] = generate_peer_id()

    log.info(f"Room created: {room_id} ({name}) by {display_name} [private={is_private}]")
    return redirect(url_for('room_page', room_id=room_id, invite=invite_token))


@app.route('/join/<room_id>', methods=['GET', 'POST'])
def join_room_page(room_id):
    room = db.get_room(room_id)
    if not room:
        abort(404)
    if room['status'] != 'active':
        abort(410)

    invite_token = request.args.get('invite', '')

    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        passphrase = request.form.get('passphrase', '').strip()

        if not display_name:
            return render_template('lobby.html', rooms=db.list_active_rooms(),
                                   error='Display name is required', join_room=room)

        # Check passphrase if room has one
        if room['passphrase_hash']:
            if not passphrase or not verify_passphrase(passphrase, room['passphrase_hash']):
                return render_template('lobby.html', rooms=db.list_active_rooms(),
                                       error='Invalid passphrase', join_room=room)

        # Check capacity
        current = db.count_active_participants(room_id)
        if current >= room['max_participants']:
            return render_template('lobby.html', rooms=db.list_active_rooms(),
                                   error='Room is full', join_room=room)

        session['display_name'] = display_name
        session['peer_id'] = generate_peer_id()
        return redirect(url_for('room_page', room_id=room_id, invite=invite_token))

    # GET — show join form if room needs passphrase
    needs_passphrase = bool(room['passphrase_hash'])
    return render_template('lobby.html', rooms=db.list_active_rooms(),
                           join_room=room, needs_passphrase=needs_passphrase)


@app.route('/room/<room_id>')
def room_page(room_id):
    room = db.get_room(room_id)
    if not room:
        abort(404)

    display_name = session.get('display_name', '')
    peer_id = session.get('peer_id', '')

    if not display_name or not peer_id:
        return redirect(url_for('join_room_page', room_id=room_id))

    invite_token = request.args.get('invite', '')
    sfu_running = sfu_client.is_sfu_running()

    return render_template('room.html',
                           room=room,
                           display_name=display_name,
                           peer_id=peer_id,
                           invite_token=invite_token,
                           sfu_running=sfu_running)


@app.route('/invite/<token>')
def use_invite(token):
    link = db.get_invite_link(token)
    if not link:
        abort(404)

    # Check expiry
    if link['expires_at'] and link['expires_at'] < time.time():
        abort(410)

    room = db.get_room(link['room_id'])
    if not room or room['status'] != 'active':
        abort(410)

    db.use_invite_link(token)
    return redirect(url_for('join_room_page', room_id=link['room_id'], invite=token))


# --- File upload/download ---

@app.route('/api/upload/<room_id>', methods=['POST'])
def upload_file(room_id):
    room = db.get_room(room_id)
    if not room:
        abort(404)

    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file'}), 400

    sender = request.form.get('sender', 'Unknown')
    original_filename = request.form.get('original_filename', file.filename)
    encryption_iv = request.form.get('encryption_iv', '')

    # Generate storage filename
    encrypted_filename = f"{secrets.token_hex(16)}_{int(time.time())}.enc"
    storage_path = os.path.join(UPLOAD_DIR, encrypted_filename)

    file.save(storage_path)
    file_size = os.path.getsize(storage_path)

    db.save_file_record(
        room_id=room_id,
        sender_name=sender,
        original_filename=original_filename,
        encrypted_filename=encrypted_filename,
        file_size=file_size,
        encryption_iv=encryption_iv,
        storage_path=storage_path,
    )

    log.info(f"File uploaded: {original_filename} ({file_size} bytes) to room {room_id}")
    return jsonify({'ok': True, 'filename': original_filename, 'size': file_size})


@app.route('/api/download/<int:file_id>')
def download_file(file_id):
    record = db.get_file_record(file_id)
    if not record:
        abort(404)

    return send_file(
        record['storage_path'],
        as_attachment=True,
        download_name=record['encrypted_filename'],
    )


@app.route('/api/files/<room_id>')
def list_files(room_id):
    files = db.get_shared_files(room_id)
    return jsonify([{
        'id': f['id'],
        'sender': f['sender_name'],
        'filename': f['original_filename'],
        'size': f['file_size'],
        'iv': f['encryption_iv'],
        'uploaded_at': f['uploaded_at'],
    } for f in files])


# --- API ---

@app.route('/api/ice-servers')
def api_ice_servers():
    return jsonify(get_ice_server_list())


@app.route('/api/room/<room_id>/status')
def api_room_status(room_id):
    room = db.get_room(room_id)
    if not room:
        return jsonify({'error': 'Not found'}), 404

    participants = db.get_active_participants(room_id)
    sfu_stats = None
    try:
        sfu_stats = sfu_client.get_room_stats(room_id)
    except Exception:
        pass

    return jsonify({
        'room_id': room_id,
        'name': room['name'],
        'status': room['status'],
        'participant_count': len(participants),
        'max_participants': room['max_participants'],
        'is_private': bool(room['is_private']),
        'sfu_stats': sfu_stats,
    })


@app.route('/api/status')
def api_status():
    """Health endpoint for hub integration."""
    sfu_ok = sfu_client.is_sfu_running()
    rooms = db.list_active_rooms()
    total_participants = sum(db.count_active_participants(r['id']) for r in rooms)

    return jsonify({
        'status': 'running',
        'sfu': 'connected' if sfu_ok else 'disconnected',
        'active_rooms': len(rooms),
        'total_participants': total_participants,
    })


# ============================================================
# Socket.IO — Signaling
# ============================================================

@socketio.on('connect')
def on_connect():
    log.debug(f"Socket connected: {request.sid}")


@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    peer = connected_peers.pop(sid, None)
    if peer:
        room_id = peer['roomId']
        peer_id = peer['peerId']
        display_name = peer['displayName']

        try:
            result = sfu_client.leave_room(room_id, peer_id)
            closed_producers = result.get('closedProducers', [])
            for pid in closed_producers:
                emit('producerClosed', {'producerId': pid}, to=room_id)
        except Exception as e:
            log.error(f"SFU leave error: {e}")

        db.remove_participant(room_id, peer_id)
        leave_room(room_id)

        emit('peerLeft', {
            'peerId': peer_id,
            'displayName': display_name,
        }, to=room_id)

        log.info(f"{display_name} left room {room_id}")


@socketio.on('joinRoom')
def on_join_room(data):
    room_id = data['roomId']
    peer_id = data['peerId']
    display_name = data['displayName']
    sid = request.sid

    room = db.get_room(room_id)
    if not room or room['status'] != 'active':
        emit('error', {'message': 'Room not found or inactive'})
        return

    # Check if room requires approval
    if room['require_approval'] and room['created_by'] != display_name:
        db.create_pending_approval(room_id, display_name, peer_id)
        emit('waitingApproval', {'roomId': room_id})
        # Notify room host
        emit('approvalRequest', {
            'peerId': peer_id,
            'displayName': display_name,
        }, to=room_id)
        return

    # Check capacity
    current = db.count_active_participants(room_id)
    if current >= room['max_participants']:
        emit('error', {'message': 'Room is full'})
        return

    # Join SFU room
    try:
        rtp_capabilities = sfu_client.get_router_rtp_capabilities(room_id)
        sfu_result = sfu_client.join_room(room_id, peer_id, display_name)
    except Exception as e:
        log.error(f"SFU join error: {e}")
        emit('error', {'message': 'Media server unavailable'})
        return

    # Track peer
    connected_peers[sid] = {
        'roomId': room_id,
        'peerId': peer_id,
        'displayName': display_name,
    }

    db.add_participant(room_id, display_name, peer_id)
    join_room(room_id)

    # Get ICE servers
    ice_servers = get_ice_server_list(peer_id)

    # Notify existing peers
    emit('peerJoined', {
        'peerId': peer_id,
        'displayName': display_name,
    }, to=room_id, include_self=False)

    # Send room state to the new peer
    emit('roomJoined', {
        'roomId': room_id,
        'peerId': peer_id,
        'rtpCapabilities': rtp_capabilities,
        'peers': sfu_result.get('peers', []),
        'iceServers': ice_servers,
    })

    log.info(f"{display_name} joined room {room_id}")


@socketio.on('approveParticipant')
def on_approve_participant(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    room = db.get_room(peer['roomId'])
    if not room or room['created_by'] != peer['displayName']:
        return  # Only host can approve

    approval_id = data.get('approvalId')
    approved = data.get('approved', False)

    if approved:
        approval = db.approve_participant(approval_id)
        if approval:
            socketio.emit('approved', {
                'roomId': peer['roomId'],
            }, to=peer['roomId'])
    else:
        db.reject_participant(approval_id)
        socketio.emit('rejected', {
            'peerId': data.get('peerId'),
        }, to=peer['roomId'])


@socketio.on('createTransport')
def on_create_transport(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        emit('error', {'message': 'Not in a room'})
        return

    try:
        transport = sfu_client.create_webrtc_transport(
            peer['roomId'], peer['peerId'],
            consuming=data.get('consuming', False)
        )
        emit('transportCreated', {
            'consuming': data.get('consuming', False),
            **transport,
        })
    except Exception as e:
        log.error(f"Create transport error: {e}")
        emit('error', {'message': 'Failed to create transport'})


@socketio.on('connectTransport')
def on_connect_transport(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    try:
        sfu_client.connect_transport(
            peer['roomId'], peer['peerId'],
            data['transportId'], data['dtlsParameters']
        )
        emit('transportConnected', {'transportId': data['transportId']})
    except Exception as e:
        log.error(f"Connect transport error: {e}")
        emit('error', {'message': 'Failed to connect transport'})


@socketio.on('produce')
def on_produce(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    try:
        result = sfu_client.produce(
            peer['roomId'], peer['peerId'],
            data['transportId'], data['kind'],
            data['rtpParameters'], data.get('appData', {})
        )

        emit('produced', {'id': result['id'], 'kind': data['kind']})

        # Notify other peers about new producer
        emit('newProducer', {
            'producerId': result['id'],
            'peerId': peer['peerId'],
            'kind': data['kind'],
            'appData': data.get('appData', {}),
        }, to=peer['roomId'], include_self=False)

    except Exception as e:
        log.error(f"Produce error: {e}")
        emit('error', {'message': 'Failed to produce'})


@socketio.on('consume')
def on_consume(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    try:
        result = sfu_client.consume(
            peer['roomId'], peer['peerId'],
            data['producerId'], data['rtpCapabilities']
        )
        emit('consumed', {
            'consumerId': result['id'],
            'producerId': result['producerId'],
            'kind': result['kind'],
            'rtpParameters': result['rtpParameters'],
        })
    except Exception as e:
        log.error(f"Consume error: {e}")
        emit('error', {'message': 'Failed to consume'})


@socketio.on('resumeConsumer')
def on_resume_consumer(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    try:
        sfu_client.resume_consumer(
            peer['roomId'], peer['peerId'], data['consumerId']
        )
    except Exception as e:
        log.error(f"Resume consumer error: {e}")


@socketio.on('pauseProducer')
def on_pause_producer(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    try:
        sfu_client.pause_producer(
            peer['roomId'], peer['peerId'], data['producerId']
        )
        emit('producerPaused', {
            'producerId': data['producerId'],
            'peerId': peer['peerId'],
        }, to=peer['roomId'], include_self=False)
    except Exception as e:
        log.error(f"Pause producer error: {e}")


@socketio.on('resumeProducer')
def on_resume_producer(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    try:
        sfu_client.resume_producer(
            peer['roomId'], peer['peerId'], data['producerId']
        )
        emit('producerResumed', {
            'producerId': data['producerId'],
            'peerId': peer['peerId'],
        }, to=peer['roomId'], include_self=False)
    except Exception as e:
        log.error(f"Resume producer error: {e}")


@socketio.on('closeProducer')
def on_close_producer(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    try:
        sfu_client.close_producer(
            peer['roomId'], peer['peerId'], data['producerId']
        )
        emit('producerClosed', {
            'producerId': data['producerId'],
            'peerId': peer['peerId'],
        }, to=peer['roomId'], include_self=False)
    except Exception as e:
        log.error(f"Close producer error: {e}")


@socketio.on('getProducers')
def on_get_producers(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    try:
        result = sfu_client.get_producers(peer['roomId'], peer['peerId'])
        emit('producers', result)
    except Exception as e:
        log.error(f"Get producers error: {e}")


@socketio.on('setPreferredLayers')
def on_set_preferred_layers(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    try:
        sfu_client.set_preferred_layers(
            peer['roomId'], peer['peerId'],
            data['consumerId'], data['spatialLayer'],
            data.get('temporalLayer')
        )
    except Exception as e:
        log.error(f"Set preferred layers error: {e}")


# --- Chat ---

@socketio.on('chatMessage')
def on_chat_message(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    # Store encrypted message
    ts = db.save_chat_message(
        peer['roomId'], peer['displayName'],
        data['ciphertext'], data['iv']
    )

    # Relay to all in room (including sender for confirmation)
    emit('chatMessage', {
        'sender': peer['displayName'],
        'peerId': peer['peerId'],
        'ciphertext': data['ciphertext'],
        'iv': data['iv'],
        'timestamp': ts,
    }, to=peer['roomId'])


@socketio.on('getChatHistory')
def on_get_chat_history(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    messages = db.get_chat_history(peer['roomId'])
    emit('chatHistory', [{
        'sender': m['sender_name'],
        'ciphertext': m['ciphertext'],
        'iv': m['iv'],
        'timestamp': m['timestamp'],
    } for m in messages])


# --- File sharing notification ---

@socketio.on('fileShared')
def on_file_shared(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    emit('fileShared', {
        'sender': peer['displayName'],
        'filename': data['filename'],
        'size': data['size'],
        'fileId': data.get('fileId'),
        'iv': data.get('iv', ''),
    }, to=peer['roomId'])


# ============================================================
# Payments — Socket.IO handlers
# ============================================================

@socketio.on('addExpense')
def on_add_expense(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    room_id = data['roomId']
    desc = data.get('description', '').strip()
    amount = float(data.get('amount', 0))
    paid_by = data.get('paidBy', peer['displayName'])
    split_type = data.get('splitType', 'equal')
    custom_splits = data.get('splits', [])

    if not desc or amount <= 0:
        emit('error', {'message': 'Invalid expense'})
        return

    # Build splits
    if split_type == 'equal':
        participants = _get_room_participant_names(room_id)
        if not participants:
            participants = [peer['displayName']]
        per_person = round(amount / len(participants), 2)
        splits = [{'name': p, 'amount': per_person} for p in participants]
    else:
        splits = custom_splits

    expense_id, ts = db.create_expense(
        room_id, desc, amount, 'USD', paid_by, split_type,
        peer['displayName'], splits
    )

    expenses = db.get_expenses(room_id)
    expense_data = None
    for e in expenses:
        if e['id'] == expense_id:
            expense_data = e
            break

    if expense_data:
        emit('expenseAdded', expense_data, to=room_id)


@socketio.on('getExpenses')
def on_get_expenses(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    room_id = data.get('roomId', peer['roomId'])
    expenses = db.get_expenses(room_id)
    emit('expensesList', {'expenses': expenses})


@socketio.on('settleSplit')
def on_settle_split(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    split_id = data.get('splitId')
    if split_id:
        db.settle_split(split_id)
        emit('splitSettled', {'splitId': split_id}, to=peer['roomId'])


@socketio.on('getBalances')
def on_get_balances(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    room_id = data.get('roomId', peer['roomId'])
    balances = db.get_balances(room_id)
    emit('balances', {'balances': balances})


def _get_room_participant_names(room_id):
    """Get display names of all connected peers in a room."""
    names = set()
    for sid, peer in connected_peers.items():
        if peer['roomId'] == room_id:
            names.add(peer['displayName'])
    return list(names)


# ============================================================
# Nearby — HTTP routes + Socket.IO handlers
# ============================================================

OSM_CATEGORIES = {
    'food': '[amenity~"restaurant|cafe|fast_food|bar|pub|food_court|bakery"]',
    'shopping': '[shop]',
    'entertainment': '[amenity~"cinema|theatre|nightclub|arts_centre|casino|music_venue"]',
    'medical': '[amenity~"hospital|clinic|pharmacy|dentist|doctors"]',
}


@app.route('/api/nearby/search', methods=['POST'])
def nearby_search():
    data = request.get_json()
    lat = data.get('lat')
    lon = data.get('lon')
    category = data.get('category', 'food')
    radius = min(data.get('radius', 2000), 5000)

    if lat is None or lon is None:
        return jsonify({'error': 'Location required'}), 400

    osm_filter = OSM_CATEGORIES.get(category, OSM_CATEGORIES['food'])

    # Overpass QL query — handle compound filters (separated by brackets)
    filters = osm_filter.strip()
    # Build query for each filter group
    query_parts = []
    # Split compound filters like [amenity~"..."][leisure~"..."] into separate queries
    import re
    filter_groups = re.findall(r'\[[^\]]+\]', filters)
    for fg in filter_groups:
        query_parts.append(f'node{fg}(around:{radius},{lat},{lon});')
        query_parts.append(f'way{fg}(around:{radius},{lat},{lon});')

    overpass_query = f"""
    [out:json][timeout:10];
    (
        {"".join(query_parts)}
    );
    out center 30;
    """

    try:
        resp = http_requests.post(
            OSM_OVERPASS_URL,
            data={'data': overpass_query},
            headers={'User-Agent': OSM_USER_AGENT},
            timeout=15
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        log.error(f"Overpass API error: {e}")
        return jsonify({'places': [], 'error': 'Search failed'})

    places = []
    for el in result.get('elements', []):
        tags = el.get('tags', {})
        name = tags.get('name')
        if not name:
            continue

        el_lat = el.get('lat') or (el.get('center', {}).get('lat'))
        el_lon = el.get('lon') or (el.get('center', {}).get('lon'))
        if not el_lat or not el_lon:
            continue

        addr_parts = []
        for key in ['addr:housenumber', 'addr:street', 'addr:city']:
            if tags.get(key):
                addr_parts.append(tags[key])

        distance = _haversine(lat, lon, el_lat, el_lon)

        places.append({
            'name': name,
            'address': ', '.join(addr_parts) if addr_parts else '',
            'category': category,
            'lat': el_lat,
            'lon': el_lon,
            'osm_id': str(el.get('id', '')),
            'distance': round(distance),
        })

    places.sort(key=lambda p: p['distance'])
    return jsonify({'places': places[:30]})


@app.route('/api/nearby/geocode', methods=['POST'])
def nearby_geocode():
    data = request.get_json()
    query = data.get('query', '').strip()
    lat = data.get('lat')
    lon = data.get('lon')

    if not query:
        return jsonify({'error': 'Query required'}), 400

    params = {
        'q': query,
        'format': 'json',
        'limit': 20,
        'addressdetails': 1,
    }
    if lat and lon:
        params['viewbox'] = f"{lon-0.1},{lat+0.1},{lon+0.1},{lat-0.1}"
        params['bounded'] = 0

    try:
        resp = http_requests.get(
            OSM_NOMINATIM_URL,
            params=params,
            headers={'User-Agent': OSM_USER_AGENT},
            timeout=10
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as e:
        log.error(f"Nominatim error: {e}")
        return jsonify({'places': [], 'error': 'Search failed'})

    places = []
    for r in results:
        el_lat = float(r.get('lat', 0))
        el_lon = float(r.get('lon', 0))
        distance = _haversine(lat, lon, el_lat, el_lon) if lat and lon else None

        places.append({
            'name': r.get('display_name', '').split(',')[0],
            'address': r.get('display_name', ''),
            'category': r.get('type', ''),
            'lat': el_lat,
            'lon': el_lon,
            'osm_id': str(r.get('osm_id', '')),
            'distance': round(distance) if distance else None,
        })

    if lat and lon:
        places.sort(key=lambda p: p.get('distance') or 999999)

    return jsonify({'places': places})


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # Earth radius in meters
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@socketio.on('sharePlace')
def on_share_place(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    room_id = data.get('roomId', peer['roomId'])
    place_id, ts = db.save_nearby_share(
        room_id, data['name'], data.get('address', ''),
        data.get('category', ''), data.get('lat'), data.get('lon'),
        data.get('osmId', ''), peer['displayName']
    )

    emit('placeShared', {
        'id': place_id,
        'name': data['name'],
        'address': data.get('address', ''),
        'category': data.get('category', ''),
        'lat': data.get('lat'),
        'lon': data.get('lon'),
        'shared_by': peer['displayName'],
        'shared_at': ts,
    }, to=room_id)


@socketio.on('getSharedPlaces')
def on_get_shared_places(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    room_id = data.get('roomId', peer['roomId'])
    shares = db.get_nearby_shares(room_id)
    emit('sharedPlacesList', {'places': [{
        'id': s['id'],
        'name': s['place_name'],
        'address': s['place_address'],
        'category': s['place_category'],
        'lat': s['latitude'],
        'lon': s['longitude'],
        'shared_by': s['shared_by'],
        'shared_at': s['shared_at'],
    } for s in shares]})


# ============================================================
# Travel — HTTP routes + Amadeus + Socket.IO handlers
# ============================================================

def _get_amadeus_token():
    """Get or refresh Amadeus API token (cached in memory)."""
    global _amadeus_token

    if not AMADEUS_API_KEY or not AMADEUS_API_SECRET:
        return None

    if _amadeus_token['token'] and time.time() < _amadeus_token['expires_at']:
        return _amadeus_token['token']

    try:
        resp = http_requests.post(AMADEUS_TOKEN_URL, data={
            'grant_type': 'client_credentials',
            'client_id': AMADEUS_API_KEY,
            'client_secret': AMADEUS_API_SECRET,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        _amadeus_token = {
            'token': data['access_token'],
            'expires_at': time.time() + data.get('expires_in', 1700) - 60,
        }
        log.info("Amadeus token refreshed")
        return _amadeus_token['token']
    except Exception as e:
        log.error(f"Amadeus token error: {e}")
        return None


@app.route('/api/travel/flights', methods=['POST'])
def travel_flights():
    if not AMADEUS_API_KEY:
        return jsonify({'error': 'Travel search requires an Amadeus API key. Set AMADEUS_API_KEY and AMADEUS_API_SECRET environment variables.'})

    data = request.get_json()
    room_id = data.get('roomId', '')
    origin = data.get('origin', '').upper()
    dest = data.get('dest', '').upper()
    depart = data.get('depart', '')
    return_date = data.get('returnDate')
    pax = data.get('passengers', 1)

    if not origin or not dest or not depart:
        return jsonify({'error': 'Origin, destination, and departure date are required'}), 400

    # Check cache
    search_key = json.dumps({'type': 'flights', 'origin': origin, 'dest': dest,
                             'depart': depart, 'return': return_date, 'pax': pax}, sort_keys=True)
    cached = db.get_cached_search(room_id, 'flights', search_key)
    if cached:
        return jsonify(json.loads(cached['results_json']))

    token = _get_amadeus_token()
    if not token:
        return jsonify({'error': 'Unable to authenticate with travel API'})

    params = {
        'originLocationCode': origin,
        'destinationLocationCode': dest,
        'departureDate': depart,
        'adults': pax,
        'max': 10,
        'currencyCode': 'USD',
    }
    if return_date:
        params['returnDate'] = return_date

    try:
        resp = http_requests.get(
            AMADEUS_FLIGHT_URL,
            params=params,
            headers={'Authorization': f'Bearer {token}'},
            timeout=20
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        log.error(f"Amadeus flights error: {e}")
        return jsonify({'error': 'Flight search failed'})

    flights = []
    for offer in result.get('data', []):
        itin = offer.get('itineraries', [{}])[0]
        segments = itin.get('segments', [])
        first_seg = segments[0] if segments else {}
        last_seg = segments[-1] if segments else {}

        price = offer.get('price', {})
        carrier = first_seg.get('carrierCode', '??')

        flights.append({
            'origin': origin,
            'dest': dest,
            'airline': carrier,
            'departure': first_seg.get('departure', {}).get('at', ''),
            'arrival': last_seg.get('arrival', {}).get('at', ''),
            'duration': itin.get('duration', ''),
            'stops': len(segments) - 1,
            'price': f"${price.get('total', '?')}",
        })

    response = {'flights': flights}

    # Cache
    db.save_travel_search(
        room_id, 'flights', search_key, json.dumps(response),
        'system', time.time() + AMADEUS_CACHE_TTL
    )

    return jsonify(response)


@app.route('/api/travel/hotels', methods=['POST'])
def travel_hotels():
    if not AMADEUS_API_KEY:
        return jsonify({'error': 'Travel search requires an Amadeus API key. Set AMADEUS_API_KEY and AMADEUS_API_SECRET environment variables.'})

    data = request.get_json()
    room_id = data.get('roomId', '')
    city = data.get('city', '').upper()

    if not city:
        return jsonify({'error': 'City code is required'}), 400

    # Check cache
    search_key = json.dumps({'type': 'hotels', 'city': city}, sort_keys=True)
    cached = db.get_cached_search(room_id, 'hotels', search_key)
    if cached:
        return jsonify(json.loads(cached['results_json']))

    token = _get_amadeus_token()
    if not token:
        return jsonify({'error': 'Unable to authenticate with travel API'})

    try:
        resp = http_requests.get(
            AMADEUS_HOTEL_URL,
            params={'cityCode': city},
            headers={'Authorization': f'Bearer {token}'},
            timeout=20
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        log.error(f"Amadeus hotels error: {e}")
        return jsonify({'error': 'Hotel search failed'})

    hotels = []
    for h in result.get('data', [])[:15]:
        hotels.append({
            'name': h.get('name', 'Unknown'),
            'hotelId': h.get('hotelId', ''),
            'address': ', '.join(filter(None, [
                h.get('address', {}).get('streetAddress', ''),
                h.get('address', {}).get('cityName', ''),
            ])),
            'rating': h.get('rating'),
            'price': '',
        })

    response = {'hotels': hotels}

    db.save_travel_search(
        room_id, 'hotels', search_key, json.dumps(response),
        'system', time.time() + AMADEUS_CACHE_TTL
    )

    return jsonify(response)


@socketio.on('shareTravelDeal')
def on_share_travel_deal(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    room_id = data.get('roomId', peer['roomId'])
    deal_type = data.get('type', 'flight')
    deal = data.get('deal', {})

    bookmark_id, ts = db.save_travel_bookmark(
        room_id, None, deal_type, json.dumps(deal), peer['displayName']
    )

    emit('travelDealShared', {
        'id': bookmark_id,
        'type': deal_type,
        'deal': deal,
        'shared_by': peer['displayName'],
        'shared_at': ts,
    }, to=room_id)


@socketio.on('getTravelBookmarks')
def on_get_travel_bookmarks(data):
    sid = request.sid
    peer = connected_peers.get(sid)
    if not peer:
        return

    room_id = data.get('roomId', peer['roomId'])
    bookmarks = db.get_travel_bookmarks(room_id)
    emit('travelBookmarksList', {'bookmarks': [{
        'id': b['id'],
        'type': b['bookmark_type'],
        'deal': json.loads(b['data_json']),
        'shared_by': b['shared_by'],
        'shared_at': b['shared_at'],
    } for b in bookmarks]})


# ============================================================
# Main
# ============================================================

if __name__ == '__main__':
    log.info(f"Starting Ribbon on port {PORT}")
    socketio.run(app, host=HOST, port=PORT, debug=False)
