/**
 * Ribbon — E2EE Worker for media frame encryption/decryption
 * Used via RTCRtpScriptTransform (Insertable Streams)
 *
 * Each frame is encrypted with AES-GCM using the media subkey.
 * IV = 4 bytes of keyId + 8 bytes incrementing counter (12 bytes total).
 */

'use strict';

let encryptionKey = null;
let keyId = new Uint8Array(4); // First 4 bytes of key for IV prefix
let encryptCounter = 0;

// AES-GCM with Web Crypto API
const ALGORITHM = 'AES-GCM';

async function importKey(rawKeyBytes) {
    encryptionKey = await crypto.subtle.importKey(
        'raw',
        rawKeyBytes,
        { name: ALGORITHM, length: 256 },
        false,
        ['encrypt', 'decrypt']
    );
    // Use first 4 bytes of key material as key identifier
    keyId = new Uint8Array(rawKeyBytes.slice(0, 4));
    encryptCounter = 0;
}

function buildIv(counter) {
    const iv = new Uint8Array(12);
    iv.set(keyId, 0); // Bytes 0-3: keyId
    // Bytes 4-11: counter (big-endian)
    const view = new DataView(iv.buffer);
    view.setUint32(4, Math.floor(counter / 0x100000000));
    view.setUint32(8, counter & 0xffffffff);
    return iv;
}

async function encryptFrame(frame, controller) {
    if (!encryptionKey) {
        controller.enqueue(frame);
        return;
    }

    try {
        const data = new Uint8Array(frame.data);

        // For video, keep first bytes unencrypted (codec header)
        // VP8: 1 byte unencrypted, VP9: 1 byte, H264: depends
        // We'll keep first 1 byte unencrypted for any video codec
        const headerSize = frame.type === undefined ? 0 : 1; // Audio: 0, Video: 1

        const header = data.subarray(0, headerSize);
        const payload = data.subarray(headerSize);

        const iv = buildIv(encryptCounter++);

        const encrypted = await crypto.subtle.encrypt(
            { name: ALGORITHM, iv: iv },
            encryptionKey,
            payload
        );

        // Output format: [header][IV (12 bytes)][encrypted payload][tag appended by AES-GCM]
        const encryptedArray = new Uint8Array(encrypted);
        const output = new Uint8Array(header.length + 12 + encryptedArray.length);
        output.set(header, 0);
        output.set(iv, header.length);
        output.set(encryptedArray, header.length + 12);

        frame.data = output.buffer;
        controller.enqueue(frame);
    } catch (e) {
        // On error, pass through unencrypted (fallback)
        controller.enqueue(frame);
    }
}

async function decryptFrame(frame, controller) {
    if (!encryptionKey) {
        controller.enqueue(frame);
        return;
    }

    try {
        const data = new Uint8Array(frame.data);

        const headerSize = frame.type === undefined ? 0 : 1;

        if (data.length < headerSize + 12 + 16) {
            // Too short to be encrypted, pass through
            controller.enqueue(frame);
            return;
        }

        const header = data.subarray(0, headerSize);
        const iv = data.subarray(headerSize, headerSize + 12);
        const encrypted = data.subarray(headerSize + 12);

        const decrypted = await crypto.subtle.decrypt(
            { name: ALGORITHM, iv: iv },
            encryptionKey,
            encrypted
        );

        const decryptedArray = new Uint8Array(decrypted);
        const output = new Uint8Array(header.length + decryptedArray.length);
        output.set(header, 0);
        output.set(decryptedArray, header.length);

        frame.data = output.buffer;
        controller.enqueue(frame);
    } catch (e) {
        // Decryption failed — might be unencrypted frame or key mismatch
        // Silence the frame to avoid noise
    }
}

// Handle messages from main thread
if (typeof self.onrtctransform !== 'undefined' || true) {
    // RTCRtpScriptTransform mode
    self.onrtctransform = (event) => {
        const transformer = event.transformer;
        const direction = transformer.options.direction;

        transformer.readable
            .pipeThrough(new TransformStream({
                transform: direction === 'encrypt' ? encryptFrame : decryptFrame
            }))
            .pipeTo(transformer.writable);
    };
}

// Handle key updates from main thread
self.addEventListener('message', async (event) => {
    const { type, keyBytes } = event.data;
    if (type === 'setKey') {
        await importKey(new Uint8Array(keyBytes));
    }
});
