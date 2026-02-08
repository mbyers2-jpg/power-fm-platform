/**
 * Ribbon — Travel search module
 * Flights, hotels, car rental links, deal sharing
 */

window.RibbonTravel = (function() {
    'use strict';

    let _socket = null;
    let _roomId = null;

    function init(socket, roomId) {
        _socket = socket;
        _roomId = roomId;

        _setupTabs();
        _setupFlights();
        _setupHotels();
        _setupSocketEvents();
        _setDefaultDates();

        socket.emit('getTravelBookmarks', { roomId });
    }

    function _setupTabs() {
        document.querySelectorAll('.travel-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.travel-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');

                document.getElementById('travelFlights').style.display = 'none';
                document.getElementById('travelHotels').style.display = 'none';
                document.getElementById('travelCars').style.display = 'none';

                const pane = tab.dataset.travel;
                const el = document.getElementById('travel' + pane.charAt(0).toUpperCase() + pane.slice(1));
                if (el) el.style.display = 'block';
            });
        });
    }

    function _setDefaultDates() {
        const today = new Date();
        const tomorrow = new Date(today);
        tomorrow.setDate(tomorrow.getDate() + 1);
        const weekOut = new Date(today);
        weekOut.setDate(weekOut.getDate() + 8);
        const dayAfter = new Date(today);
        dayAfter.setDate(dayAfter.getDate() + 2);

        // Only fill if empty (don't overwrite user input)
        const fd = document.getElementById('flightDepart');
        const fr = document.getElementById('flightReturn');
        const hc = document.getElementById('hotelCheckin');
        const ho = document.getElementById('hotelCheckout');

        if (fd && !fd.value) fd.value = _dateStr(tomorrow);
        if (fr && !fr.value) fr.value = _dateStr(weekOut);
        if (hc && !hc.value) hc.value = _dateStr(tomorrow);
        if (ho && !ho.value) ho.value = _dateStr(dayAfter);
    }

    function show() {
        _setDefaultDates();
        document.getElementById('travelModal').style.display = 'flex';
    }

    function _setupFlights() {
        document.getElementById('btnSearchFlights').addEventListener('click', _searchFlights);
    }

    function _setupHotels() {
        document.getElementById('btnSearchHotels').addEventListener('click', _searchHotels);
    }

    function _searchFlights() {
        const origin = document.getElementById('flightOrigin').value.trim().toUpperCase();
        const dest = document.getElementById('flightDest').value.trim().toUpperCase();
        const depart = document.getElementById('flightDepart').value;
        const ret = document.getElementById('flightReturn').value;
        const pax = parseInt(document.getElementById('flightPax').value) || 1;

        if (!origin || !dest || origin.length !== 3 || dest.length !== 3) {
            RibbonUI.showToast('Enter valid 3-letter IATA codes');
            return;
        }
        if (!depart) {
            RibbonUI.showToast('Select a departure date');
            return;
        }

        const results = document.getElementById('flightResults');
        results.innerHTML = '<div class="travel-loading">Searching flights...</div>';

        fetch('/api/travel/flights', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                roomId: _roomId,
                origin, dest, depart,
                returnDate: ret || null,
                passengers: pax,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                results.innerHTML = `<div class="travel-no-api"><p>${_esc(data.error)}</p></div>`;
                return;
            }
            _renderFlightResults(data.flights || []);
        })
        .catch(() => {
            results.innerHTML = '<div class="empty-state">Search failed</div>';
        });
    }

    function _renderFlightResults(flights) {
        const container = document.getElementById('flightResults');
        container.innerHTML = '';

        if (flights.length === 0) {
            container.innerHTML = '<div class="empty-state">No flights found</div>';
            return;
        }

        for (const f of flights) {
            const card = document.createElement('div');
            card.className = 'travel-result-card';
            card.innerHTML = `
                <div class="travel-result-top">
                    <span class="travel-result-title">${_esc(f.airline)} — ${_esc(f.origin)} to ${_esc(f.dest)}</span>
                    <span class="travel-result-price">${_esc(f.price)}</span>
                </div>
                <div class="travel-result-details">
                    ${_esc(f.departure)} — ${_esc(f.duration)}<br>
                    ${f.stops === 0 ? 'Nonstop' : f.stops + ' stop(s)'}
                </div>
                <div class="travel-result-actions">
                    <button class="btn-share-deal" data-deal='${JSON.stringify(f).replace(/'/g, "&#39;")}' data-type="flight">Share to Room</button>
                </div>
            `;

            card.querySelector('.btn-share-deal').addEventListener('click', (e) => {
                const deal = JSON.parse(e.target.dataset.deal);
                _shareDeal('flight', deal);
            });

            container.appendChild(card);
        }
    }

    function _searchHotels() {
        const city = document.getElementById('hotelCity').value.trim().toUpperCase();
        const checkin = document.getElementById('hotelCheckin').value;
        const checkout = document.getElementById('hotelCheckout').value;
        const guests = parseInt(document.getElementById('hotelGuests').value) || 1;

        if (!city || city.length !== 3) {
            RibbonUI.showToast('Enter a valid 3-letter city code');
            return;
        }

        const results = document.getElementById('hotelResults');
        results.innerHTML = '<div class="travel-loading">Searching hotels...</div>';

        fetch('/api/travel/hotels', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                roomId: _roomId,
                city, checkin, checkout, guests,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                results.innerHTML = `<div class="travel-no-api"><p>${_esc(data.error)}</p></div>`;
                return;
            }
            _renderHotelResults(data.hotels || []);
        })
        .catch(() => {
            results.innerHTML = '<div class="empty-state">Search failed</div>';
        });
    }

    function _renderHotelResults(hotels) {
        const container = document.getElementById('hotelResults');
        container.innerHTML = '';

        if (hotels.length === 0) {
            container.innerHTML = '<div class="empty-state">No hotels found</div>';
            return;
        }

        for (const h of hotels) {
            const card = document.createElement('div');
            card.className = 'travel-result-card';
            card.innerHTML = `
                <div class="travel-result-top">
                    <span class="travel-result-title">${_esc(h.name)}</span>
                    <span class="travel-result-price">${_esc(h.price || 'N/A')}</span>
                </div>
                <div class="travel-result-details">
                    ${h.rating ? h.rating + ' stars' : ''}<br>
                    ${_esc(h.address || '')}
                </div>
                <div class="travel-result-actions">
                    <button class="btn-share-deal" data-deal='${JSON.stringify(h).replace(/'/g, "&#39;")}' data-type="hotel">Share to Room</button>
                </div>
            `;

            card.querySelector('.btn-share-deal').addEventListener('click', (e) => {
                const deal = JSON.parse(e.target.dataset.deal);
                _shareDeal('hotel', deal);
            });

            container.appendChild(card);
        }
    }

    function _shareDeal(type, deal) {
        _socket.emit('shareTravelDeal', {
            roomId: _roomId,
            type: type,
            deal: deal,
        });
        RibbonUI.showToast('Deal shared to room');
    }

    function _setupSocketEvents() {
        _socket.on('travelDealShared', (data) => {
            _addBookmark(data);
        });

        _socket.on('travelBookmarksList', (data) => {
            const list = document.getElementById('travelBookmarksList');
            list.innerHTML = '';
            for (const b of data.bookmarks || []) {
                _addBookmark(b);
            }
        });
    }

    function _addBookmark(bookmark) {
        const list = document.getElementById('travelBookmarksList');
        const item = document.createElement('div');
        item.className = 'travel-bookmark-item';

        const deal = bookmark.deal || {};
        let label = bookmark.type === 'flight'
            ? `${deal.origin || '?'} → ${deal.dest || '?'} ${deal.price || ''}`
            : `${deal.name || 'Hotel'} ${deal.price || ''}`;

        item.innerHTML = `
            <span>${_esc(label)}</span>
            <span>by ${_esc(bookmark.shared_by || bookmark.sharedBy)}</span>
        `;
        list.prepend(item);
    }

    function _dateStr(d) {
        return d.toISOString().split('T')[0];
    }

    function _esc(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    return { init, show };
})();
