/**
 * Ribbon — Nearby places module
 * Browser geolocation, category search, place cards, sharing
 */

window.RibbonNearby = (function() {
    'use strict';

    let _socket = null;
    let _roomId = null;
    let _lat = null;
    let _lon = null;
    let _activeCategory = null;

    function init(socket, roomId) {
        _socket = socket;
        _roomId = roomId;

        _requestLocation();
        _setupUI();
        _setupSocketEvents();

        socket.emit('getSharedPlaces', { roomId });
    }

    function _requestLocation() {
        const locText = document.getElementById('nearbyLocText');
        if (!navigator.geolocation) {
            locText.textContent = 'Geolocation not supported';
            return;
        }

        navigator.geolocation.getCurrentPosition(
            (pos) => {
                _lat = pos.coords.latitude;
                _lon = pos.coords.longitude;
                locText.textContent = `${_lat.toFixed(4)}, ${_lon.toFixed(4)}`;
            },
            (err) => {
                locText.textContent = 'Location unavailable';
                console.warn('Geolocation error:', err);
            },
            { enableHighAccuracy: true, timeout: 10000 }
        );
    }

    function _setupUI() {
        // Category buttons
        document.querySelectorAll('.nearby-cat-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const cat = btn.dataset.cat;
                // Toggle active state
                document.querySelectorAll('.nearby-cat-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                _activeCategory = cat;
                _searchCategory(cat);
            });
        });

        // Text search
        const searchInput = document.getElementById('nearbySearchInput');
        const btnSearch = document.getElementById('btnNearbySearch');

        btnSearch.addEventListener('click', () => {
            const query = searchInput.value.trim();
            if (query) _searchText(query);
        });

        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const query = searchInput.value.trim();
                if (query) _searchText(query);
            }
        });
    }

    function _searchCategory(category) {
        if (!_lat || !_lon) {
            RibbonUI.showToast('Location not available yet');
            return;
        }

        const results = document.getElementById('nearbyResults');
        results.innerHTML = '<div class="travel-loading">Searching...</div>';

        fetch('/api/nearby/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                lat: _lat,
                lon: _lon,
                category: category,
                radius: 2000,
            }),
        })
        .then(r => r.json())
        .then(data => _renderResults(data.places || []))
        .catch(() => {
            results.innerHTML = '<div class="empty-state">Search failed</div>';
        });
    }

    function _searchText(query) {
        const results = document.getElementById('nearbyResults');
        results.innerHTML = '<div class="travel-loading">Searching...</div>';

        const params = { query };
        if (_lat && _lon) {
            params.lat = _lat;
            params.lon = _lon;
        }

        fetch('/api/nearby/geocode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
        })
        .then(r => r.json())
        .then(data => _renderResults(data.places || []))
        .catch(() => {
            results.innerHTML = '<div class="empty-state">Search failed</div>';
        });
    }

    function _renderResults(places) {
        const container = document.getElementById('nearbyResults');
        container.innerHTML = '';

        if (places.length === 0) {
            container.innerHTML = '<div class="empty-state">No places found</div>';
            return;
        }

        for (const place of places) {
            const card = document.createElement('div');
            card.className = 'nearby-card';

            const dist = place.distance ? _formatDist(place.distance) : '';
            const mapUrl = `https://www.openstreetmap.org/?mlat=${place.lat}&mlon=${place.lon}#map=17/${place.lat}/${place.lon}`;

            card.innerHTML = `
                <div class="nearby-card-name">${_esc(place.name)}</div>
                <div class="nearby-card-address">${_esc(place.address || '')}</div>
                <div class="nearby-card-meta">
                    <span class="nearby-card-dist">${dist}</span>
                    <div class="nearby-card-actions">
                        <a href="${mapUrl}" target="_blank" rel="noopener">Map</a>
                        <button class="btn-share-place" data-place='${JSON.stringify(place).replace(/'/g, "&#39;")}'>Share</button>
                    </div>
                </div>
            `;

            card.querySelector('.btn-share-place').addEventListener('click', (e) => {
                const p = JSON.parse(e.target.dataset.place);
                _sharePlace(p);
            });

            container.appendChild(card);
        }
    }

    function _sharePlace(place) {
        _socket.emit('sharePlace', {
            roomId: _roomId,
            name: place.name,
            address: place.address || '',
            category: place.category || _activeCategory || '',
            lat: place.lat,
            lon: place.lon,
            osmId: place.osm_id || '',
        });
        RibbonUI.showToast('Place shared to room');
    }

    function _setupSocketEvents() {
        _socket.on('placeShared', (data) => {
            _addSharedPlace(data);
        });

        _socket.on('sharedPlacesList', (data) => {
            const list = document.getElementById('nearbySharedList');
            list.innerHTML = '';
            for (const p of data.places || []) {
                _addSharedPlace(p);
            }
        });
    }

    function _addSharedPlace(place) {
        const list = document.getElementById('nearbySharedList');
        const item = document.createElement('div');
        item.className = 'nearby-shared-item';
        item.innerHTML = `
            <span>${_esc(place.name)}</span>
            <span>by ${_esc(place.shared_by || place.sharedBy)}</span>
        `;
        list.prepend(item);
    }

    function _formatDist(meters) {
        if (meters < 1000) return Math.round(meters) + 'm';
        return (meters / 1000).toFixed(1) + 'km';
    }

    function _esc(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    return { init };
})();
