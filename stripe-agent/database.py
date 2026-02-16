"""
Local SQLite database for Stripe payment tracking, subscriptions, customers, and revenue.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'stripe.db')


def get_connection():
    """Get a database connection, creating tables if needed."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _create_tables(conn)
    return conn


def _create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_id TEXT UNIQUE NOT NULL,
            email TEXT,
            name TEXT,
            phone TEXT,
            metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_id TEXT UNIQUE NOT NULL,
            name TEXT,
            description TEXT,
            active INTEGER DEFAULT 1,
            metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_id TEXT UNIQUE NOT NULL,
            product_id TEXT,
            unit_amount_cents INTEGER,
            currency TEXT DEFAULT 'usd',
            recurring_interval TEXT,
            recurring_interval_count INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_id TEXT UNIQUE NOT NULL,
            customer_id TEXT,
            product_id TEXT,
            price_id TEXT,
            status TEXT DEFAULT 'active',
            current_period_start TEXT,
            current_period_end TEXT,
            cancel_at_period_end INTEGER DEFAULT 0,
            trial_end TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_id TEXT UNIQUE NOT NULL,
            customer_id TEXT,
            amount_cents INTEGER,
            currency TEXT DEFAULT 'usd',
            status TEXT DEFAULT 'pending',
            description TEXT,
            payment_method TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_id TEXT UNIQUE NOT NULL,
            customer_id TEXT,
            subscription_id TEXT,
            amount_due_cents INTEGER,
            amount_paid_cents INTEGER,
            currency TEXT DEFAULT 'usd',
            status TEXT,
            period_start TEXT,
            period_end TEXT,
            paid_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS revenue_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            mrr_cents INTEGER DEFAULT 0,
            arr_cents INTEGER DEFAULT 0,
            total_customers INTEGER DEFAULT 0,
            active_subscriptions INTEGER DEFAULT 0,
            churned INTEGER DEFAULT 0,
            new_subscribers INTEGER DEFAULT 0,
            revenue_cents INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
        CREATE INDEX IF NOT EXISTS idx_customers_stripe ON customers(stripe_id);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions(customer_id);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe ON subscriptions(stripe_id);
        CREATE INDEX IF NOT EXISTS idx_payments_customer ON payments(customer_id);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        CREATE INDEX IF NOT EXISTS idx_payments_created ON payments(created_at);
        CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_id);
        CREATE INDEX IF NOT EXISTS idx_invoices_subscription ON invoices(subscription_id);
        CREATE INDEX IF NOT EXISTS idx_prices_product ON prices(product_id);
        CREATE INDEX IF NOT EXISTS idx_products_stripe ON products(stripe_id);
        CREATE INDEX IF NOT EXISTS idx_revenue_summary_date ON revenue_summary(date);
    """)
    conn.commit()


# --- Customer operations ---

def upsert_customer(conn, data):
    """Insert or update a customer record from Stripe data."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO customers (stripe_id, email, name, phone, metadata, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_id) DO UPDATE SET
            email = excluded.email,
            name = excluded.name,
            phone = excluded.phone,
            metadata = excluded.metadata,
            updated_at = excluded.updated_at
    """, (
        data['id'],
        data.get('email', ''),
        data.get('name', ''),
        data.get('phone', ''),
        str(data.get('metadata', {})),
        _ts_to_iso(data.get('created')) or now,
        now,
    ))
    conn.commit()


def upsert_product(conn, data):
    """Insert or update a product record from Stripe data."""
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO products (stripe_id, name, description, active, metadata, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            active = excluded.active,
            metadata = excluded.metadata,
            updated_at = excluded.updated_at
    """, (
        data['id'],
        data.get('name', ''),
        data.get('description', ''),
        1 if data.get('active', True) else 0,
        str(data.get('metadata', {})),
        _ts_to_iso(data.get('created')) or now,
        now,
    ))
    conn.commit()


def upsert_price(conn, data):
    """Insert or update a price record from Stripe data."""
    recurring = data.get('recurring') or {}
    conn.execute("""
        INSERT INTO prices (stripe_id, product_id, unit_amount_cents, currency,
                            recurring_interval, recurring_interval_count, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_id) DO UPDATE SET
            product_id = excluded.product_id,
            unit_amount_cents = excluded.unit_amount_cents,
            currency = excluded.currency,
            recurring_interval = excluded.recurring_interval,
            recurring_interval_count = excluded.recurring_interval_count,
            active = excluded.active
    """, (
        data['id'],
        data.get('product', ''),
        data.get('unit_amount', 0),
        data.get('currency', 'usd'),
        recurring.get('interval'),
        recurring.get('interval_count', 1),
        1 if data.get('active', True) else 0,
        _ts_to_iso(data.get('created')) or datetime.utcnow().isoformat(),
    ))
    conn.commit()


