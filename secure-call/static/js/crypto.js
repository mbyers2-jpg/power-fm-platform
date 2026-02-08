/**
 * Ribbon — Client-side cryptography
 * Key generation, HKDF derivation, AES-GCM for chat + files
 * Room key shared via URL fragment (never sent to server)
 */

window.RibbonCrypto = (function() {
    'use strict';

    const ALGORITHM = 'AES-GCM';
    const KEY_LENGTH = 256;
    const IV_LENGTH = 12;

    // Subkey labels for HKDF derivation
    const LABELS = {
        media: 'ribbon-media-key',
        chat: 'ribbon-chat-key',
        file: 'ribbon-file-key',
    };

    let _roomKeyRaw = null;  // Raw room key bytes (Uint8Array)
    let _chatKey = null;     // Derived CryptoKey for chat
    let _fileKey = null;     // Derived CryptoKey for files
    let _mediaKeyRaw = null; // Derived raw bytes for media encryption

    /**
     * Generate a new room key (256-bit random)
     * Returns base64url-encoded key for URL fragment
     */
    async function generateRoomKey() {
        const keyBytes = crypto.getRandomValues(new Uint8Array(32));
        _roomKeyRaw = keyBytes;
        await _deriveSubkeys();
        return _arrayToBase64url(keyBytes);
    }

    /**
     * Import room key from base64url string (from URL fragment)
     */
    async function importRoomKey(base64urlKey) {
        _roomKeyRaw = _base64urlToArray(base64urlKey);
        await _deriveSubkeys();
    }

    /**
     * Get the room key as base64url for sharing
     */
    function getRoomKeyBase64url() {
        if (!_roomKeyRaw) return null;
        return _arrayToBase64url(_roomKeyRaw);
    }

    /**
     * Get raw media key bytes for e2ee-worker
     */
    function getMediaKeyRaw() {
        return _mediaKeyRaw;
    }

    /**
     * Derive subkeys from room key using HKDF
     */
    async function _deriveSubkeys() {
        const baseKey = await crypto.subtle.importKey(
            'raw', _roomKeyRaw, 'HKDF', false, ['deriveKey', 'deriveBits']
        );

        // Derive chat key
        _chatKey = await crypto.subtle.deriveKey(
            {
                name: 'HKDF',
                hash: 'SHA-256',
                salt: new TextEncoder().encode('ribbon-salt'),
                info: new TextEncoder().encode(LABELS.chat),
            },
            baseKey,
            { name: ALGORITHM, length: KEY_LENGTH },
            false,
            ['encrypt', 'decrypt']
        );

        // Derive file key
        _fileKey = await crypto.subtle.deriveKey(
            {
                name: 'HKDF',
                hash: 'SHA-256',
                salt: new TextEncoder().encode('ribbon-salt'),
                info: new TextEncoder().encode(LABELS.file),
            },
            baseKey,
            { name: ALGORITHM, length: KEY_LENGTH },
            false,
            ['encrypt', 'decrypt']
        );

        // Derive media key (raw bytes for worker)
        const mediaKeyBits = await crypto.subtle.deriveBits(
            {
                name: 'HKDF',
                hash: 'SHA-256',
                salt: new TextEncoder().encode('ribbon-salt'),
                info: new TextEncoder().encode(LABELS.media),
            },
            baseKey,
            KEY_LENGTH
        );
        _mediaKeyRaw = new Uint8Array(mediaKeyBits);
    }

    /**
     * Encrypt a chat message (string -> {ciphertext, iv} as base64)
     */
    async function encryptChat(plaintext) {
        if (!_chatKey) throw new Error('Chat key not initialized');

        const iv = crypto.getRandomValues(new Uint8Array(IV_LENGTH));
        const encoded = new TextEncoder().encode(plaintext);

        const ciphertext = await crypto.subtle.encrypt(
            { name: ALGORITHM, iv },
            _chatKey,
            encoded
        );

        return {
            ciphertext: _arrayToBase64(new Uint8Array(ciphertext)),
            iv: _arrayToBase64(iv),
        };
    }

    /**
     * Decrypt a chat message ({ciphertext, iv} as base64 -> string)
     */
    async function decryptChat(ciphertextB64, ivB64) {
        if (!_chatKey) throw new Error('Chat key not initialized');

        const ciphertext = _base64ToArray(ciphertextB64);
        const iv = _base64ToArray(ivB64);

        const plaintext = await crypto.subtle.decrypt(
            { name: ALGORITHM, iv },
            _chatKey,
            ciphertext
        );

        return new TextDecoder().decode(plaintext);
    }

    /**
     * Encrypt a file (ArrayBuffer -> {encrypted: ArrayBuffer, iv: base64})
     */
    async function encryptFile(fileData) {
        if (!_fileKey) throw new Error('File key not initialized');

        const iv = crypto.getRandomValues(new Uint8Array(IV_LENGTH));

        const encrypted = await crypto.subtle.encrypt(
            { name: ALGORITHM, iv },
            _fileKey,
            fileData
        );

        return {
            encrypted: encrypted,
            iv: _arrayToBase64(iv),
        };
    }

    /**
     * Decrypt a file (ArrayBuffer + iv -> ArrayBuffer)
     */
    async function decryptFile(encryptedData, ivB64) {
        if (!_fileKey) throw new Error('File key not initialized');

        const iv = _base64ToArray(ivB64);

        return await crypto.subtle.decrypt(
            { name: ALGORITHM, iv },
            _fileKey,
            encryptedData
        );
    }

    /**
     * Check if keys are initialized
     */
    function isReady() {
        return _chatKey !== null && _fileKey !== null && _mediaKeyRaw !== null;
    }

    // --- Encoding helpers ---

    function _arrayToBase64(arr) {
        return btoa(String.fromCharCode.apply(null, arr));
    }

    function _base64ToArray(b64) {
        const binary = atob(b64);
        const arr = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            arr[i] = binary.charCodeAt(i);
        }
        return arr;
    }

    function _arrayToBase64url(arr) {
        return _arrayToBase64(arr)
            .replace(/\+/g, '-')
            .replace(/\//g, '_')
            .replace(/=+$/, '');
    }

    function _base64urlToArray(b64url) {
        let b64 = b64url.replace(/-/g, '+').replace(/_/g, '/');
        while (b64.length % 4) b64 += '=';
        return _base64ToArray(b64);
    }

    return {
        generateRoomKey,
        importRoomKey,
        getRoomKeyBase64url,
        getMediaKeyRaw,
        encryptChat,
        decryptChat,
        encryptFile,
        decryptFile,
        isReady,
    };
})();
