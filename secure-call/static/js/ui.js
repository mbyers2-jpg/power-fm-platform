/**
 * Ribbon — UI management
 * Video grid layout, active speaker, participant list, timer
 */

window.RibbonUI = (function() {
    'use strict';

    const videoGrid = document.getElementById('videoGrid');
    const participantCount = document.getElementById('participantCount');
    const callTimer = document.getElementById('callTimer');
    const sidePanel = document.getElementById('sidePanel');
    const chatBadge = document.getElementById('chatBadge');
    const peopleList = document.getElementById('peopleList');

    let callStartTime = null;
    let timerInterval = null;
    let _panelVisible = false;
    let _activeTab = 'chat';
    let _participants = new Map(); // peerId -> { displayName, tile, audioLevel }
    let _unreadChat = 0;
    let _activeSpeakerPeerId = null;

    function init() {
        // Tab switching
        document.querySelectorAll('.panel-tab').forEach(tab => {
            tab.addEventListener('click', () => switchTab(tab.dataset.tab));
        });

        // Start call timer
        callStartTime = Date.now();
        timerInterval = setInterval(updateTimer, 1000);
        updateTimer();
    }

    function updateTimer() {
        if (!callStartTime) return;
        const elapsed = Math.floor((Date.now() - callStartTime) / 1000);
        const h = String(Math.floor(elapsed / 3600)).padStart(2, '0');
        const m = String(Math.floor((elapsed % 3600) / 60)).padStart(2, '0');
        const s = String(elapsed % 60).padStart(2, '0');
        callTimer.textContent = `${h}:${m}:${s}`;
    }

    // --- Video grid ---

    function addVideoTile(peerId, displayName, isLocal) {
        if (document.querySelector(`.video-tile[data-peer-id="${peerId}"]`)) {
            return; // Already exists
        }

        const tile = document.createElement('div');
        tile.className = 'video-tile' + (isLocal ? ' local-tile' : '');
        tile.dataset.peerId = peerId;

        const video = document.createElement('video');
        video.autoplay = true;
        video.playsInline = true;
        if (isLocal) video.muted = true;
        video.id = isLocal ? 'localVideo' : `video-${peerId}`;

        const avatar = document.createElement('div');
        avatar.className = 'tile-avatar';
        avatar.textContent = displayName.charAt(0);
        avatar.style.display = 'none';
        avatar.id = `avatar-${peerId}`;

        const label = document.createElement('div');
        label.className = 'tile-label';
        label.innerHTML = `
            <span class="tile-name">${_escapeHtml(displayName)}${isLocal ? ' (You)' : ''}</span>
            <span class="tile-mic-icon" id="mic-${peerId}">&#127908;</span>
        `;

        const ring = document.createElement('div');
        ring.className = 'active-speaker-ring';

        tile.appendChild(video);
        tile.appendChild(avatar);
        tile.appendChild(label);
        tile.appendChild(ring);

        if (isLocal) {
            videoGrid.prepend(tile);
        } else {
            videoGrid.appendChild(tile);
        }

        _participants.set(peerId, { displayName, tile });
        _updateGridLayout();
        _updateParticipantCount();
        _updatePeopleList();

        return video;
    }

    function removeVideoTile(peerId) {
        const tile = document.querySelector(`.video-tile[data-peer-id="${peerId}"]`);
        if (tile) {
            tile.remove();
        }
        _participants.delete(peerId);
        _updateGridLayout();
        _updateParticipantCount();
        _updatePeopleList();
    }

    function getVideoElement(peerId) {
        return document.getElementById(`video-${peerId}`) || document.getElementById('localVideo');
    }

    function setStream(peerId, stream, isLocal) {
        const videoId = isLocal ? 'localVideo' : `video-${peerId}`;
        const video = document.getElementById(videoId);
        if (video) {
            video.srcObject = stream;
        }
    }

    function attachTrack(peerId, track, isLocal) {
        const videoId = isLocal ? 'localVideo' : `video-${peerId}`;
        const video = document.getElementById(videoId);
        if (!video) return;

        let stream = video.srcObject;
        if (!stream) {
            stream = new MediaStream();
            video.srcObject = stream;
        }

        // Remove existing track of same kind
        stream.getTracks().forEach(t => {
            if (t.kind === track.kind) stream.removeTrack(t);
        });
        stream.addTrack(track);
    }

    function showAvatar(peerId, show) {
        const avatar = document.getElementById(`avatar-${peerId}`);
        const videoId = peerId === window.ROOM_DATA.peerId ? 'localVideo' : `video-${peerId}`;
        const video = document.getElementById(videoId);

        if (avatar) avatar.style.display = show ? 'flex' : 'none';
        if (video) video.style.display = show ? 'none' : 'block';
    }

    function setMicMuted(peerId, muted) {
        const icon = document.getElementById(`mic-${peerId}`);
        if (icon) {
            icon.classList.toggle('muted', muted);
            icon.textContent = muted ? '\u{1F507}' : '\u{1F3A4}';
        }
    }

    function _updateGridLayout() {
        const count = videoGrid.children.length;
        videoGrid.setAttribute('data-count', count);
    }

    function _updateParticipantCount() {
        const count = videoGrid.children.length;
        participantCount.textContent = count;
    }

    // --- Active speaker ---

    function setActiveSpeaker(peerId) {
        if (_activeSpeakerPeerId === peerId) return;

        // Remove from previous
        if (_activeSpeakerPeerId) {
            const prev = document.querySelector(`.video-tile[data-peer-id="${_activeSpeakerPeerId}"]`);
            if (prev) prev.classList.remove('speaking');
        }

        _activeSpeakerPeerId = peerId;

        if (peerId) {
            const tile = document.querySelector(`.video-tile[data-peer-id="${peerId}"]`);
            if (tile) tile.classList.add('speaking');
        }
    }

    // --- Side panel ---

    function togglePanel() {
        _panelVisible = !_panelVisible;
        sidePanel.style.display = _panelVisible ? 'flex' : 'none';
        if (_panelVisible && _activeTab === 'chat') {
            _unreadChat = 0;
            chatBadge.style.display = 'none';
        }
    }

    function showPanel() {
        _panelVisible = true;
        sidePanel.style.display = 'flex';
        if (_activeTab === 'chat') {
            _unreadChat = 0;
            chatBadge.style.display = 'none';
        }
    }

    function switchTab(tab) {
        _activeTab = tab;
        document.querySelectorAll('.panel-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.tab === tab);
        });
        // Generic: hide all panel-content, show the matching one
        document.querySelectorAll('.side-panel > .panel-content').forEach(p => {
            p.style.display = 'none';
        });
        const active = document.getElementById(tab + 'Panel');
        if (active) active.style.display = 'flex';

        if (tab === 'chat') {
            _unreadChat = 0;
            chatBadge.style.display = 'none';
        }
    }

    function incrementChatBadge() {
        if (_panelVisible && _activeTab === 'chat') return;
        _unreadChat++;
        chatBadge.textContent = _unreadChat;
        chatBadge.style.display = 'inline';
    }

    // --- People list ---

    function _updatePeopleList() {
        if (!peopleList) return;
        peopleList.innerHTML = '';

        for (const [peerId, p] of _participants) {
            const item = document.createElement('div');
            item.className = 'person-item';

            const avatar = document.createElement('div');
            avatar.className = 'person-avatar';
            avatar.textContent = p.displayName.charAt(0);

            const name = document.createElement('span');
            name.className = 'person-name';
            name.textContent = p.displayName;

            item.appendChild(avatar);
            item.appendChild(name);

            if (peerId === window.ROOM_DATA.peerId) {
                const you = document.createElement('span');
                you.className = 'person-host';
                you.textContent = '(You)';
                item.appendChild(you);
            }

            peopleList.appendChild(item);
        }
    }

    // --- Screen share ---

    function showScreenShare(peerId, displayName) {
        const view = document.getElementById('screenShareView');
        const label = document.getElementById('screenShareLabel');
        view.style.display = 'block';
        label.textContent = `${displayName} is sharing their screen`;
    }

    function hideScreenShare() {
        document.getElementById('screenShareView').style.display = 'none';
    }

    function getScreenShareVideo() {
        return document.getElementById('screenShareVideo');
    }

    // --- Settings modal ---

    function showSettings() {
        document.getElementById('settingsModal').style.display = 'flex';
    }

    function hideSettings() {
        document.getElementById('settingsModal').style.display = 'none';
    }

    // --- Utilities ---

    function _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function showToast(message) {
        // Simple toast notification
        const toast = document.createElement('div');
        toast.style.cssText = `
            position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%);
            background: var(--surface2); color: var(--text); padding: 10px 20px;
            border-radius: 8px; border: 1px solid var(--border); font-size: 14px;
            z-index: 200; animation: fadeIn 0.2s;
        `;
        toast.textContent = message;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }

    return {
        init,
        addVideoTile,
        removeVideoTile,
        getVideoElement,
        setStream,
        attachTrack,
        showAvatar,
        setMicMuted,
        setActiveSpeaker,
        togglePanel,
        showPanel,
        switchTab,
        incrementChatBadge,
        showScreenShare,
        hideScreenShare,
        getScreenShareVideo,
        showSettings,
        hideSettings,
        showToast,
    };
})();
