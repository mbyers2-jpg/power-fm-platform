"""
Real-Time Revenue Calculator
Handles royalty calculations, split distributions, projections,
and global territory-based rate adjustments.
"""

import json
from datetime import datetime, timedelta
from database import get_connection, init_db


# ─── Global Per-Stream Rates by Territory (2025-2026 averages) ──────
# Source: industry reports, distributor dashboards
TERRITORY_MULTIPLIERS = {
    # Tier 1 markets (full rate)
    "US": 1.0, "CA": 0.95, "GB": 0.92, "AU": 0.88, "DE": 0.85,
    "FR": 0.82, "JP": 0.80, "NL": 0.80, "SE": 0.78, "NO": 0.78,
    "DK": 0.78, "CH": 0.85, "NZ": 0.82, "IE": 0.80, "AT": 0.80,
    # Tier 2 markets
    "BR": 0.35, "MX": 0.30, "AR": 0.20, "CL": 0.35, "CO": 0.25,
    "KR": 0.55, "ES": 0.60, "IT": 0.55, "PT": 0.50, "PL": 0.40,
    "ZA": 0.30, "NG": 0.15, "GH": 0.12, "KE": 0.12, "TZ": 0.10,
    # Tier 3 markets
    "IN": 0.10, "PK": 0.08, "BD": 0.06, "PH": 0.15, "ID": 0.12,
    "VN": 0.10, "EG": 0.12, "TR": 0.20,
    # Default
    "GLOBAL": 0.50,
}

# PRO collection rates (percentage of performance royalties collected)
PRO_RATES = {
    "ASCAP": {"writer_share": 0.50, "publisher_share": 0.50, "admin_rate": 0.125},
    "BMI": {"writer_share": 0.50, "publisher_share": 0.50, "admin_rate": 0.135},
    "SESAC": {"writer_share": 0.50, "publisher_share": 0.50, "admin_rate": 0.10},
    "SOCAN": {"writer_share": 0.50, "publisher_share": 0.50, "admin_rate": 0.115},
    "PRS": {"writer_share": 0.50, "publisher_share": 0.50, "admin_rate": 0.15},
    "GEMA": {"writer_share": 0.50, "publisher_share": 0.50, "admin_rate": 0.15},
    "SACEM": {"writer_share": 0.50, "publisher_share": 0.50, "admin_rate": 0.14},
    "JASRAC": {"writer_share": 0.50, "publisher_share": 0.50, "admin_rate": 0.12},
    "SoundExchange": {"writer_share": 0.45, "publisher_share": 0.00, "performer_share": 0.50, "admin_rate": 0.05},
}

# Distributor fee schedules
DISTRIBUTOR_FEES = {
    "tunecore": {"type": "flat", "annual_fee": 29.99, "rev_share": 0.0},
    "distrokid": {"type": "flat", "annual_fee": 22.99, "rev_share": 0.0},
    "cd_baby": {"type": "rev_share", "annual_fee": 0, "rev_share": 0.09},
    "unitedmasters": {"type": "rev_share", "annual_fee": 0, "rev_share": 0.10},
    "stem": {"type": "rev_share", "annual_fee": 0, "rev_share": 0.05},
    "empire": {"type": "rev_share", "annual_fee": 0, "rev_share": 0.30},
    "awal": {"type": "rev_share", "annual_fee": 0, "rev_share": 0.15},
    "ditto": {"type": "flat", "annual_fee": 19.99, "rev_share": 0.0},
    "amuse": {"type": "rev_share", "annual_fee": 0, "rev_share": 0.0},
    "self": {"type": "flat", "annual_fee": 0, "rev_share": 0.0},
}

# Radio royalty estimates per spin by market size
RADIO_RATES = {
    "major_market": {"terrestrial": 1.50, "satellite": 0.08, "internet": 0.02, "college": 0.0},
    "medium_market": {"terrestrial": 0.75, "satellite": 0.08, "internet": 0.02, "college": 0.0},
    "small_market": {"terrestrial": 0.25, "satellite": 0.08, "internet": 0.02, "college": 0.0},
}


