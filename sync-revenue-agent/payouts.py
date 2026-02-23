"""
Payout Processor — calculates and tracks creator payouts.
Reads from sync-legal-agent deals.db for completed deals.
Integrates with stripe-agent for payment processing.
"""

import os
import sqlite3
from datetime import datetime, date

import database as db

# External agent database paths
LEGAL_DB = os.path.expanduser("~/Agents/sync-legal-agent/data/legal.db")
STRIPE_DB = os.path.expanduser("~/Agents/stripe-agent/data/stripe.db")
SONGS_DB = os.path.expanduser("~/Agents/song-tracker/data/songs.db")


def _open_readonly_db(path):
    """Open an external SQLite database in read-only mode."""
    if not os.path.exists(path):
        return None
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def calculate_split(gross_fee, commission_tier):
    """
    Apply the PTC business model split based on commission tier.

    Tiers:
        roster:            creator 70%, PTC 30%
        roster_publishing: creator 65%, PTC 35% (20% platform + 15% publishing)
        external:          creator 80%, PTC 20%
        managed_external:  creator 75%, PTC 25%

    Overrides (applied regardless of tier):
        micro (gross_fee < 500):    creator 60%, PTC 40%
        premium (gross_fee > 50000): creator 85%, PTC 15%

    Returns dict with:
        creator_pct, platform_pct, creator_payout, platform_commission, tier_applied
    """
    # Tier-based defaults
    tier_rates = {
        "roster":            (70, 30),
        "roster_publishing": (65, 35),
        "external":          (80, 20),
        "managed_external":  (75, 25),
    }

    # Start with tier rate
    creator_pct, platform_pct = tier_rates.get(commission_tier, (70, 30))
    tier_applied = commission_tier

    # Micro override: deals under $500 — high admin overhead
    if gross_fee < 500:
        creator_pct, platform_pct = 60, 40
        tier_applied = "micro"

    # Premium override: deals over $50,000 — incentivizes big deals
    if gross_fee > 50000:
        creator_pct, platform_pct = 85, 15
        tier_applied = "premium"

    creator_payout = round(gross_fee * (creator_pct / 100), 2)
    platform_commission = round(gross_fee * (platform_pct / 100), 2)

    return {
        "creator_pct": creator_pct,
        "platform_pct": platform_pct,
        "creator_payout": creator_payout,
        "platform_commission": platform_commission,
        "tier_applied": tier_applied,
    }


def process_deal_payout(conn, deal_id):
    """
    Process a single deal payout.
    Reads deal from sync-legal-agent's legal.db, finds the corresponding creator,
    calculates the split, records the earning, and handles advance recoupment.

    Returns dict with payout details, or None if deal can't be processed.
    """
    legal_conn = _open_readonly_db(LEGAL_DB)
    if not legal_conn:
        print(f"  [WARN] sync-legal-agent database not found at {LEGAL_DB}")
        return None

    try:
        # Read deal from legal.db
        deal = legal_conn.execute(
            "SELECT * FROM sync_deals WHERE id = ?", (deal_id,)
        ).fetchone()
        if not deal:
            print(f"  [WARN] Deal {deal_id} not found in legal.db")
            return None

        # Check if already processed
        existing = conn.execute(
            "SELECT id FROM placement_earnings WHERE deal_id = ?", (deal_id,)
        ).fetchone()
        if existing:
            print(f"  [SKIP] Deal {deal_id} already processed (earning #{existing['id']})")
            return None

        # Find the creator — try by artist name or creator name
        creator = None
        # Try common field names from legal.db deals table
        for field in ["artist_name", "creator_name", "licensor_name", "artist"]:
            try:
                name = deal[field]
                if name:
                    creator = db.get_creator_by_name(conn, name)
                    if creator:
                        break
            except (IndexError, KeyError):
                continue

        if not creator:
            print(f"  [WARN] No matching creator found for deal {deal_id}")
            return None

        # Extract deal details
        gross_fee = 0
        for field in ["total_fee", "license_fee", "fee", "amount", "gross_fee"]:
            try:
                val = deal[field]
                if val and float(val) > 0:
                    gross_fee = float(val)
                    break
            except (IndexError, KeyError, ValueError, TypeError):
                continue

        if gross_fee <= 0:
            print(f"  [WARN] Deal {deal_id} has no valid fee amount")
            return None

        # Get placement details
        placement_type = None
        for field in ["placement_type", "license_type", "type"]:
            try:
                placement_type = deal[field]
                if placement_type:
                    break
            except (IndexError, KeyError):
                continue

        client_name = None
        for field in ["client_name", "licensee", "client", "company"]:
            try:
                client_name = deal[field]
                if client_name:
                    break
            except (IndexError, KeyError):
                continue

        project_name = None
        for field in ["project_name", "project", "production"]:
            try:
                project_name = deal[field]
                if project_name:
                    break
            except (IndexError, KeyError):
                continue

        song_id = 0
        for field in ["song_id", "track_id"]:
            try:
                val = deal[field]
                if val:
                    song_id = int(val)
                    break
            except (IndexError, KeyError, ValueError, TypeError):
                continue

        # Calculate split
        split = calculate_split(gross_fee, creator["commission_tier"])

        # Record the earning
        earning_id = db.record_earning(
            conn, creator["id"], song_id, gross_fee,
            deal_id=deal_id,
            placement_type=placement_type,
            client_name=client_name,
            project_name=project_name,
        )

        # Handle advance recoupment
        recouped_total = 0
        outstanding_advances = db.get_outstanding_advances(conn, creator["id"])
        remaining = split["creator_payout"]

        for adv in outstanding_advances:
            if remaining <= 0:
                break
            recouped = db.recoup_advance(conn, adv["id"], remaining)
            recouped_total += recouped
            remaining -= recouped

        if recouped_total > 0:
            # Adjust the creator's balance for recoupment
            conn.execute("""
                UPDATE creators SET
                    balance_due = balance_due - ?,
                    updated_at = datetime('now')
                WHERE id = ?
            """, (recouped_total, creator["id"]))
            conn.commit()

        result = {
            "earning_id": earning_id,
            "deal_id": deal_id,
            "creator": creator["name"],
            "gross_fee": gross_fee,
            "tier_applied": split["tier_applied"],
            "creator_payout": split["creator_payout"],
            "platform_commission": split["platform_commission"],
            "advance_recouped": recouped_total,
            "net_to_creator": split["creator_payout"] - recouped_total,
        }

        print(f"  [OK] Deal {deal_id}: ${gross_fee:,.2f} -> "
              f"Creator ${split['creator_payout']:,.2f} / PTC ${split['platform_commission']:,.2f} "
              f"({split['tier_applied']} tier)")
        if recouped_total > 0:
            print(f"       Advance recouped: ${recouped_total:,.2f}")

        return result

    finally:
        legal_conn.close()


