/**
 * Ribbon — Encrypted chat
 * Messages encrypted client-side with AES-GCM before sending via Socket.IO
 */

window.RibbonChat = (function() {
    'use strict';

    let _socket = null;
    const messagesEl = document.getElementById('chatMessages');
    const inputEl = document.getElementById('chatInput');
    const sendBtn = document.getElementById('btnSendChat');

    function init(socket) {
        _socket = socket;

        sendBtn.addEventListener('click', sendMessage);
        inputEl.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });

        // Listen for incoming messages
        _socket.on('chatMessage', handleIncoming);
        _socket.on('chatHistory', handleHistory);

        // Request chat history
        _socket.emit('getChatHistory', {});
    }

    async function sendMessage() {
        const text = inputEl.value.trim();
        if (!text) return;

        inputEl.value = '';

        try {
            const encrypted = await RibbonCrypto.encryptChat(text);
            _socket.emit('chatMessage', {
                ciphertext: encrypted.ciphertext,
                iv: encrypted.iv,
            });
        } catch (e) {
            console.error('Chat encrypt error:', e);
            addSystemMessage('Failed to encrypt message');
        }
    }

    async function handleIncoming(data) {
        try {
            const plaintext = await RibbonCrypto.decryptChat(data.ciphertext, data.iv);
            const isMe = data.peerId === window.ROOM_DATA.peerId;

            addMessage(data.sender, plaintext, data.timestamp, isMe);

            if (!isMe) {
                RibbonUI.incrementChatBadge();
            }
        } catch (e) {
            console.error('Chat decrypt error:', e);
            addMessage(data.sender, '[encrypted message]', data.timestamp, false);
        }
    }

    async function handleHistory(messages) {
        for (const msg of messages) {
            try {
                const plaintext = await RibbonCrypto.decryptChat(msg.ciphertext, msg.iv);
                addMessage(msg.sender, plaintext, msg.timestamp, false);
            } catch (e) {
                // Can't decrypt history (different key session) — skip
            }
        }
    }

    function addMessage(sender, text, timestamp, isMe) {
        const el = document.createElement('div');
        el.className = 'chat-msg';

        const time = new Date(timestamp * 1000);
        const timeStr = time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        el.innerHTML = `
            <div class="chat-msg-sender">${_escapeHtml(sender)}</div>
            <div class="chat-msg-text">${_escapeHtml(text)}</div>
            <div class="chat-msg-time">${timeStr}</div>
        `;

        messagesEl.appendChild(el);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function addSystemMessage(text) {
        const el = document.createElement('div');
        el.className = 'chat-msg-system';
        el.textContent = text;
        messagesEl.appendChild(el);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    return {
        init,
        addSystemMessage,
    };
})();
