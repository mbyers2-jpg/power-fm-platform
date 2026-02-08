/**
 * Ribbon SFU — Room management
 * Handles routers, transports, producers, and consumers per room.
 */

const config = require('./config');

class Room {
  constructor(roomId, router) {
    this.roomId = roomId;
    this.router = router;
    this.peers = new Map(); // peerId -> { transports, producers, consumers, displayName }
  }

  getRtpCapabilities() {
    return this.router.rtpCapabilities;
  }

  hasPeer(peerId) {
    return this.peers.has(peerId);
  }

  addPeer(peerId, displayName) {
    if (this.peers.has(peerId)) return;
    this.peers.set(peerId, {
      displayName,
      transports: new Map(),
      producers: new Map(),
      consumers: new Map(),
    });
  }

  removePeer(peerId) {
    const peer = this.peers.get(peerId);
    if (!peer) return [];

    // Collect producer IDs before closing (to notify others)
    const producerIds = Array.from(peer.producers.keys());

    // Close all transports (which closes producers/consumers on them)
    for (const transport of peer.transports.values()) {
      transport.close();
    }

    this.peers.delete(peerId);
    return producerIds;
  }

  getPeerCount() {
    return this.peers.size;
  }

  getPeerList() {
    const list = [];
    for (const [peerId, peer] of this.peers) {
      list.push({
        peerId,
        displayName: peer.displayName,
        producers: Array.from(peer.producers.keys()),
      });
    }
    return list;
  }

  async createWebRtcTransport(peerId) {
    const peer = this.peers.get(peerId);
    if (!peer) throw new Error(`Peer ${peerId} not found`);

    const transport = await this.router.createWebRtcTransport({
      ...config.webRtcTransport,
      appData: { peerId },
    });

    // Set max incoming bitrate
    if (config.webRtcTransport.maxIncomingBitrate) {
      try {
        await transport.setMaxIncomingBitrate(config.webRtcTransport.maxIncomingBitrate);
      } catch (e) {
        // Not critical
      }
    }

    peer.transports.set(transport.id, transport);

    transport.on('dtlsstatechange', (dtlsState) => {
      if (dtlsState === 'closed') {
        transport.close();
      }
    });

    return {
      id: transport.id,
      iceParameters: transport.iceParameters,
      iceCandidates: transport.iceCandidates,
      dtlsParameters: transport.dtlsParameters,
      sctpParameters: transport.sctpParameters,
    };
  }

  async connectTransport(peerId, transportId, dtlsParameters) {
    const peer = this.peers.get(peerId);
    if (!peer) throw new Error(`Peer ${peerId} not found`);

    const transport = peer.transports.get(transportId);
    if (!transport) throw new Error(`Transport ${transportId} not found`);

    await transport.connect({ dtlsParameters });
  }

  async produce(peerId, transportId, kind, rtpParameters, appData = {}) {
    const peer = this.peers.get(peerId);
    if (!peer) throw new Error(`Peer ${peerId} not found`);

    const transport = peer.transports.get(transportId);
    if (!transport) throw new Error(`Transport ${transportId} not found`);

    const producer = await transport.produce({
      kind,
      rtpParameters,
      appData: { ...appData, peerId },
    });

    peer.producers.set(producer.id, producer);

    producer.on('transportclose', () => {
      peer.producers.delete(producer.id);
    });

    return { id: producer.id };
  }

  async consume(peerId, producerId, rtpCapabilities) {
    const peer = this.peers.get(peerId);
    if (!peer) throw new Error(`Peer ${peerId} not found`);

    if (!this.router.canConsume({ producerId, rtpCapabilities })) {
      throw new Error('Cannot consume');
    }

    // Find the send transport for this consumer peer
    let recvTransport = null;
    for (const transport of peer.transports.values()) {
      // Use the transport that isn't producing (the recv transport)
      if (transport.appData.consuming) {
        recvTransport = transport;
        break;
      }
    }

    // Fallback: use any transport
    if (!recvTransport) {
      recvTransport = peer.transports.values().next().value;
    }

    if (!recvTransport) throw new Error('No transport for consuming');

    const consumer = await recvTransport.consume({
      producerId,
      rtpCapabilities,
      paused: true, // Start paused, client resumes after setup
    });

    peer.consumers.set(consumer.id, consumer);

    consumer.on('transportclose', () => {
      peer.consumers.delete(consumer.id);
    });

    consumer.on('producerclose', () => {
      peer.consumers.delete(consumer.id);
    });

    return {
      id: consumer.id,
      producerId: consumer.producerId,
      kind: consumer.kind,
      rtpParameters: consumer.rtpParameters,
      appData: consumer.appData,
    };
  }

  async resumeConsumer(peerId, consumerId) {
    const peer = this.peers.get(peerId);
    if (!peer) throw new Error(`Peer ${peerId} not found`);

    const consumer = peer.consumers.get(consumerId);
    if (!consumer) throw new Error(`Consumer ${consumerId} not found`);

    await consumer.resume();
  }

  async pauseProducer(peerId, producerId) {
    const peer = this.peers.get(peerId);
    if (!peer) throw new Error(`Peer ${peerId} not found`);

    const producer = peer.producers.get(producerId);
    if (!producer) throw new Error(`Producer ${producerId} not found`);

    await producer.pause();
  }

  async resumeProducer(peerId, producerId) {
    const peer = this.peers.get(peerId);
    if (!peer) throw new Error(`Peer ${peerId} not found`);

    const producer = peer.producers.get(producerId);
    if (!producer) throw new Error(`Producer ${producerId} not found`);

    await producer.resume();
  }

  async closeProducer(peerId, producerId) {
    const peer = this.peers.get(peerId);
    if (!peer) throw new Error(`Peer ${peerId} not found`);

    const producer = peer.producers.get(producerId);
    if (!producer) throw new Error(`Producer ${producerId} not found`);

    producer.close();
    peer.producers.delete(producerId);
  }

  getProducersForPeer(peerId) {
    // Return all producers from OTHER peers
    const producers = [];
    for (const [otherPeerId, otherPeer] of this.peers) {
      if (otherPeerId === peerId) continue;
      for (const [producerId, producer] of otherPeer.producers) {
        producers.push({
          producerId,
          peerId: otherPeerId,
          kind: producer.kind,
          appData: producer.appData,
        });
      }
    }
    return producers;
  }

  async setPreferredLayers(peerId, consumerId, spatialLayer, temporalLayer) {
    const peer = this.peers.get(peerId);
    if (!peer) throw new Error(`Peer ${peerId} not found`);

    const consumer = peer.consumers.get(consumerId);
    if (!consumer) throw new Error(`Consumer ${consumerId} not found`);

    await consumer.setPreferredLayers({ spatialLayer, temporalLayer });
  }

  close() {
    for (const peer of this.peers.values()) {
      for (const transport of peer.transports.values()) {
        transport.close();
      }
    }
    this.peers.clear();
    this.router.close();
  }
}

module.exports = Room;