def upsert_subscription(conn, data):
    """Insert or update a subscription record from Stripe data."""
    now = datetime.utcnow().isoformat()
    # Extract product_id and price_id from items
    items = data.get('items', {}).get('data', [])
    price_id = ''
    product_id = ''
    if items:
        price_obj = items[0].get('price', {})
        price_id = price_obj.get('id', '')
        product_id = price_obj.get('product', '')

    conn.execute("""
        INSERT INTO subscriptions (stripe_id, customer_id, product_id, price_id, status,
                                   current_period_start, current_period_end,
                                   cancel_at_period_end, trial_end, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_id) DO UPDATE SET
            customer_id = excluded.customer_id,
            product_id = excluded.product_id,
            price_id = excluded.price_id,
            status = excluded.status,
            current_period_start = excluded.current_period_start,
            current_period_end = excluded.current_period_end,
            cancel_at_period_end = excluded.cancel_at_period_end,
            trial_end = excluded.trial_end,
            updated_at = excluded.updated_at
    """, (
        data['id'],
        data.get('customer', ''),
        product_id,
        price_id,
        data.get('status', 'active'),
        _ts_to_iso(data.get('current_period_start')),
        _ts_to_iso(data.get('current_period_end')),
        1 if data.get('cancel_at_period_end', False) else 0,
        _ts_to_iso(data.get('trial_end')),
        _ts_to_iso(data.get('created')) or now,
        now,
    ))
    conn.commit()


def upsert_payment(conn, data):
    """Insert or update a payment (charge) record from Stripe data."""
    conn.execute("""
        INSERT INTO payments (stripe_id, customer_id, amount_cents, currency, status,
                              description, payment_method, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_id) DO UPDATE SET
            customer_id = excluded.customer_id,
            amount_cents = excluded.amount_cents,
            currency = excluded.currency,
            status = excluded.status,
            description = excluded.description,
            payment_method = excluded.payment_method
    """, (
        data['id'],
        data.get('customer', ''),
        data.get('amount', 0),
        data.get('currency', 'usd'),
        data.get('status', 'pending'),
        data.get('description', ''),
        data.get('payment_method', ''),
        _ts_to_iso(data.get('created')) or datetime.utcnow().isoformat(),
    ))
    conn.commit()


def upsert_invoice(conn, data):
    """Insert or update an invoice record from Stripe data."""
    conn.execute("""
        INSERT INTO invoices (stripe_id, customer_id, subscription_id, amount_due_cents,
                              amount_paid_cents, currency, status, period_start, period_end,
                              paid_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_id) DO UPDATE SET
            customer_id = excluded.customer_id,
            subscription_id = excluded.subscription_id,
            amount_due_cents = excluded.amount_due_cents,
            amount_paid_cents = excluded.amount_paid_cents,
            currency = excluded.currency,
            status = excluded.status,
            period_start = excluded.period_start,
            period_end = excluded.period_end,
            paid_at = excluded.paid_at
    """, (
        data['id'],
        data.get('customer', ''),
        data.get('subscription', ''),
        data.get('amount_due', 0),
        data.get('amount_paid', 0),
        data.get('currency', 'usd'),
        data.get('status', ''),
        _ts_to_iso(data.get('period_start')),
        _ts_to_iso(data.get('period_end')),
        _ts_to_iso(data.get('status_transitions', {}).get('paid_at') if isinstance(data.get('status_transitions'), dict) else None),
        _ts_to_iso(data.get('created')) or datetime.utcnow().isoformat(),
    ))
    conn.commit()


def save_revenue_summary(conn, date_str, mrr_cents, arr_cents, total_customers,
                         active_subs, churned, new_subs, revenue_cents):
    """Insert or update a daily revenue summary row."""
    conn.execute("""
        INSERT INTO revenue_summary (date, mrr_cents, arr_cents, total_customers,
                                     active_subscriptions, churned, new_subscribers, revenue_cents)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            mrr_cents = excluded.mrr_cents,
            arr_cents = excluded.arr_cents,
            total_customers = excluded.total_customers,
            active_subscriptions = excluded.active_subscriptions,
            churned = excluded.churned,
            new_subscribers = excluded.new_subscribers,
            revenue_cents = excluded.revenue_cents
    """, (date_str, mrr_cents, arr_cents, total_customers, active_subs,
          churned, new_subs, revenue_cents))
    conn.commit()


# --- Query helpers ---

def get_active_subscriptions(conn):
    """Get all active subscriptions with customer and price details."""
    return conn.execute("""
        SELECT s.*, c.email as customer_email, c.name as customer_name,
               p.name as product_name, pr.unit_amount_cents, pr.recurring_interval,
               pr.recurring_interval_count
        FROM subscriptions s
        LEFT JOIN customers c ON s.customer_id = c.stripe_id
        LEFT JOIN products p ON s.product_id = p.stripe_id
        LEFT JOIN prices pr ON s.price_id = pr.stripe_id
        WHERE s.status = 'active'
        ORDER BY s.created_at DESC
    """).fetchall()


def get_all_subscriptions(conn):
    """Get all subscriptions with customer and product details."""
    return conn.execute("""
        SELECT s.*, c.email as customer_email, c.name as customer_name,
               p.name as product_name, pr.unit_amount_cents, pr.recurring_interval,
               pr.recurring_interval_count
        FROM subscriptions s
        LEFT JOIN customers c ON s.customer_id = c.stripe_id
        LEFT JOIN products p ON s.product_id = p.stripe_id
        LEFT JOIN prices pr ON s.price_id = pr.stripe_id
        ORDER BY s.created_at DESC
    """).fetchall()