def calculate_stream_revenue(stream_count, platform, territory="US", tier="premium"):
    """Calculate revenue from streams with territory adjustment."""
    conn = get_connection()
    rate_row = conn.execute(
        "SELECT rate FROM rate_cards WHERE platform = ? AND tier = ? "
        "ORDER BY effective_date DESC LIMIT 1",
        (platform, tier)
    ).fetchone()
    conn.close()

    base_rate = rate_row["rate"] if rate_row else 0.003
    territory_mult = TERRITORY_MULTIPLIERS.get(territory, TERRITORY_MULTIPLIERS["GLOBAL"])
    adjusted_rate = base_rate * territory_mult
    return stream_count * adjusted_rate


def calculate_split_distribution(total_revenue, song_id, conn=None):
    """Calculate how revenue splits among rights holders."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    holders = conn.execute(
        "SELECT * FROM rights_holders WHERE song_id = ? ORDER BY split_pct DESC",
        (song_id,)
    ).fetchall()

    if close_conn:
        conn.close()

    if not holders:
        return [{"name": "Unassigned", "role": "unknown", "amount": total_revenue, "split_pct": 100}]

    distribution = []
    for h in holders:
        writer_amount = total_revenue * (h["split_pct"] / 100)
        pub_amount = 0
        if h["pub_split_pct"] and h["pub_split_pct"] > 0:
            pub_amount = writer_amount * (h["pub_split_pct"] / 100)
            writer_amount -= pub_amount

        distribution.append({
            "name": h["name"],
            "role": h["role"],
            "split_pct": h["split_pct"],
            "gross_amount": total_revenue * (h["split_pct"] / 100),
            "net_to_writer": writer_amount,
            "to_publisher": pub_amount,
            "publisher": h["publisher"],
            "pro": h["pro"],
        })

    return distribution


def calculate_pro_royalties(song_id, performance_revenue, pro_name, conn=None):
    """Calculate PRO royalty breakdown (writer vs publisher share)."""
    rates = PRO_RATES.get(pro_name, PRO_RATES["ASCAP"])
    admin_fee = performance_revenue * rates["admin_rate"]
    net = performance_revenue - admin_fee
    writer_share = net * rates["writer_share"]
    publisher_share = net * rates["publisher_share"]

    return {
        "gross": performance_revenue,
        "admin_fee": admin_fee,
        "net": net,
        "writer_share": writer_share,
        "publisher_share": publisher_share,
        "performer_share": net * rates.get("performer_share", 0),
    }


def calculate_distributor_cut(gross_revenue, distributor):
    """Calculate distributor fees."""
    dist = DISTRIBUTOR_FEES.get(distributor.lower(), DISTRIBUTOR_FEES["self"])
    if dist["type"] == "rev_share":
        fee = gross_revenue * dist["rev_share"]
    else:
        fee = 0  # Flat fee is annual, not per-revenue
    return {
        "gross": gross_revenue,
        "distributor_fee": fee,
        "net": gross_revenue - fee,
        "fee_type": dist["type"],
    }


def project_revenue(song_id, months_ahead=12, conn=None):
    """Project future revenue based on historical trends."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    # Get last 90 days of streaming data
    ninety_days_ago = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT platform, SUM(stream_count) as streams, SUM(revenue) as rev,
               COUNT(DISTINCT date) as days
        FROM streams
        WHERE song_id = ? AND date >= ?
        GROUP BY platform
    """, (song_id, ninety_days_ago)).fetchall()

    if close_conn:
        conn.close()

    projections = []
    total_monthly = 0
    for row in rows:
        if row["days"] and row["days"] > 0:
            daily_streams = row["streams"] / row["days"]
            daily_revenue = row["rev"] / row["days"]
            monthly_streams = daily_streams * 30
            monthly_revenue = daily_revenue * 30

            projections.append({
                "platform": row["platform"],
                "daily_streams": round(daily_streams),
                "monthly_streams": round(monthly_streams),
                "monthly_revenue": round(monthly_revenue, 2),
                "projected_total": round(monthly_revenue * months_ahead, 2),
            })
            total_monthly += monthly_revenue

    return {
        "monthly_total": round(total_monthly, 2),
        "annual_projection": round(total_monthly * 12, 2),
        "custom_projection": round(total_monthly * months_ahead, 2),
        "months": months_ahead,
        "by_platform": projections,
    }


def calculate_song_analytics(song_id, conn=None):
    """Comprehensive analytics for a single song."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    song = conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
    if not song:
        return None

    # Total streams
    stream_totals = conn.execute("""
        SELECT platform, SUM(stream_count) as total_streams, SUM(revenue) as total_revenue
        FROM streams WHERE song_id = ?
        GROUP BY platform ORDER BY total_streams DESC
    """, (song_id,)).fetchall()

    # Total radio plays
    radio_totals = conn.execute("""
        SELECT station_type, COUNT(*) as plays, SUM(audience_estimate) as reach,
               SUM(revenue_estimate) as revenue
        FROM radio_plays WHERE song_id = ?
        GROUP BY station_type
    """, (song_id,)).fetchall()

    # PRO royalties
    pro_totals = conn.execute("""
        SELECT pro, royalty_type, SUM(net_amount) as total
        FROM pro_royalties WHERE song_id = ?
        GROUP BY pro, royalty_type
    """, (song_id,)).fetchall()

    # Sync placements
    sync_totals = conn.execute("""
        SELECT placement_type, COUNT(*) as count, SUM(fee) as total_fee
        FROM sync_placements WHERE song_id = ?
        GROUP BY placement_type
    """, (song_id,)).fetchall()

    # Playlist placements
    playlists = conn.execute("""
        SELECT * FROM playlist_placements WHERE song_id = ?
        ORDER BY playlist_followers DESC
    """, (song_id,)).fetchall()

    # Latest audience data
    audience = conn.execute("""
        SELECT * FROM audience_data WHERE song_id = ?
        ORDER BY date DESC LIMIT 1
    """, (song_id,)).fetchone()

    # Revenue splits
    from database import get_song_earnings
    earnings = get_song_earnings(conn, song_id)
    splits = calculate_split_distribution(earnings["total"], song_id, conn)
    projections = project_revenue(song_id, 12, conn)

    if close_conn:
        conn.close()

    return {
        "song": dict(song),
        "streams": [dict(r) for r in stream_totals],
        "radio": [dict(r) for r in radio_totals],
        "pro_royalties": [dict(r) for r in pro_totals],
        "sync": [dict(r) for r in sync_totals],
        "playlists": [dict(r) for r in playlists],
        "audience": dict(audience) if audience else None,
        "earnings": earnings,
        "splits": splits,
        "projections": projections,
    }