def process_pending_payouts(conn):
    """
    Process all deals from sync-legal-agent that have been paid but not yet recorded.
    Looks for deals with status 'paid' or 'completed' in legal.db.

    Returns list of processed payout results.
    """
    legal_conn = _open_readonly_db(LEGAL_DB)
    if not legal_conn:
        print(f"[WARN] sync-legal-agent database not found at {LEGAL_DB}")
        print("       No pending payouts to process.")
        return []

    results = []
    try:
        # Get all paid/completed deals
        deals = legal_conn.execute("""
            SELECT id FROM sync_deals
            WHERE status IN ('paid', 'completed', 'executed')
            ORDER BY id
        """).fetchall()

        if not deals:
            print("[INFO] No paid deals found in legal.db")
            return results

        print(f"[INFO] Found {len(deals)} paid/completed deals to check")

        for deal in deals:
            deal_id = deal["id"]
            # Check if already processed
            existing = conn.execute(
                "SELECT id FROM placement_earnings WHERE deal_id = ?", (deal_id,)
            ).fetchone()
            if existing:
                continue

            result = process_deal_payout(conn, deal_id)
            if result:
                results.append(result)

    finally:
        legal_conn.close()

    if results:
        print(f"\n[OK] Processed {len(results)} new payouts")
        total_gross = sum(r["gross_fee"] for r in results)
        total_creator = sum(r["creator_payout"] for r in results)
        total_platform = sum(r["platform_commission"] for r in results)
        print(f"     Total gross: ${total_gross:,.2f}")
        print(f"     Creator payouts: ${total_creator:,.2f}")
        print(f"     Platform commission: ${total_platform:,.2f}")
    else:
        print("[INFO] No new payouts to process")

    return results


def get_payout_summary(conn):
    """
    Get a summary of all payouts by status.

    Returns dict with pending, invoiced, processing, paid totals and counts.
    """
    statuses = ["pending", "invoiced", "processing", "paid", "disputed"]
    summary = {}

    for status in statuses:
        row = conn.execute("""
            SELECT COUNT(*) as count, COALESCE(SUM(creator_payout), 0) as total
            FROM placement_earnings WHERE payment_status = ?
        """, (status,)).fetchone()
        summary[status] = {
            "count": row["count"],
            "total": row["total"],
        }

    # Grand totals
    total_row = conn.execute("""
        SELECT COUNT(*) as count,
               COALESCE(SUM(gross_fee), 0) as gross,
               COALESCE(SUM(platform_commission), 0) as commission,
               COALESCE(SUM(creator_payout), 0) as payouts
        FROM placement_earnings
    """).fetchone()

    summary["totals"] = {
        "count": total_row["count"],
        "gross": total_row["gross"],
        "commission": total_row["commission"],
        "payouts": total_row["payouts"],
    }

    return summary


def recoup_advance(conn, creator_id, amount):
    """
    Apply a payout amount against outstanding advances for a creator.
    Processes advances in FIFO order (oldest first).

    Returns total amount recouped.
    """
    outstanding = db.get_outstanding_advances(conn, creator_id)
    total_recouped = 0
    remaining = amount

    for adv in outstanding:
        if remaining <= 0:
            break
        recouped = db.recoup_advance(conn, adv["id"], remaining)
        total_recouped += recouped
        remaining -= recouped

    return total_recouped


if __name__ == "__main__":
    conn = db.get_connection()
    db.init_db(conn)

    print("=== Payout Processor ===\n")

    # Show split calculations for each tier
    test_amounts = [250, 5000, 15000, 75000]
    tiers = ["roster", "roster_publishing", "external", "managed_external"]

    for amount in test_amounts:
        print(f"\n--- ${amount:,} Deal ---")
        for tier in tiers:
            split = calculate_split(amount, tier)
            print(f"  {tier:20s}: Creator ${split['creator_payout']:>10,.2f} ({split['creator_pct']}%) | "
                  f"PTC ${split['platform_commission']:>10,.2f} ({split['platform_pct']}%) | "
                  f"Tier: {split['tier_applied']}")

    conn.close()
