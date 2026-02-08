/**
 * Ribbon — WebRTC / mediasoup-client wrapper
 * Manages transports, producers, consumers, E2EE transforms
 */

window.RibbonRTC = (function() {
    'use strict';

    let _socket = null;
    let _device = null;
    let _sendTransport = null;
    let _recvTransport = null;
    let _producers = new Map();  // kind -> producer (audio, video, screen)
    let _consumers = new Map();  // consumerId -> { consumer, peerId, kind }
    let _localStream = null;
    let _screenStream = null;
    let _e2eeWorker = null;
    let _onTrack = null;        // Callback: (peerId, track, kind) => void
    let _onProducerClosed = null;

    async function init(socket, rtpCapabilities, iceServers, callbacks) {
        _socket = socket;
        _onTrack = callbacks.onTrack;
        _onProducerClosed = callbacks.onProducerClosed;

        // Initialize mediasoup device
        _device = new mediasoupClient.Device();
        await _device.load({ routerRtpCapabilities: rtpCapabilities });

        // Initialize E2EE worker
        try {
            _e2eeWorker = new Worker('/static/js/e2ee-worker.js');
            const mediaKey = RibbonCrypto.getMediaKeyRaw();
            if (mediaKey) {
                _e2eeWorker.postMessage({
                    type: 'setKey',
                    keyBytes: mediaKey.buffer,
                });
            }
        } catch (e) {
            console.warn('E2EE worker not available:', e);
        }

        // Create send transport
        _sendTransport = await _createTransport(false);
        // Create recv transport
        _recvTransport = await _createTransport(true);
    }

    async function _createTransport(consuming) {
        return new Promise((resolve, reject) => {
            _socket.emit('createTransport', { consuming }, (err) => {
                if (err) reject(err);
            });

            _socket.once('transportCreated', async (data) => {
                const transport = consuming
                    ? _device.createRecvTransport({
                        id: data.id,
                        iceParameters: data.iceParameters,
                        iceCandidates: data.iceCandidates,
                        dtlsParameters: data.dtlsParameters,
                        sctpParameters: data.sctpParameters,
                    })
                    : _device.createSendTransport({
                        id: data.id,
                        iceParameters: data.iceParameters,
                        iceCandidates: data.iceCandidates,
                        dtlsParameters: data.dtlsParameters,
                        sctpParameters: data.sctpParameters,
                    });

                transport.on('connect', ({ dtlsParameters }, callback, errback) => {
                    _socket.emit('connectTransport', {
                        transportId: transport.id,
                        dtlsParameters,
                    });
                    _socket.once('transportConnected', () => callback());
                });

                if (!consuming) {
                    transport.on('produce', ({ kind, rtpParameters, appData }, callback, errback) => {
                        _socket.emit('produce', {
                            transportId: transport.id,
                            kind,
                            rtpParameters,
                            appData,
                        });
                        _socket.once('produced', ({ id }) => callback({ id }));
                    });
                }

                resolve(transport);
            });
        });
    }

    /**
     * Get local media and start producing audio + video
     */
    async function startLocalMedia(audioEnabled, videoEnabled) {
        const constraints = {
            audio: audioEnabled ? {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            } : false,
            video: videoEnabled ? {
                width: { ideal: 1280 },
                height: { ideal: 720 },
                frameRate: { ideal: 30, max: 30 },
            } : false,
        };

        try {
            _localStream = await navigator.mediaDevices.getUserMedia(constraints);
        } catch (e) {
            console.error('getUserMedia failed:', e);
            // Try audio-only fallback
            if (videoEnabled) {
                _localStream = await navigator.mediaDevices.getUserMedia({
                    audio: constraints.audio,
                    video: false,
                });
            } else {
                throw e;
            }
        }

        return _localStream;
    }

    async function produceAudio() {
        if (!_localStream || !_sendTransport) return null;

        const audioTrack = _localStream.getAudioTracks()[0];
        if (!audioTrack) return null;

        const producer = await _sendTransport.produce({
            track: audioTrack,
            codecOptions: {
                opusStereo: true,
                opusDtx: true,
            },
            appData: { type: 'audio' },
        });

        _applyE2eeToSender(producer);
        _producers.set('audio', producer);
        return producer;
    }

    async function produceVideo() {
        if (!_localStream || !_sendTransport) return null;

        const videoTrack = _localStream.getVideoTracks()[0];
        if (!videoTrack) return null;

        const encodings = [
            { rid: 'r0', maxBitrate: 100000, scaleResolutionDownBy: 4 },
            { rid: 'r1', maxBitrate: 300000, scaleResolutionDownBy: 2 },
            { rid: 'r2', maxBitrate: 900000 },
        ];

        const producer = await _sendTransport.produce({
            track: videoTrack,
            encodings,
            codecOptions: {
                videoGoogleStartBitrate: 1000,
            },
            appData: { type: 'video' },
        });

        _applyE2eeToSender(producer);
        _producers.set('video', producer);
        return producer;
    }

    async function produceScreen() {
        try {
            _screenStream = await navigator.mediaDevices.getDisplayMedia({
                video: {
                    width: { ideal: 1920 },
                    height: { ideal: 1080 },
                    frameRate: { ideal: 15, max: 30 },
                },
                audio: false,
            });
        } catch (e) {
            console.log('Screen share cancelled');
            return null;
        }

        const screenTrack = _screenStream.getVideoTracks()[0];

        const producer = await _sendTransport.produce({
            track: screenTrack,
            encodings: [{ maxBitrate: 2000000 }],
            appData: { type: 'screen' },
        });

        _applyE2eeToSender(producer);

        screenTrack.addEventListener('ended', () => {
            stopScreen();
        });

        _producers.set('screen', producer);
        return producer;
    }

    async function stopScreen() {
        const producer = _producers.get('screen');
        if (producer) {
            producer.close();
            _producers.delete('screen');
            _socket.emit('closeProducer', { producerId: producer.id });
        }
        if (_screenStream) {
            _screenStream.getTracks().forEach(t => t.stop());
            _screenStream = null;
        }
    }

    /**
     * Consume a remote producer
     */
    async function consume(producerId, peerId) {
        return new Promise((resolve, reject) => {
            _socket.emit('consume', {
                producerId,
                rtpCapabilities: _device.rtpCapabilities,
            });

            _socket.once('consumed', async (data) => {
                const consumer = await _recvTransport.consume({
                    id: data.consumerId,
                    producerId: data.producerId,
                    kind: data.kind,
                    rtpParameters: data.rtpParameters,
                });

                _applyE2eeToReceiver(consumer);

                _consumers.set(consumer.id, {
                    consumer,
                    peerId,
                    kind: data.kind,
                });

                // Resume consumer on server
                _socket.emit('resumeConsumer', { consumerId: consumer.id });

                // Notify UI
                if (_onTrack) {
                    _onTrack(peerId, consumer.track, data.kind);
                }

                resolve(consumer);
            });
        });
    }

    /**
     * Apply E2EE encryption to outgoing sender
     */
    function _applyE2eeToSender(producer) {
        if (!_e2eeWorker) return;
        if (typeof RTCRtpScriptTransform === 'undefined') return;

        try {
            const senders = _sendTransport._handler._pc.getSenders();
            const sender = senders.find(s => s.track && s.track.id === producer.track.id);
            if (sender) {
                sender.transform = new RTCRtpScriptTransform(_e2eeWorker, {
                    direction: 'encrypt',
                });
            }
        } catch (e) {
            console.warn('Could not apply E2EE to sender:', e);
        }
    }

    /**
     * Apply E2EE decryption to incoming receiver
     */
    function _applyE2eeToReceiver(consumer) {
        if (!_e2eeWorker) return;
        if (typeof RTCRtpScriptTransform === 'undefined') return;

        try {
            const receivers = _recvTransport._handler._pc.getReceivers();
            const receiver = receivers.find(r => r.track && r.track.id === consumer.track.id);
            if (receiver) {
                receiver.transform = new RTCRtpScriptTransform(_e2eeWorker, {
                    direction: 'decrypt',
                });
            }
        } catch (e) {
            console.warn('Could not apply E2EE to receiver:', e);
        }
    }

    // --- Control methods ---

    function toggleMic() {
        const producer = _producers.get('audio');
        if (!producer) return false;

        if (producer.paused) {
            producer.resume();
            _socket.emit('resumeProducer', { producerId: producer.id });
            return true; // now unmuted
        } else {
            producer.pause();
            _socket.emit('pauseProducer', { producerId: producer.id });
            return false; // now muted
        }
    }

    function toggleCamera() {
        const producer = _producers.get('video');
        if (!producer) return false;

        if (producer.paused) {
            producer.resume();
            _socket.emit('resumeProducer', { producerId: producer.id });
            return true; // now on
        } else {
            producer.pause();
            _socket.emit('pauseProducer', { producerId: producer.id });
            return false; // now off
        }
    }

    function isMicEnabled() {
        const p = _producers.get('audio');
        return p && !p.paused;
    }

    function isCameraEnabled() {
        const p = _producers.get('video');
        return p && !p.paused;
    }

    function isScreenSharing() {
        return _producers.has('screen');
    }

    function getLocalStream() {
        return _localStream;
    }

    /**
     * Get audio levels for active speaker detection
     */
    function createAudioLevelDetector(stream, callback) {
        try {
            const ctx = new AudioContext();
            const source = ctx.createMediaStreamSource(stream);
            const analyser = ctx.createAnalyser();
            analyser.fftSize = 256;
            source.connect(analyser);

            const data = new Uint8Array(analyser.frequencyBinCount);
            const check = () => {
                analyser.getByteFrequencyData(data);
                const avg = data.reduce((a, b) => a + b, 0) / data.length;
                callback(avg);
                requestAnimationFrame(check);
            };
            check();

            return { ctx, stop: () => ctx.close() };
        } catch (e) {
            return null;
        }
    }

    /**
     * Clean up everything
     */
    function close() {
        for (const [, producer] of _producers) {
            producer.close();
        }
        _producers.clear();

        for (const [, { consumer }] of _consumers) {
            consumer.close();
        }
        _consumers.clear();

        if (_sendTransport) _sendTransport.close();
        if (_recvTransport) _recvTransport.close();

        if (_localStream) {
            _localStream.getTracks().forEach(t => t.stop());
        }
        if (_screenStream) {
            _screenStream.getTracks().forEach(t => t.stop());
        }

        if (_e2eeWorker) {
            _e2eeWorker.terminate();
        }
    }

    return {
        init,
        startLocalMedia,
        produceAudio,
        produceVideo,
        produceScreen,
        stopScreen,
        consume,
        toggleMic,
        toggleCamera,
        isMicEnabled,
        isCameraEnabled,
        isScreenSharing,
        getLocalStream,
        createAudioLevelDetector,
        close,
    };
})();
