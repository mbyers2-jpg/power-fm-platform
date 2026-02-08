/**
 * Ribbon — Main application controller
 * Orchestrates room joining, media, chat, files, and UI
 */

(async function() {
    'use strict';

    const { roomId, roomName, peerId, displayName, inviteToken, isHost, sfuRunning } = window.ROOM_DATA;

    // --- Initialize crypto from URL fragment ---
    const fragment = window.location.hash.slice(1);
    const params = new URLSearchParams(fragment);
    let roomKeyB64 = params.get('key');

    if (roomKeyB64) {
        await RibbonCrypto.importRoomKey(roomKeyB64);
    } else {
        // First person — generate key and update URL
        roomKeyB64 = await RibbonCrypto.generateRoomKey();
        const newHash = `key=${roomKeyB64}`;
        history.replaceState(null, '', `#${newHash}`);
    }

    // --- Socket.IO connection ---
    const socket = io({
        transports: ['websocket', 'polling'],
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionAttempts: 10,
    });

    // --- Initialize UI ---
    RibbonUI.init();

    // Add local tile
    RibbonUI.addVideoTile(peerId, displayName, true);

    // --- Join room ---
    socket.on('connect', () => {
        socket.emit('joinRoom', { roomId, peerId, displayName });
    });

    socket.on('roomJoined', async (data) => {
        RibbonChat.addSystemMessage(`Joined "${roomName}" — End-to-end encrypted`);
        RibbonChat.addSystemMessage('Share the invite link to add others');

        // Initialize WebRTC
        try {
            await RibbonRTC.init(socket, data.rtpCapabilities, data.iceServers, {
                onTrack: handleRemoteTrack,
                onProducerClosed: handleProducerClosed,
            });

            // Get local media and start producing
            const localStream = await RibbonRTC.startLocalMedia(true, true);
            RibbonUI.setStream(peerId, localStream, true);

            await RibbonRTC.produceAudio();
            await RibbonRTC.produceVideo();

            // Setup audio level detection for active speaker
            RibbonRTC.createAudioLevelDetector(localStream, (level) => {
                if (level > 15) {
                    RibbonUI.setActiveSpeaker(peerId);
                }
            });

            // Consume existing producers
            for (const peer of data.peers || []) {
                if (peer.peerId === peerId) continue;
                for (const producerId of peer.producers || []) {
                    await RibbonRTC.consume(producerId, peer.peerId);
                }
            }
        } catch (e) {
            console.error('Media setup error:', e);
            RibbonChat.addSystemMessage('Camera/mic unavailable — joined audio-only');
            RibbonUI.showAvatar(peerId, true);

            // Try audio-only
            try {
                const localStream = await RibbonRTC.startLocalMedia(true, false);
                await RibbonRTC.produceAudio();
            } catch (e2) {
                RibbonChat.addSystemMessage('No media devices available');
            }
        }

        // Initialize chat, files, payments, nearby, travel
        RibbonChat.init(socket);
        RibbonFiles.init(socket, roomId);
        RibbonPayments.init(socket, roomId);
        RibbonNearby.init(socket, roomId);
        RibbonTravel.init(socket, roomId);

        // Request existing producers
        socket.emit('getProducers', {});
    });

    // --- Remote peer events ---

    socket.on('peerJoined', (data) => {
        RibbonUI.addVideoTile(data.peerId, data.displayName, false);
        RibbonUI.showAvatar(data.peerId, true); // Show avatar until video arrives
        RibbonChat.addSystemMessage(`${data.displayName} joined`);
    });

    socket.on('peerLeft', (data) => {
        RibbonUI.removeVideoTile(data.peerId);
        RibbonChat.addSystemMessage(`${data.displayName} left`);
    });

    socket.on('newProducer', async (data) => {
        // Ensure tile exists
        RibbonUI.addVideoTile(data.peerId, data.peerId, false);
        await RibbonRTC.consume(data.producerId, data.peerId);
    });

    socket.on('producerClosed', (data) => {
        handleProducerClosed(data.peerId, data.producerId);
    });

    socket.on('producerPaused', (data) => {
        // Could be audio or video pause
        RibbonUI.setMicMuted(data.peerId, true);
    });

    socket.on('producerResumed', (data) => {
        RibbonUI.setMicMuted(data.peerId, false);
    });

    socket.on('producers', async (data) => {
        for (const p of data.producers || []) {
            await RibbonRTC.consume(p.producerId, p.peerId);
        }
    });

    // --- Approval flow (private rooms) ---

    socket.on('waitingApproval', () => {
        RibbonChat.addSystemMessage('Waiting for host approval...');
    });

    socket.on('approvalRequest', (data) => {
        if (isHost) {
            RibbonUI.showToast(`${data.displayName} wants to join`);
        }
    });

    socket.on('error', (data) => {
        RibbonChat.addSystemMessage(`Error: ${data.message}`);
    });

    // --- Handle remote tracks ---

    function handleRemoteTrack(remotePeerId, track, kind) {
        if (kind === 'video') {
            // Check if screen share
            RibbonUI.attachTrack(remotePeerId, track, false);
            RibbonUI.showAvatar(remotePeerId, false);
        } else if (kind === 'audio') {
            RibbonUI.attachTrack(remotePeerId, track, false);
        }
    }

    function handleProducerClosed(remotePeerId, producerId) {
        // Producer was closed, might need to show avatar again
    }

    // --- Control buttons ---

    const btnMic = document.getElementById('btnMic');
    const btnCam = document.getElementById('btnCam');
    const btnScreen = document.getElementById('btnScreen');
    const btnChat = document.getElementById('btnChat');
    const btnTravel = document.getElementById('btnTravel');
    const btnSettings = document.getElementById('btnSettings');
    const btnLeave = document.getElementById('btnLeave');
    const btnCopyInvite = document.getElementById('btnCopyInvite');
    const btnCloseSettings = document.getElementById('btnCloseSettings');
    const btnCloseTravel = document.getElementById('btnCloseTravel');

    btnMic.addEventListener('click', () => {
        const isOn = RibbonRTC.toggleMic();
        btnMic.classList.toggle('muted', !isOn);
        RibbonUI.setMicMuted(peerId, !isOn);
    });

    btnCam.addEventListener('click', () => {
        const isOn = RibbonRTC.toggleCamera();
        btnCam.classList.toggle('muted', !isOn);
        RibbonUI.showAvatar(peerId, !isOn);
    });

    btnScreen.addEventListener('click', async () => {
        if (RibbonRTC.isScreenSharing()) {
            await RibbonRTC.stopScreen();
            btnScreen.classList.remove('active');
            RibbonUI.hideScreenShare();
        } else {
            const producer = await RibbonRTC.produceScreen();
            if (producer) {
                btnScreen.classList.add('active');
            }
        }
    });

    btnChat.addEventListener('click', () => {
        RibbonUI.togglePanel();
    });

    btnTravel.addEventListener('click', () => {
        RibbonTravel.show();
    });

    btnSettings.addEventListener('click', () => {
        RibbonUI.showSettings();
        populateDeviceSelectors();
    });

    btnCloseSettings.addEventListener('click', () => {
        RibbonUI.hideSettings();
    });

    btnCloseTravel.addEventListener('click', () => {
        document.getElementById('travelModal').style.display = 'none';
    });

    btnLeave.addEventListener('click', () => {
        if (confirm('Leave this call?')) {
            RibbonRTC.close();
            socket.disconnect();
            window.location.href = '/';
        }
    });

    btnCopyInvite.addEventListener('click', () => {
        const key = RibbonCrypto.getRoomKeyBase64url();
        const baseUrl = window.location.origin;
        let inviteUrl;

        if (inviteToken) {
            inviteUrl = `${baseUrl}/invite/${inviteToken}#key=${key}`;
        } else {
            inviteUrl = `${baseUrl}/join/${roomId}#key=${key}`;
        }

        navigator.clipboard.writeText(inviteUrl).then(() => {
            RibbonUI.showToast('Invite link copied! Key is embedded in link.');
        }).catch(() => {
            // Fallback
            prompt('Copy invite link:', inviteUrl);
        });
    });

    // --- Device selection ---

    async function populateDeviceSelectors() {
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            const audioInput = document.getElementById('audioInput');
            const videoInput = document.getElementById('videoInput');
            const audioOutput = document.getElementById('audioOutput');

            audioInput.innerHTML = '';
            videoInput.innerHTML = '';
            audioOutput.innerHTML = '';

            devices.forEach(device => {
                const option = document.createElement('option');
                option.value = device.deviceId;
                option.textContent = device.label || `${device.kind} (${device.deviceId.slice(0, 8)})`;

                if (device.kind === 'audioinput') audioInput.appendChild(option);
                else if (device.kind === 'videoinput') videoInput.appendChild(option);
                else if (device.kind === 'audiooutput') audioOutput.appendChild(option);
            });
        } catch (e) {
            console.error('Enumerate devices error:', e);
        }
    }

    // --- Keyboard shortcuts ---

    document.addEventListener('keydown', (e) => {
        // Don't capture when typing in chat
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

        if (e.key === 'm' || e.key === 'M') {
            btnMic.click();
        } else if (e.key === 'v' || e.key === 'V') {
            btnCam.click();
        } else if (e.key === 'c' || e.key === 'C') {
            btnChat.click();
        } else if (e.key === 'Escape') {
            RibbonUI.hideSettings();
            document.getElementById('travelModal').style.display = 'none';
        }
    });

    // --- Window unload ---

    window.addEventListener('beforeunload', () => {
        RibbonRTC.close();
        socket.disconnect();
    });

})();