def global_revenue_summary(conn=None):
    """Total revenue across entire catalog, all territories, all sources."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    # Streaming by territory
    territory_streams = conn.execute("""
        SELECT territory, SUM(stream_count) as streams, SUM(revenue) as revenue
        FROM streams GROUP BY territory ORDER BY revenue DESC
    """).fetchall()

    # Revenue by source type
    by_source = conn.execute("""
        SELECT 'streaming' as source, SUM(revenue) as total FROM streams
        UNION ALL
        SELECT 'radio', SUM(revenue_estimate) FROM radio_plays
        UNION ALL
        SELECT 'pro_royalties', SUM(net_amount) FROM pro_royalties
        UNION ALL
        SELECT 'sync', SUM(fee) FROM sync_placements WHERE status IN ('placed','aired','paid')
    """).fetchall()

    # Monthly trend
    monthly_trend = conn.execute("""
        SELECT strftime('%Y-%m', date) as month,
               SUM(stream_count) as streams, SUM(revenue) as revenue
        FROM streams
        GROUP BY month ORDER BY month DESC LIMIT 12
    """).fetchall()

    if close_conn:
        conn.close()

    return {
        "by_territory": [dict(r) for r in territory_streams],
        "by_source": [dict(r) for r in by_source],
        "monthly_trend": [dict(r) for r in monthly_trend],
    }
