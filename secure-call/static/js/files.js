/**
 * Ribbon — Encrypted file sharing
 * Files encrypted client-side with AES-GCM before upload
 */

window.RibbonFiles = (function() {
    'use strict';

    let _socket = null;
    let _roomId = null;
    const filesListEl = document.getElementById('filesList');
    const fileInput = document.getElementById('fileInput');
    const uploadBtn = document.getElementById('btnUploadFile');

    function init(socket, roomId) {
        _socket = socket;
        _roomId = roomId;

        uploadBtn.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', handleFileSelect);

        _socket.on('fileShared', handleFileShared);

        // Load existing files
        loadFiles();
    }

    async function loadFiles() {
        try {
            const resp = await fetch(`/api/files/${_roomId}`);
            const files = await resp.json();
            files.forEach(f => addFileItem(f));
        } catch (e) {
            console.error('Load files error:', e);
        }
    }

    async function handleFileSelect() {
        const file = fileInput.files[0];
        if (!file) return;

        fileInput.value = '';

        RibbonUI.showToast(`Encrypting ${file.name}...`);

        try {
            const arrayBuffer = await file.arrayBuffer();
            const encrypted = await RibbonCrypto.encryptFile(arrayBuffer);

            // Upload encrypted data
            const formData = new FormData();
            formData.append('file', new Blob([encrypted.encrypted]), 'encrypted.bin');
            formData.append('sender', window.ROOM_DATA.displayName);
            formData.append('original_filename', file.name);
            formData.append('encryption_iv', encrypted.iv);

            const resp = await fetch(`/api/upload/${_roomId}`, {
                method: 'POST',
                body: formData,
            });

            const result = await resp.json();
            if (result.ok) {
                // Notify other participants
                _socket.emit('fileShared', {
                    filename: file.name,
                    size: file.size,
                    iv: encrypted.iv,
                });
                RibbonUI.showToast(`${file.name} shared`);
            }
        } catch (e) {
            console.error('File upload error:', e);
            RibbonUI.showToast('File upload failed');
        }
    }

    function handleFileShared(data) {
        addFileItem({
            sender: data.sender,
            filename: data.filename,
            size: data.size,
            iv: data.iv,
            id: data.fileId,
        });
        RibbonUI.showToast(`${data.sender} shared ${data.filename}`);
    }

    function addFileItem(file) {
        const el = document.createElement('div');
        el.className = 'file-item';

        const ext = (file.filename || '').split('.').pop().toLowerCase();
        const icon = _getFileIcon(ext);
        const sizeStr = _formatSize(file.size);

        el.innerHTML = `
            <span class="file-icon">${icon}</span>
            <div class="file-info">
                <div class="file-name" title="${_escapeHtml(file.filename)}">${_escapeHtml(file.filename)}</div>
                <div class="file-meta">${_escapeHtml(file.sender)} &middot; ${sizeStr}</div>
            </div>
            <button class="file-download" data-file-id="${file.id}" data-iv="${file.iv}" data-filename="${_escapeHtml(file.filename)}">Download</button>
        `;

        el.querySelector('.file-download').addEventListener('click', downloadFile);
        filesListEl.appendChild(el);
    }

    async function downloadFile(e) {
        const btn = e.target;
        const fileId = btn.dataset.fileId;
        const iv = btn.dataset.iv;
        const filename = btn.dataset.filename;

        if (!fileId) return;

        btn.textContent = 'Decrypting...';

        try {
            const resp = await fetch(`/api/download/${fileId}`);
            const encryptedData = await resp.arrayBuffer();

            const decrypted = await RibbonCrypto.decryptFile(encryptedData, iv);

            // Trigger download
            const blob = new Blob([decrypted]);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            btn.textContent = 'Download';
        } catch (e) {
            console.error('File download error:', e);
            btn.textContent = 'Failed';
            setTimeout(() => { btn.textContent = 'Download'; }, 2000);
        }
    }

    function _getFileIcon(ext) {
        const icons = {
            wav: '\u{1F3B5}', mp3: '\u{1F3B5}', flac: '\u{1F3B5}', aac: '\u{1F3B5}',
            mp4: '\u{1F3AC}', mov: '\u{1F3AC}', avi: '\u{1F3AC}',
            jpg: '\u{1F5BC}', jpeg: '\u{1F5BC}', png: '\u{1F5BC}', gif: '\u{1F5BC}',
            pdf: '\u{1F4C4}', doc: '\u{1F4C4}', docx: '\u{1F4C4}',
            zip: '\u{1F4E6}', rar: '\u{1F4E6}',
        };
        return icons[ext] || '\u{1F4CE}';
    }

    function _formatSize(bytes) {
        if (!bytes) return '';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1048576).toFixed(1) + ' MB';
    }

    function _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    return { init };
})();
