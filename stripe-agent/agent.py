#!/usr/bin/env python3
"""
Stripe Agent for Power FM Platform
Layer 8 (Subcarrier Paywall) + Station Subscriptions

Manage station subscriptions, premium content paywalls, fan subscriptions,
payment tracking, and revenue reporting.

Usage:
    venv/bin/python agent.py --scan                          # Sync all Stripe data
    venv/bin/python agent.py --revenue                       # Revenue summary
    venv/bin/python agent.py --subscriptions                 # List active subs
    venv/bin/python agent.py --customers                     # List customers
    venv/bin/python agent.py --create-product "Name" --price 999  # Create product ($9.99)
    venv/bin/python agent.py --report                        # Generate report
    venv/bin/python agent.py --daemon                        # Run continuously
"""

import os
import sys
import time
import signal
import logging
import argparse
from datetime import datetime, timedelta

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

from database import (
    get_connection, upsert_customer, upsert_product, upsert_price,
    upsert_subscription, upsert_payment, upsert_invoice,
    save_revenue_summary, get_active_subscriptions, get_all_subscriptions,
    get_all_customers, get_recent_payments, get_subscription_counts_by_product,
    get_monthly_revenue, get_past_due_subscriptions, get_failed_payments,
    get_expiring_trials, get_monthly_sub_changes, get_agent_state, set_agent_state,
)
from api_client import StripeClient

# --- Configuration ---
POLL_INTERVAL = 3600  # 1 hour
LOG_DIR = os.path.join(AGENT_DIR, 'logs')
REPORT_DIR = os.path.join(AGENT_DIR, 'reports')

# --- Logging ---
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'agent.log')),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('stripe-agent')

# --- Graceful Shutdown ---
running = True


def shutdown_handler(signum, frame):
    global running
    log.info("Shutdown signal received. Finishing current cycle...")
    running = False


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def sync_all_data(client, conn):
    """Sync all data from Stripe."""
    if not client.is_configured():
        log.error("Stripe API not configured. Create config/stripe_config.json with your secret_key.")
        return 0

    total = 0

    # Sync products
    log.info("Syncing products...")
    products = client.list_products()
    for p in products:
        upsert_product(conn, p)
    total += len(products)
    log.info(f"  {len(products)} products synced")

    # Sync prices
    log.info("Syncing prices...")
    prices = client.list_prices()
    for p in prices:
        upsert_price(conn, p)
    total += len(prices)
    log.info(f"  {len(prices)} prices synced")

    # Sync customers
    log.info("Syncing customers...")
    customers = client.list_customers()
    for c in customers:
        upsert_customer(conn, c)
    total += len(customers)
    log.info(f"  {len(customers)} customers synced")

    # Sync subscriptions
    log.info("Syncing subscriptions...")
    subs = client.list_subscriptions(status='all')
    for s in subs:
        upsert_subscription(conn, s)
    total += len(subs)
    log.info(f"  {len(subs)} subscriptions synced")

    # Sync payments
    log.info("Syncing payments...")
    payments = client.list_payments()
    for p in payments:
        upsert_payment(conn, p)
    total += len(payments)
    log.info(f"  {len(payments)} payments synced")

    # Sync invoices
    log.info("Syncing invoices...")
    invoices = client.list_invoices()
    for inv in invoices:
        upsert_invoice(conn, inv)
    total += len(invoices)
    log.info(f"  {len(invoices)} invoices synced")

    # Calculate and save revenue summary
    _calculate_revenue_summary(conn)

    set_agent_state(conn, 'last_scan_timestamp', datetime.utcnow().isoformat())
    log.info(f"Sync complete: {total} records processed")
    return total


