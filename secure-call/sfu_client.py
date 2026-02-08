"""Ribbon — JSON-RPC client for mediasoup SFU"""
import json
import socket
import os
import time
from config import SFU_SOCKET

_request_id = 0


def _next_id():
    global _request_id
    _request_id += 1
    return _request_id


def _call(method, params=None, timeout=5.0):
    """Send a JSON-RPC request to the SFU over Unix socket."""
    if not os.path.exists(SFU_SOCKET):
        raise ConnectionError(f"SFU socket not found: {SFU_SOCKET}")

    request = {
        'jsonrpc': '2.0',
        'id': _next_id(),
        'method': method,
        'params': params or {},
    }

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(SFU_SOCKET)
        sock.sendall((json.dumps(request) + '\n').encode('utf-8'))

        # Read response
        buffer = b''
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buffer += chunk
            if b'\n' in buffer:
                break

        response_line = buffer.split(b'\n')[0]
        response = json.loads(response_line.decode('utf-8'))

        if 'error' in response and response['error']:
            raise Exception(response['error'].get('message', 'SFU error'))

        return response.get('result')
    finally:
        sock.close()


def ping():
    return _call('ping')


def get_router_rtp_capabilities(room_id):
    return _call('getRouterRtpCapabilities', {'roomId': room_id})


def join_room(room_id, peer_id, display_name):
    return _call('join', {
        'roomId': room_id,
        'peerId': peer_id,
        'displayName': display_name,
    })


def leave_room(room_id, peer_id):
    return _call('leave', {
        'roomId': room_id,
        'peerId': peer_id,
    })


def create_webrtc_transport(room_id, peer_id, consuming=False):
    return _call('createWebRtcTransport', {
        'roomId': room_id,
        'peerId': peer_id,
        'consuming': consuming,
    })


def connect_transport(room_id, peer_id, transport_id, dtls_parameters):
    return _call('connectTransport', {
        'roomId': room_id,
        'peerId': peer_id,
        'transportId': transport_id,
        'dtlsParameters': dtls_parameters,
    })


def produce(room_id, peer_id, transport_id, kind, rtp_parameters, app_data=None):
    return _call('produce', {
        'roomId': room_id,
        'peerId': peer_id,
        'transportId': transport_id,
        'kind': kind,
        'rtpParameters': rtp_parameters,
        'appData': app_data or {},
    })


def consume(room_id, peer_id, producer_id, rtp_capabilities):
    return _call('consume', {
        'roomId': room_id,
        'peerId': peer_id,
        'producerId': producer_id,
        'rtpCapabilities': rtp_capabilities,
    })


def resume_consumer(room_id, peer_id, consumer_id):
    return _call('resumeConsumer', {
        'roomId': room_id,
        'peerId': peer_id,
        'consumerId': consumer_id,
    })


def pause_producer(room_id, peer_id, producer_id):
    return _call('pauseProducer', {
        'roomId': room_id,
        'peerId': peer_id,
        'producerId': producer_id,
    })


def resume_producer(room_id, peer_id, producer_id):
    return _call('resumeProducer', {
        'roomId': room_id,
        'peerId': peer_id,
        'producerId': producer_id,
    })


def close_producer(room_id, peer_id, producer_id):
    return _call('closeProducer', {
        'roomId': room_id,
        'peerId': peer_id,
        'producerId': producer_id,
    })


def get_producers(room_id, peer_id):
    return _call('getProducers', {
        'roomId': room_id,
        'peerId': peer_id,
    })


def set_preferred_layers(room_id, peer_id, consumer_id, spatial_layer, temporal_layer=None):
    return _call('setPreferredLayers', {
        'roomId': room_id,
        'peerId': peer_id,
        'consumerId': consumer_id,
        'spatialLayer': spatial_layer,
        'temporalLayer': temporal_layer,
    })


def get_room_stats(room_id):
    return _call('getRoomStats', {'roomId': room_id})


def is_sfu_running():
    """Check if the SFU is running and responsive."""
    try:
        result = ping()
        return result is not None
    except Exception:
        return False
