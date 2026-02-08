/**
 * Ribbon — Payments module
 * Expense tracking, splitting, balances, payment deep links
 */

window.RibbonPayments = (function() {
    'use strict';

    let _socket = null;
    let _roomId = null;
    let _displayName = '';
    let _dropdownOpen = null;

    function init(socket, roomId) {
        _socket = socket;
        _roomId = roomId;
        _displayName = window.ROOM_DATA.displayName;

        _setupForm();
        _setupSocketEvents();

        // Load existing data
        socket.emit('getExpenses', { roomId });
        socket.emit('getBalances', { roomId });

        // Close dropdowns on outside click
        document.addEventListener('click', (e) => {
            if (_dropdownOpen && !e.target.closest('.btn-paylink')) {
                _dropdownOpen.remove();
                _dropdownOpen = null;
            }
        });
    }

    function _setupForm() {
        const btnAdd = document.getElementById('btnAddExpense');
        const btnSubmit = document.getElementById('btnSubmitExpense');
        const btnCancel = document.getElementById('btnCancelExpense');
        const payForm = document.getElementById('payForm');
        const splitType = document.getElementById('expenseSplitType');

        btnAdd.addEventListener('click', () => {
            payForm.style.display = payForm.style.display === 'none' ? 'block' : 'none';
            _populatePaidBy();
        });

        btnCancel.addEventListener('click', () => {
            payForm.style.display = 'none';
            _clearForm();
        });

        btnSubmit.addEventListener('click', _submitExpense);

        splitType.addEventListener('change', () => {
            const custom = document.getElementById('paySplitCustom');
            if (splitType.value === 'custom') {
                custom.style.display = 'block';
                _buildCustomSplitFields();
            } else {
                custom.style.display = 'none';
            }
        });
    }

    function _populatePaidBy() {
        const select = document.getElementById('expensePaidBy');
        select.innerHTML = '';
        // Use current display name as default
        const opt = document.createElement('option');
        opt.value = _displayName;
        opt.textContent = _displayName + ' (You)';
        select.appendChild(opt);
    }

    function _buildCustomSplitFields() {
        const container = document.getElementById('paySplitCustom');
        container.innerHTML = '';
        // For now, show a single text field for comma-separated "Name:Amount"
        const label = document.createElement('label');
        label.textContent = 'Enter splits (Name:Amount, ...)';
        label.className = 'form-group';
        const input = document.createElement('input');
        input.type = 'text';
        input.id = 'customSplitInput';
        input.placeholder = 'e.g. Marc:25, Glenn:15, Firefly:10';
        input.style.width = '100%';
        input.style.padding = '8px 12px';
        input.style.background = 'var(--bg)';
        input.style.border = '1px solid var(--border)';
        input.style.borderRadius = 'var(--radius)';
        input.style.color = 'var(--text)';
        input.style.fontSize = '13px';
        label.appendChild(input);
        container.appendChild(label);
    }

    function _clearForm() {
        document.getElementById('expenseDesc').value = '';
        document.getElementById('expenseAmount').value = '';
        document.getElementById('expenseSplitType').value = 'equal';
        document.getElementById('paySplitCustom').style.display = 'none';
    }

    function _submitExpense() {
        const desc = document.getElementById('expenseDesc').value.trim();
        const amount = parseFloat(document.getElementById('expenseAmount').value);
        const paidBy = document.getElementById('expensePaidBy').value;
        const splitType = document.getElementById('expenseSplitType').value;

        if (!desc || !amount || amount <= 0) {
            RibbonUI.showToast('Enter a description and valid amount');
            return;
        }

        let splits = [];
        if (splitType === 'custom') {
            const raw = document.getElementById('customSplitInput').value.trim();
            if (!raw) {
                RibbonUI.showToast('Enter custom split amounts');
                return;
            }
            const parts = raw.split(',').map(s => s.trim());
            for (const part of parts) {
                const [name, amt] = part.split(':').map(s => s.trim());
                if (name && amt && !isNaN(parseFloat(amt))) {
                    splits.push({ name, amount: parseFloat(amt) });
                }
            }
            if (splits.length === 0) {
                RibbonUI.showToast('Invalid split format');
                return;
            }
        }

        _socket.emit('addExpense', {
            roomId: _roomId,
            description: desc,
            amount: amount,
            paidBy: paidBy,
            splitType: splitType,
            splits: splits,
        });

        document.getElementById('payForm').style.display = 'none';
        _clearForm();
    }

    function _setupSocketEvents() {
        _socket.on('expenseAdded', (data) => {
            _renderExpense(data);
            _socket.emit('getBalances', { roomId: _roomId });
        });

        _socket.on('expensesList', (data) => {
            const container = document.getElementById('payExpenses');
            container.innerHTML = '';
            for (const expense of data.expenses || []) {
                _renderExpense(expense);
            }
        });

        _socket.on('splitSettled', (data) => {
            // Refresh
            _socket.emit('getExpenses', { roomId: _roomId });
            _socket.emit('getBalances', { roomId: _roomId });
        });

        _socket.on('balances', (data) => {
            _renderBalances(data.balances || {});
        });
    }

    function _renderExpense(expense) {
        const container = document.getElementById('payExpenses');
        // Remove existing card for this expense (if re-rendering)
        const existing = document.getElementById('expense-' + expense.id);
        if (existing) existing.remove();

        const card = document.createElement('div');
        card.className = 'pay-expense-card';
        card.id = 'expense-' + expense.id;

        const currency = expense.currency || 'USD';
        const symbol = currency === 'USD' ? '$' : currency;

        let html = `
            <div class="pay-expense-top">
                <span class="pay-expense-desc">${_esc(expense.description)}</span>
                <span class="pay-expense-amount">${symbol}${expense.amount.toFixed(2)}</span>
            </div>
            <div class="pay-expense-payer">Paid by ${_esc(expense.paid_by)}</div>
        `;

        for (const split of expense.splits || []) {
            const settledClass = split.settled ? ' settled' : '';
            html += `<div class="pay-split-item${settledClass}">
                <span>${_esc(split.name)}: ${symbol}${split.amount.toFixed(2)}</span>`;

            if (!split.settled && split.name !== expense.paid_by) {
                html += `<div class="pay-split-actions">
                    <button class="btn-settle" data-split-id="${split.id}">Settle</button>
                    <button class="btn-paylink" data-name="${_esc(split.name)}" data-amount="${split.amount.toFixed(2)}" data-payee="${_esc(expense.paid_by)}">Pay &#9662;</button>
                </div>`;
            }
            html += '</div>';
        }

        card.innerHTML = html;

        // Settle buttons
        card.querySelectorAll('.btn-settle').forEach(btn => {
            btn.addEventListener('click', () => {
                _socket.emit('settleSplit', { splitId: parseInt(btn.dataset.splitId) });
            });
        });

        // Pay link buttons
        card.querySelectorAll('.btn-paylink').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                _showPayDropdown(btn, btn.dataset.amount, btn.dataset.payee);
            });
        });

        container.prepend(card);
    }

    function _showPayDropdown(btn, amount, payee) {
        if (_dropdownOpen) {
            _dropdownOpen.remove();
            _dropdownOpen = null;
        }

        const dd = document.createElement('div');
        dd.className = 'paylink-dropdown';
        dd.innerHTML = `
            <a href="https://venmo.com/?txn=pay&amount=${amount}&note=${encodeURIComponent('Ribbon split')}" target="_blank" rel="noopener">Venmo</a>
            <a href="https://cash.app/$cashtag/${amount}" target="_blank" rel="noopener">Cash App</a>
            <a href="https://www.paypal.com/paypalme/${encodeURIComponent(payee)}/${amount}" target="_blank" rel="noopener">PayPal</a>
            <a href="#" onclick="return false;">Zelle (open banking app)</a>
        `;
        btn.appendChild(dd);
        _dropdownOpen = dd;
    }

    function _renderBalances(balances) {
        const list = document.getElementById('balancesList');
        list.innerHTML = '';

        const entries = Object.entries(balances);
        if (entries.length === 0) {
            list.innerHTML = '<div class="empty-state">No expenses yet</div>';
            return;
        }

        for (const [name, amount] of entries) {
            const item = document.createElement('div');
            item.className = 'pay-balance-item';

            const nameSpan = document.createElement('span');
            nameSpan.className = 'pay-balance-name';
            nameSpan.textContent = name;

            const amountSpan = document.createElement('span');
            amountSpan.className = 'pay-balance-amount ' +
                (amount >= 0 ? 'pay-balance-positive' : 'pay-balance-negative');
            const symbol = '$';
            amountSpan.textContent = (amount >= 0 ? '+' : '') + symbol + Math.abs(amount).toFixed(2);

            item.appendChild(nameSpan);
            item.appendChild(amountSpan);
            list.appendChild(item);
        }
    }

    function _esc(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    return { init };
})();