def _calculate_revenue_summary(conn):
    """Calculate and save today's revenue summary."""
    today = datetime.utcnow().strftime('%Y-%m-%d')

    active_subs = get_active_subscriptions(conn)
    all_subs = get_all_subscriptions(conn)
    customers = get_all_customers(conn)

    # Calculate MRR from active subscriptions
    mrr_cents = 0
    for sub in active_subs:
        amount = sub['unit_amount_cents'] or 0
        interval = sub['recurring_interval'] or 'month'
        interval_count = sub['recurring_interval_count'] or 1

        if interval == 'year':
            monthly = amount / (12 * interval_count)
        elif interval == 'week':
            monthly = amount * 4.33 / interval_count
        elif interval == 'day':
            monthly = amount * 30.44 / interval_count
        else:  # month
            monthly = amount / interval_count

        mrr_cents += int(monthly)

    arr_cents = mrr_cents * 12

    # Count churned (canceled this month)
    this_month = datetime.utcnow().strftime('%Y-%m')
    churned = sum(1 for s in all_subs
                  if s['status'] == 'canceled' and
                  s['updated_at'] and s['updated_at'].startswith(this_month))

    new_subs = sum(1 for s in all_subs
                   if s['created_at'] and s['created_at'].startswith(this_month))

    # Revenue this month from succeeded payments
    monthly_rev = conn.execute("""
        SELECT COALESCE(SUM(amount_cents), 0) FROM payments
        WHERE status = 'succeeded'
        AND strftime('%Y-%m', created_at) = ?
    """, (this_month,)).fetchone()[0]

    save_revenue_summary(
        conn, today, mrr_cents, arr_cents,
        len(customers), len(active_subs), churned, new_subs, monthly_rev
    )


def show_revenue(conn):
    """Display revenue summary."""
    active_subs = get_active_subscriptions(conn)
    customers = get_all_customers(conn)
    past_due = get_past_due_subscriptions(conn)
    failed = get_failed_payments(conn, days=7)
    trials = get_expiring_trials(conn, days=7)

    mrr_cents = 0
    for sub in active_subs:
        amount = sub['unit_amount_cents'] or 0
        interval = sub['recurring_interval'] or 'month'
        interval_count = sub['recurring_interval_count'] or 1
        if interval == 'year':
            monthly = amount / (12 * interval_count)
        else:
            monthly = amount / interval_count
        mrr_cents += int(monthly)

    arr_cents = mrr_cents * 12

    print("\n=== Revenue Summary ===")
    print(f"  MRR: ${mrr_cents / 100:,.2f}")
    print(f"  ARR: ${arr_cents / 100:,.2f}")
    print(f"  Active Subscriptions: {len(active_subs)}")
    print(f"  Total Customers: {len(customers)}")
    print(f"  Past Due: {len(past_due)}")
    print(f"  Failed Payments (7d): {len(failed)}")
    print(f"  Expiring Trials (7d): {len(trials)}")


def show_subscriptions(conn):
    """Display active subscriptions."""
    subs = get_active_subscriptions(conn)
    if not subs:
        print("\nNo active subscriptions found.")
        print("Run --scan to sync data from Stripe, or check config/stripe_config.json")
        return

    print(f"\n=== Active Subscriptions ({len(subs)}) ===")
    print(f"{'Customer':<30} {'Product':<25} {'Amount':<12} {'Status':<10} {'Since'}")
    print("-" * 95)
    for s in subs:
        name = s['customer_name'] or s['customer_email'] or s['customer_id'] or 'Unknown'
        product = s['product_name'] or 'Unknown'
        amount = s['unit_amount_cents'] or 0
        interval = s['recurring_interval'] or 'mo'
        since = (s['created_at'] or '')[:10]
        print(f"  {name:<28} {product:<25} ${amount/100:>8.2f}/{interval:<3} {s['status']:<10} {since}")


def show_customers(conn):
    """Display all customers."""
    customers = get_all_customers(conn)
    if not customers:
        print("\nNo customers found.")
        print("Run --scan to sync data from Stripe, or check config/stripe_config.json")
        return

    print(f"\n=== Customers ({len(customers)}) ===")
    print(f"{'Name':<30} {'Email':<35} {'Subs':<6} {'Total Paid':<12} {'Since'}")
    print("-" * 95)
    for c in customers:
        name = c['name'] or 'Unknown'
        total = c['total_paid_cents'] or 0
        since = (c['created_at'] or '')[:10]
        print(f"  {name:<28} {c['email'] or '':<35} {c['active_subs']:<6} ${total/100:>8.2f}   {since}")


