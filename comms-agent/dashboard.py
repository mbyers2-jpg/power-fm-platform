"""
Comms & Email Agent Dashboard — Flask web UI on port 5557.
Reads from comms-agent DB (read-write) and email-agent DB (read-only).
"""

import os
import sys
import sqlite3
from datetime import datetime, date

from flask import Flask, render_template, request, redirect, url_for, jsonify

# Import database helpers from the comms-agent codebase
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import (
    get_connection,
    get_email_db,
    get_pending_follow_ups,
    get_pending_drafts,
    get_overdue_follow_ups,
)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def priority_order(p):
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(p, 4)


# ---------------------------------------------------------------------------
# Dashboard (main page)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    # --- Email stats ---
    email_stats = {
        "total": 0,
        "unread": 0,
        "has_attachment": 0,
        "categories": {},
        "importance": {},
        "recent": [],
    }
    action_items = []

    email_conn = get_email_db()
    if email_conn:
        try:
            email_stats["total"] = email_conn.execute(
                "SELECT COUNT(*) FROM emails"
            ).fetchone()[0]
            email_stats["unread"] = email_conn.execute(
                "SELECT COUNT(*) FROM emails WHERE is_read = 0"
            ).fetchone()[0]
            email_stats["has_attachment"] = email_conn.execute(
                "SELECT COUNT(*) FROM emails WHERE has_attachment = 1"
            ).fetchone()[0]

            cats = email_conn.execute(
                "SELECT category, COUNT(*) as cnt FROM emails GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            email_stats["categories"] = {r["category"] or "uncategorized": r["cnt"] for r in cats}

            imp = email_conn.execute(
                "SELECT importance, COUNT(*) as cnt FROM emails GROUP BY importance ORDER BY cnt DESC"
            ).fetchall()
            email_stats["importance"] = {r["importance"] or "normal": r["cnt"] for r in imp}

            email_stats["recent"] = rows_to_dicts(email_conn.execute(
                "SELECT id, subject, sender, sender_email, date, category, importance, is_read, snippet, "
                "COALESCE(source, 'gmail') as source, COALESCE(account_email, '') as account_email "
                "FROM emails ORDER BY date DESC LIMIT 20"
            ).fetchall())

            # Per-account stats
            acct_rows = email_conn.execute(
                "SELECT COALESCE(source, 'gmail') as source, COALESCE(account_email, '') as account_email, COUNT(*) as cnt "
                "FROM emails GROUP BY source, account_email"
            ).fetchall()
            email_stats["accounts"] = [dict(r) for r in acct_rows]

            action_items = rows_to_dicts(email_conn.execute(
                "SELECT * FROM action_items WHERE status != 'completed' "
                "ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'normal' THEN 3 WHEN 'low' THEN 4 END, "
                "due_date ASC LIMIT 25"
            ).fetchall())
        finally:
            email_conn.close()

    # --- Comms stats ---
    comms_conn = get_connection()
    pending_followups = rows_to_dicts(get_pending_follow_ups(comms_conn))
    overdue = rows_to_dicts(get_overdue_follow_ups(comms_conn))
    pending_drafts = rows_to_dicts(get_pending_drafts(comms_conn))

    total_followups = comms_conn.execute("SELECT COUNT(*) FROM follow_ups").fetchone()[0]
    completed_followups = comms_conn.execute(
        "SELECT COUNT(*) FROM follow_ups WHERE status = 'completed'"
    ).fetchone()[0]
    total_drafts = comms_conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
    sent_drafts = comms_conn.execute(
        "SELECT COUNT(*) FROM drafts WHERE status = 'sent'"
    ).fetchone()[0]
    comms_conn.close()

    return render_template(
        "index.html",
        email_stats=email_stats,
        action_items=action_items,
        pending_followups=pending_followups,
        overdue=overdue,
        pending_drafts=pending_drafts,
        total_followups=total_followups,
        completed_followups=completed_followups,
        total_drafts=total_drafts,
        sent_drafts=sent_drafts,
        now=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Follow-ups page
# ---------------------------------------------------------------------------

@app.route("/follow-ups")
def follow_ups():
    comms_conn = get_connection()

    filter_status = request.args.get("status", "pending")
    filter_priority = request.args.get("priority", "all")

    query = "SELECT * FROM follow_ups WHERE 1=1"
    params = []

    if filter_status != "all":
        query += " AND status = ?"
        params.append(filter_status)
    if filter_priority != "all":
        query += " AND priority = ?"
        params.append(filter_priority)

    query += (
        " ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 "
        "WHEN 'medium' THEN 3 WHEN 'low' THEN 4 END, due_date ASC"
    )

    items = rows_to_dicts(comms_conn.execute(query, params).fetchall())
    overdue = rows_to_dicts(get_overdue_follow_ups(comms_conn))
    overdue_ids = {r["id"] for r in overdue}

    stats = {
        "total": comms_conn.execute("SELECT COUNT(*) FROM follow_ups").fetchone()[0],
        "pending": comms_conn.execute("SELECT COUNT(*) FROM follow_ups WHERE status='pending'").fetchone()[0],
        "completed": comms_conn.execute("SELECT COUNT(*) FROM follow_ups WHERE status='completed'").fetchone()[0],
        "overdue": len(overdue),
        "critical": comms_conn.execute("SELECT COUNT(*) FROM follow_ups WHERE priority='critical' AND status='pending'").fetchone()[0],
        "high": comms_conn.execute("SELECT COUNT(*) FROM follow_ups WHERE priority='high' AND status='pending'").fetchone()[0],
    }
    comms_conn.close()

    return render_template(
        "follow_ups.html",
        items=items,
        overdue_ids=overdue_ids,
        stats=stats,
        filter_status=filter_status,
        filter_priority=filter_priority,
        today=date.today().isoformat(),
    )


@app.route("/follow-ups/<int:item_id>/complete", methods=["POST"])
def complete_follow_up(item_id):
    comms_conn = get_connection()
    comms_conn.execute(
        "UPDATE follow_ups SET status = 'completed', completed_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), item_id),
    )
    comms_conn.commit()
    comms_conn.close()
    return redirect(url_for("follow_ups"))


# ---------------------------------------------------------------------------
# Drafts page
# ---------------------------------------------------------------------------

@app.route("/drafts")
def drafts():
    comms_conn = get_connection()

    filter_status = request.args.get("status", "pending_review")

    query = "SELECT * FROM drafts WHERE 1=1"
    params = []
    if filter_status != "all":
        query += " AND status = ?"
        params.append(filter_status)
    query += " ORDER BY created_at DESC"

    items = rows_to_dicts(comms_conn.execute(query, params).fetchall())

    stats = {
        "total": comms_conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0],
        "pending_review": comms_conn.execute("SELECT COUNT(*) FROM drafts WHERE status='pending_review'").fetchone()[0],
        "sent": comms_conn.execute("SELECT COUNT(*) FROM drafts WHERE status='sent'").fetchone()[0],
    }
    comms_conn.close()

    return render_template(
        "drafts.html",
        items=items,
        stats=stats,
        filter_status=filter_status,
    )


@app.route("/drafts/<int:draft_id>")
def draft_detail(draft_id):
    comms_conn = get_connection()
    draft = row_to_dict(comms_conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone())
    comms_conn.close()
    if not draft:
        return "Draft not found", 404
    return render_template("draft_detail.html", draft=draft)


# ---------------------------------------------------------------------------
# Contacts page
# ---------------------------------------------------------------------------

@app.route("/contacts")
def contacts():
    search = request.args.get("q", "").strip()
    sort = request.args.get("sort", "email_count")
    contact_list = []

    email_conn = get_email_db()
    if email_conn:
        try:
            if search:
                contact_list = rows_to_dicts(email_conn.execute(
                    "SELECT * FROM contacts WHERE name LIKE ? OR email LIKE ? OR organization LIKE ? "
                    "ORDER BY email_count DESC LIMIT 200",
                    (f"%{search}%", f"%{search}%", f"%{search}%"),
                ).fetchall())
            else:
                order = "email_count DESC" if sort == "email_count" else "last_contact DESC"
                contact_list = rows_to_dicts(email_conn.execute(
                    f"SELECT * FROM contacts ORDER BY {order} LIMIT 200"
                ).fetchall())

            total_contacts = email_conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
            cat_counts = dict(email_conn.execute(
                "SELECT category, COUNT(*) FROM contacts GROUP BY category ORDER BY COUNT(*) DESC"
            ).fetchall())
        finally:
            email_conn.close()
    else:
        total_contacts = 0
        cat_counts = {}

    return render_template(
        "contacts.html",
        contacts=contact_list,
        total_contacts=total_contacts,
        cat_counts=cat_counts,
        search=search,
        sort=sort,
    )


# ---------------------------------------------------------------------------
# API endpoints (for charts / AJAX)
# ---------------------------------------------------------------------------

@app.route("/api/email-categories")
def api_email_categories():
    email_conn = get_email_db()
    if not email_conn:
        return jsonify({})
    try:
        cats = email_conn.execute(
            "SELECT category, COUNT(*) as cnt FROM emails GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
        return jsonify({r["category"] or "uncategorized": r["cnt"] for r in cats})
    finally:
        email_conn.close()


@app.route("/api/email-timeline")
def api_email_timeline():
    email_conn = get_email_db()
    if not email_conn:
        return jsonify({})
    try:
        rows = email_conn.execute(
            "SELECT DATE(date) as d, COUNT(*) as cnt FROM emails "
            "WHERE date >= DATE('now', '-30 days') GROUP BY d ORDER BY d"
        ).fetchall()
        return jsonify({r["d"]: r["cnt"] for r in rows})
    finally:
        email_conn.close()


@app.route("/api/followup-priority")
def api_followup_priority():
    comms_conn = get_connection()
    rows = comms_conn.execute(
        "SELECT priority, COUNT(*) as cnt FROM follow_ups WHERE status='pending' GROUP BY priority"
    ).fetchall()
    comms_conn.close()
    return jsonify({r["priority"]: r["cnt"] for r in rows})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5557, debug=False)
