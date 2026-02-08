/**
 * Ribbon SFU — mediasoup server
 * Communicates with Flask via JSON-RPC over Unix socket.
 */

const mediasoup = require('mediasoup');
const net = require('net');
const fs = require('fs');
const config = require('./config');
const Room = require('./room');

// State
const workers = [];
let nextWorkerIdx = 0;
const rooms = new Map(); // roomId -> Room

// --- Worker management ---

async function createWorkers() {
  for (let i = 0; i < config.numWorkers; i++) {
    const worker = await mediasoup.createWorker({
      rtcMinPort: config.worker.rtcMinPort,
      rtcMaxPort: config.worker.rtcMaxPort,
      logLevel: config.worker.logLevel,
      logTags: config.worker.logTags,
    });

    worker.on('died', () => {
      console.error(`mediasoup worker ${worker.pid} died, exiting...`);
      process.exit(1);
    });

    workers.push(worker);
    console.log(`mediasoup worker ${worker.pid} created`);
  }
}

function getNextWorker() {
  const worker = workers[nextWorkerIdx];
  nextWorkerIdx = (nextWorkerIdx + 1) % workers.length;
  return worker;
}

// --- Room management ---

async function getOrCreateRoom(roomId) {
  if (rooms.has(roomId)) {
    return rooms.get(roomId);
  }

  const worker = getNextWorker();
  const router = await worker.createRouter({
    mediaCodecs: config.mediaCodecs,
  });

  const room = new Room(roomId, router);
  rooms.set(roomId, room);
  console.log(`Room ${roomId} created on worker ${worker.pid}`);
  return room;
}

function deleteRoom(roomId) {
  const room = rooms.get(roomId);
  if (room) {
    room.close();
    rooms.delete(roomId);
    console.log(`Room ${roomId} deleted`);
  }
}

// --- JSON-RPC handler ---

async function handleRpcRequest(method, params) {
  switch (method) {
    case 'getRouterRtpCapabilities': {
      const room = await getOrCreateRoom(params.roomId);
      return room.getRtpCapabilities();
    }

    case 'join': {
      const room = await getOrCreateRoom(params.roomId);
      room.addPeer(params.peerId, params.displayName);
      return { peers: room.getPeerList() };
    }

    case 'leave': {
      const room = rooms.get(params.roomId);
      if (!room) return { closedProducers: [] };
      const closedProducers = room.removePeer(params.peerId);
      if (room.getPeerCount() === 0) {
        deleteRoom(params.roomId);
      }
      return { closedProducers };
    }

    case 'createWebRtcTransport': {
      const room = rooms.get(params.roomId);
      if (!room) throw new Error('Room not found');
      const transportData = await room.createWebRtcTransport(params.peerId);
      // Mark consuming transports
      if (params.consuming) {
        const peer = room.peers.get(params.peerId);
        if (peer) {
          const transport = peer.transports.get(transportData.id);
          if (transport) transport.appData.consuming = true;
        }
      }
      return transportData;
    }

    case 'connectTransport': {
      const room = rooms.get(params.roomId);
      if (!room) throw new Error('Room not found');
      await room.connectTransport(params.peerId, params.transportId, params.dtlsParameters);
      return {};
    }

    case 'produce': {
      const room = rooms.get(params.roomId);
      if (!room) throw new Error('Room not found');
      return await room.produce(
        params.peerId, params.transportId, params.kind,
        params.rtpParameters, params.appData || {}
      );
    }

    case 'consume': {
      const room = rooms.get(params.roomId);
      if (!room) throw new Error('Room not found');
      return await room.consume(params.peerId, params.producerId, params.rtpCapabilities);
    }

    case 'resumeConsumer': {
      const room = rooms.get(params.roomId);
      if (!room) throw new Error('Room not found');
      await room.resumeConsumer(params.peerId, params.consumerId);
      return {};
    }

    case 'pauseProducer': {
      const room = rooms.get(params.roomId);
      if (!room) throw new Error('Room not found');
      await room.pauseProducer(params.peerId, params.producerId);
      return {};
    }

    case 'resumeProducer': {
      const room = rooms.get(params.roomId);
      if (!room) throw new Error('Room not found');
      await room.resumeProducer(params.peerId, params.producerId);
      return {};
    }

    case 'closeProducer': {
      const room = rooms.get(params.roomId);
      if (!room) throw new Error('Room not found');
      await room.closeProducer(params.peerId, params.producerId);
      return {};
    }

    case 'getProducers': {
      const room = rooms.get(params.roomId);
      if (!room) return { producers: [] };
      return { producers: room.getProducersForPeer(params.peerId) };
    }

    case 'setPreferredLayers': {
      const room = rooms.get(params.roomId);
      if (!room) throw new Error('Room not found');
      await room.setPreferredLayers(
        params.peerId, params.consumerId,
        params.spatialLayer, params.temporalLayer
      );
      return {};
    }

    case 'getRoomStats': {
      const room = rooms.get(params.roomId);
      if (!room) return null;
      return {
        peerCount: room.getPeerCount(),
        peers: room.getPeerList(),
      };
    }

    case 'ping':
      return { pong: true, rooms: rooms.size, workers: workers.length };

    default:
      throw new Error(`Unknown method: ${method}`);
  }
}

// --- Unix socket server ---

function startSocketServer() {
  // Remove stale socket file
  if (fs.existsSync(config.socketPath)) {
    fs.unlinkSync(config.socketPath);
  }

  const server = net.createServer((socket) => {
    let buffer = '';

    socket.on('data', (data) => {
      buffer += data.toString();
      // Process complete JSON messages (delimited by newlines)
      const lines = buffer.split('\n');
      buffer = lines.pop(); // Keep incomplete line in buffer

      for (const line of lines) {
        if (!line.trim()) continue;
        processMessage(socket, line.trim());
      }
    });

    socket.on('error', (err) => {
      if (err.code !== 'EPIPE' && err.code !== 'ECONNRESET') {
        console.error('Socket error:', err.message);
      }
    });
  });

  server.listen(config.socketPath, () => {
    // Make socket accessible
    fs.chmodSync(config.socketPath, 0o666);
    console.log(`SFU listening on ${config.socketPath}`);
  });

  return server;
}

async function processMessage(socket, message) {
  let request;
  try {
    request = JSON.parse(message);
  } catch (e) {
    sendResponse(socket, null, { code: -32700, message: 'Parse error' });
    return;
  }

  try {
    const result = await handleRpcRequest(request.method, request.params || {});
    sendResponse(socket, request.id, null, result);
  } catch (err) {
    console.error(`RPC error [${request.method}]:`, err.message);
    sendResponse(socket, request.id, { code: -32000, message: err.message });
  }
}

function sendResponse(socket, id, error, result) {
  const response = { jsonrpc: '2.0', id };
  if (error) {
    response.error = error;
  } else {
    response.result = result;
  }
  try {
    socket.write(JSON.stringify(response) + '\n');
  } catch (e) {
    // Socket already closed
  }
}

// --- Graceful shutdown ---

function shutdown() {
  console.log('Shutting down SFU...');
  for (const room of rooms.values()) {
    room.close();
  }
  for (const worker of workers) {
    worker.close();
  }
  // Remove socket file
  if (fs.existsSync(config.socketPath)) {
    fs.unlinkSync(config.socketPath);
  }
  process.exit(0);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

// --- Main ---

async function main() {
  console.log('Starting Ribbon SFU...');
  await createWorkers();
  startSocketServer();
  console.log('Ribbon SFU ready');
}

main().catch((err) => {
  console.error('SFU startup failed:', err);
  process.exit(1);
});