def create_product_with_price(client, conn, name, price_cents):
    """Create a product with a price in Stripe."""
    if not client.is_configured():
        log.error("Stripe API not configured.")
        return

    product = client.create_product(name)
    if not product:
        log.error("Failed to create product")
        return

    upsert_product(conn, product)
    log.info(f"Created product: {product['id']} — {name}")

    price = client.create_price(product['id'], price_cents)
    if not price:
        log.error("Failed to create price")
        return

    upsert_price(conn, price)
    log.info(f"Created price: {price['id']} — ${price_cents/100:.2f}/month")
    print(f"Product created: {name} — ${price_cents/100:.2f}/month")
    print(f"  Product ID: {product['id']}")
    print(f"  Price ID: {price['id']}")


def generate_report(conn):
    """Generate revenue report."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    report_path = os.path.join(REPORT_DIR, f'revenue_{today}.md')

    active_subs = get_active_subscriptions(conn)
    all_customers = get_all_customers(conn)
    recent_payments = get_recent_payments(conn, limit=15)
    product_breakdown = get_subscription_counts_by_product(conn)
    monthly_rev = get_monthly_revenue(conn, months=6)
    past_due = get_past_due_subscriptions(conn)
    failed = get_failed_payments(conn, days=7)
    trials = get_expiring_trials(conn, days=7)
    sub_changes = get_monthly_sub_changes(conn, months=6)

    # Calculate MRR
    mrr_cents = 0
    for sub in active_subs:
        amount = sub['unit_amount_cents'] or 0
        interval = sub['recurring_interval'] or 'month'
        interval_count = sub['recurring_interval_count'] or 1
        if interval == 'year':
            monthly = amount / (12 * interval_count)
        else:
            monthly = amount / interval_count
        mrr_cents += int(monthly)
    arr_cents = mrr_cents * 12

    this_month_rev = 0
    if monthly_rev:
        this_month_rev = monthly_rev[0]['revenue_cents'] or 0

    lines = [
        f"# Revenue Report — {today}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Revenue Overview",
        f"- Monthly Recurring Revenue (MRR): **${mrr_cents / 100:,.2f}**",
        f"- Annual Recurring Revenue (ARR): **${arr_cents / 100:,.2f}**",
        f"- Total Revenue (this month): **${this_month_rev / 100:,.2f}**",
        f"- Active Subscriptions: **{len(active_subs)}**",
        f"- Total Customers: **{len(all_customers)}**",
        "",
    ]

    # Subscription Breakdown
    lines.append("## Subscription Breakdown")
    if product_breakdown:
        lines.append("| Product | Price | Interval | Status | Count |")
        lines.append("|---------|-------|----------|--------|-------|")
        for row in product_breakdown:
            product = row['product_name'] or 'Unknown'
            amount = (row['unit_amount_cents'] or 0) / 100
            interval = row['recurring_interval'] or 'one-time'
            lines.append(f"| {product} | ${amount:.2f} | {interval} | {row['status']} | {row['count']} |")
    else:
        lines.append("No subscriptions found.")
    lines.append("")

    # Recent Payments
    lines.append("## Recent Payments")
    if recent_payments:
        lines.append("| Date | Customer | Amount | Status | Description |")
        lines.append("|------|----------|--------|--------|-------------|")
        for p in recent_payments:
            date = (p['created_at'] or '')[:10]
            name = p['customer_name'] or p['customer_email'] or 'Unknown'
            amount = (p['amount_cents'] or 0) / 100
            desc = (p['description'] or '')[:40]
            lines.append(f"| {date} | {name} | ${amount:.2f} | {p['status']} | {desc} |")
    else:
        lines.append("No payments recorded.")
    lines.append("")

    # Monthly Trends
    lines.append("## Monthly Trends")
    if monthly_rev:
        lines.append("| Month | Revenue | Payments |")
        lines.append("|-------|---------|----------|")
        for m in monthly_rev:
            rev = (m['revenue_cents'] or 0) / 100
            lines.append(f"| {m['month']} | ${rev:,.2f} | {m['payment_count']} |")
    else:
        lines.append("No monthly data available.")
    lines.append("")

    # Subscriber Changes
    if sub_changes:
        lines.append("## Subscriber Changes")
        lines.append("| Month | New Subs | Churned | Net Growth |")
        lines.append("|-------|----------|---------|------------|")
        for sc in sub_changes:
            new = sc['new_subs'] or 0
            churned = sc['churned'] or 0
            net = new - churned
            sign = '+' if net >= 0 else ''
            lines.append(f"| {sc['month']} | {new} | {churned} | {sign}{net} |")
        lines.append("")

    # Customer Summary (top 10)
    lines.append("## Top Customers")
    if all_customers:
        lines.append("| Customer | Email | Active Subs | Total Paid | Since |")
        lines.append("|----------|-------|-------------|------------|-------|")
        sorted_customers = sorted(all_customers, key=lambda c: c['total_paid_cents'] or 0, reverse=True)
        for c in sorted_customers[:10]:
            name = c['name'] or 'Unknown'
            total = (c['total_paid_cents'] or 0) / 100
            since = (c['created_at'] or '')[:10]
            lines.append(f"| {name} | {c['email'] or ''} | {c['active_subs']} | ${total:,.2f} | {since} |")
    else:
        lines.append("No customers found.")
    lines.append("")

    # Alerts
    lines.append("## Alerts")
    lines.append(f"- Past due subscriptions: **{len(past_due)}**")
    lines.append(f"- Failed payments (last 7 days): **{len(failed)}**")
    lines.append(f"- Expiring trials (next 7 days): **{len(trials)}**")
    lines.append("")

    # Stats
    last_scan = get_agent_state(conn, 'last_scan_timestamp') or 'Never'
    lines.append("## Stats")
    lines.append(f"- Last scan: {last_scan}")
    lines.append("")

    content = '\n'.join(lines)
    with open(report_path, 'w') as f:
        f.write(content)

    log.info(f"Report generated: {report_path}")
    return report_path


def run_daemon(client, conn):
    """Continuous polling loop."""
    log.info(f"Stripe agent starting in daemon mode (poll every {POLL_INTERVAL}s)")

    # Initial sync
    sync_all_data(client, conn)
    generate_report(conn)

    while running:
        log.info(f"Sleeping {POLL_INTERVAL}s until next sync...")
        for _ in range(POLL_INTERVAL):
            if not running:
                break
            time.sleep(1)

        if not running:
            break

        sync_all_data(client, conn)

        # Generate report hourly
        last_report = get_agent_state(conn, 'last_report_timestamp')
        if not last_report or (
            datetime.utcnow() - datetime.fromisoformat(last_report)
        ) > timedelta(hours=1):
            generate_report(conn)
            set_agent_state(conn, 'last_report_timestamp', datetime.utcnow().isoformat())

    log.info("Stripe agent stopped.")


def main():
    parser = argparse.ArgumentParser(description='Stripe Agent — Power FM Platform')
    parser.add_argument('--scan', action='store_true', help='Sync all data from Stripe')
    parser.add_argument('--revenue', action='store_true', help='Show revenue summary')
    parser.add_argument('--subscriptions', action='store_true', help='List active subscriptions')
    parser.add_argument('--customers', action='store_true', help='List all customers')
    parser.add_argument('--create-product', type=str, help='Create a product (name)')
    parser.add_argument('--price', type=int, help='Price in cents for --create-product')
    parser.add_argument('--report', action='store_true', help='Generate revenue report')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon')
    args = parser.parse_args()

    log.info("Initializing Stripe agent...")
    conn = get_connection()
    client = StripeClient()

    if args.create_product:
        price_cents = args.price or 999
        create_product_with_price(client, conn, args.create_product, price_cents)
    elif args.scan:
        count = sync_all_data(client, conn)
        print(f"Synced {count} records from Stripe")
    elif args.revenue:
        show_revenue(conn)
    elif args.subscriptions:
        show_subscriptions(conn)
    elif args.customers:
        show_customers(conn)
    elif args.report:
        report = generate_report(conn)
        print(f"Report saved to: {report}")
    elif args.daemon:
        run_daemon(client, conn)
    else:
        # Default: show revenue + generate report
        show_revenue(conn)
        report = generate_report(conn)
        print(f"\nReport saved to: {report}")

    conn.close()


if __name__ == '__main__':
    main()