def get_all_customers(conn):
    """Get all customers ordered by creation date."""
    return conn.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM subscriptions WHERE customer_id = c.stripe_id AND status = 'active') as active_subs,
               (SELECT COALESCE(SUM(amount_cents), 0) FROM payments WHERE customer_id = c.stripe_id AND status = 'succeeded') as total_paid_cents
        FROM customers c
        ORDER BY c.created_at DESC
    """).fetchall()


def get_recent_payments(conn, limit=20):
    """Get recent payments with customer details."""
    return conn.execute("""
        SELECT pay.*, c.email as customer_email, c.name as customer_name
        FROM payments pay
        LEFT JOIN customers c ON pay.customer_id = c.stripe_id
        ORDER BY pay.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()


def get_subscription_counts_by_product(conn):
    """Get subscription counts grouped by product and status."""
    return conn.execute("""
        SELECT p.name as product_name, pr.unit_amount_cents, pr.recurring_interval,
               s.status, COUNT(*) as count
        FROM subscriptions s
        LEFT JOIN products p ON s.product_id = p.stripe_id
        LEFT JOIN prices pr ON s.price_id = pr.stripe_id
        GROUP BY p.name, pr.unit_amount_cents, pr.recurring_interval, s.status
        ORDER BY p.name, s.status
    """).fetchall()


def get_monthly_revenue(conn, months=6):
    """Get monthly revenue totals from payments."""
    return conn.execute("""
        SELECT strftime('%Y-%m', created_at) as month,
               SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END) as revenue_cents,
               COUNT(CASE WHEN status = 'succeeded' THEN 1 END) as payment_count
        FROM payments
        WHERE created_at >= date('now', ? || ' months')
        GROUP BY strftime('%Y-%m', created_at)
        ORDER BY month DESC
    """, (f'-{months}',)).fetchall()


def get_past_due_subscriptions(conn):
    """Get subscriptions that are past due."""
    return conn.execute("""
        SELECT s.*, c.email as customer_email, c.name as customer_name,
               p.name as product_name
        FROM subscriptions s
        LEFT JOIN customers c ON s.customer_id = c.stripe_id
        LEFT JOIN products p ON s.product_id = p.stripe_id
        WHERE s.status = 'past_due'
    """).fetchall()


def get_failed_payments(conn, days=7):
    """Get failed payments in the last N days."""
    return conn.execute("""
        SELECT pay.*, c.email as customer_email, c.name as customer_name
        FROM payments pay
        LEFT JOIN customers c ON pay.customer_id = c.stripe_id
        WHERE pay.status = 'failed'
        AND pay.created_at >= date('now', ? || ' days')
        ORDER BY pay.created_at DESC
    """, (f'-{days}',)).fetchall()


def get_expiring_trials(conn, days=7):
    """Get subscriptions with trials ending in the next N days."""
    return conn.execute("""
        SELECT s.*, c.email as customer_email, c.name as customer_name,
               p.name as product_name
        FROM subscriptions s
        LEFT JOIN customers c ON s.customer_id = c.stripe_id
        LEFT JOIN products p ON s.product_id = p.stripe_id
        WHERE s.status = 'trialing'
        AND s.trial_end IS NOT NULL
        AND s.trial_end != ''
        AND date(s.trial_end) <= date('now', '+' || ? || ' days')
        ORDER BY s.trial_end ASC
    """, (days,)).fetchall()


def get_monthly_sub_changes(conn, months=6):
    """Get monthly new subscriber and churn counts."""
    return conn.execute("""
        SELECT strftime('%Y-%m', created_at) as month,
               COUNT(*) as new_subs,
               SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END) as churned
        FROM subscriptions
        WHERE created_at >= date('now', ? || ' months')
        GROUP BY strftime('%Y-%m', created_at)
        ORDER BY month DESC
    """, (f'-{months}',)).fetchall()


def get_revenue_history(conn, limit=30):
    """Get recent revenue summary history."""
    return conn.execute("""
        SELECT * FROM revenue_summary
        ORDER BY date DESC
        LIMIT ?
    """, (limit,)).fetchall()


# --- Agent state ---

def get_agent_state(conn, key, default=None):
    """Get a persistent agent state value."""
    row = conn.execute(
        "SELECT value FROM agent_state WHERE key = ?", (key,)
    ).fetchone()
    return row['value'] if row else default


def set_agent_state(conn, key, value):
    """Set a persistent agent state value."""
    conn.execute("""
        INSERT OR REPLACE INTO agent_state (key, value, updated_at)
        VALUES (?, ?, ?)
    """, (key, value, datetime.utcnow().isoformat()))
    conn.commit()


# --- Utilities ---

def _ts_to_iso(ts):
    """Convert a Unix timestamp (int) to ISO string, or return None."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.utcfromtimestamp(ts).isoformat()
    if isinstance(ts, str):
        return ts
    return None
