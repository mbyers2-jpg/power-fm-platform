"""
Deal Tracker Dashboard
Web UI for viewing pipeline, deal details, and gap analysis.
Runs on http://localhost:5556
"""

import os
import sys
import json
from datetime import datetime

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash

from database import (
    get_connection, get_active_deals, get_stale_deals,
    get_deal_with_details, get_upcoming_milestones, get_deal_stats,
    upsert_deal, add_milestone, link_contact
)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = "deal-tracker-dashboard-key"


STAGE_ORDER = ['prospect', 'negotiation', 'contract', 'signed', 'active']
STAGE_LABELS = {
    'prospect': 'Prospect',
    'negotiation': 'Negotiation',
    'contract': 'Contract',
    'signed': 'Signed',
    'active': 'Active',
}
STAGE_COLORS = {
    'prospect': 'purple',
    'negotiation': 'yellow',
    'contract': 'orange',
    'signed': 'accent',
    'active': 'green',
}

STATUS_COLORS = {
    'active': 'green',
    'closed_won': 'accent',
    'closed_lost': 'red',
}

PRIORITY_COLORS = {
    'critical': 'red',
    'high': 'orange',
    'medium': 'yellow',
    'low': 'text-dim',
}


def get_db():
    return get_connection()


# ---------- Helpers ----------

def deals_by_stage(conn):
    """Return active deals grouped by stage in pipeline order."""
    deals = get_active_deals(conn)
    grouped = {stage: [] for stage in STAGE_ORDER}
    for d in deals:
        stage = d['stage'] or 'prospect'
        if stage not in grouped:
            grouped[stage] = []
        grouped[stage].append(dict(d))
    return grouped


def gap_analysis(conn):
    """Analyze active deals for missing data."""
    deals = get_active_deals(conn)
    gaps = []
    for d in deals:
        detail = get_deal_with_details(conn, d['id'])
        if not detail:
            continue
        deal = detail['deal']
        deal_gaps = []
        if not detail['documents']:
            deal_gaps.append('No documents linked')
        if not detail['contacts']:
            deal_gaps.append('No contacts associated')
        if not detail['milestones']:
            deal_gaps.append('No milestones set')
        if not deal['next_action']:
            deal_gaps.append('No next action defined')
        if not deal['next_action_date']:
            deal_gaps.append('No next action date')
        if deal['stage'] in ('prospect', 'negotiation') and not deal['value_estimate']:
            deal_gaps.append('No value estimate')
        if not deal['counterparty']:
            deal_gaps.append('No counterparty identified')

        if deal_gaps:
            gaps.append({
                'deal': dict(deal),
                'gaps': deal_gaps,
                'gap_count': len(deal_gaps),
                'severity': 'high' if len(deal_gaps) >= 4 else ('medium' if len(deal_gaps) >= 2 else 'low'),
            })

    gaps.sort(key=lambda g: -g['gap_count'])
    return gaps


def stale_days(last_activity):
    """Calculate days since last activity."""
    if not last_activity:
        return 999
    try:
        last = datetime.fromisoformat(last_activity[:19])
        return (datetime.utcnow() - last).days
    except (ValueError, TypeError):
        return 999


# ---------- Routes ----------

@app.route("/")
def index():
    conn = get_db()
    stats = get_deal_stats(conn)
    grouped = deals_by_stage(conn)
    stale = get_stale_deals(conn, days=30)
    upcoming = get_upcoming_milestones(conn, days=14)

    # Stage counts for chart
    stage_counts = {stage: len(grouped.get(stage, [])) for stage in STAGE_ORDER}

    # Deal type breakdown
    type_rows = conn.execute(
        "SELECT deal_type, COUNT(*) as cnt FROM deals WHERE status = 'active' GROUP BY deal_type ORDER BY cnt DESC"
    ).fetchall()
    type_data = {(r[0] or 'Uncategorized'): r[1] for r in type_rows}

    # Priority breakdown
    priority_rows = conn.execute(
        "SELECT priority, COUNT(*) as cnt FROM deals WHERE status = 'active' GROUP BY priority"
    ).fetchall()
    priority_data = {(r[0] or 'medium'): r[1] for r in priority_rows}

    # Recent activity
    recent = conn.execute(
        "SELECT * FROM deals WHERE last_activity IS NOT NULL ORDER BY last_activity DESC LIMIT 5"
    ).fetchall()

    # Stale deals with days since activity
    stale_with_days = []
    for d in stale:
        sd = dict(d)
        sd['days_stale'] = stale_days(d['last_activity'])
        stale_with_days.append(sd)

    conn.close()

    return render_template("index.html",
        stats=stats,
        grouped=grouped,
        stage_order=STAGE_ORDER,
        stage_labels=STAGE_LABELS,
        stage_colors=STAGE_COLORS,
        stage_counts=stage_counts,
        type_data=type_data,
        priority_data=priority_data,
        stale_deals=stale_with_days,
        upcoming_milestones=upcoming,
        recent_deals=[dict(r) for r in recent],
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


@app.route("/deal/<int:deal_id>")
def deal_detail(deal_id):
    conn = get_db()
    detail = get_deal_with_details(conn, deal_id)
    if not detail:
        conn.close()
        return "Deal not found", 404

    deal = dict(detail['deal'])
    milestones = [dict(m) for m in detail['milestones']]
    documents = [dict(d) for d in detail['documents']]
    contacts = [dict(c) for c in detail['contacts']]

    # Calculate completion metrics
    total_milestones = len(milestones)
    completed_milestones = sum(1 for m in milestones if m['status'] == 'completed')
    milestone_pct = int((completed_milestones / total_milestones * 100)) if total_milestones > 0 else 0

    # Days since last activity
    deal['days_stale'] = stale_days(deal.get('last_activity'))
    deal['is_stale'] = deal['days_stale'] >= 30

    # All deals for navigation
    all_deals = conn.execute("SELECT id, name FROM deals ORDER BY name").fetchall()

    conn.close()

    return render_template("deal.html",
        deal=deal,
        milestones=milestones,
        documents=documents,
        contacts=contacts,
        total_milestones=total_milestones,
        completed_milestones=completed_milestones,
        milestone_pct=milestone_pct,
        all_deals=[dict(d) for d in all_deals],
        stage_labels=STAGE_LABELS,
        stage_colors=STAGE_COLORS,
        status_colors=STATUS_COLORS,
        priority_colors=PRIORITY_COLORS,
    )


@app.route("/gaps")
def gaps_page():
    conn = get_db()
    gaps = gap_analysis(conn)
    stats = get_deal_stats(conn)

    # Summary metrics
    total_active = stats['active']
    deals_with_gaps = len(gaps)
    clean_deals = total_active - deals_with_gaps
    gap_pct = int((deals_with_gaps / total_active * 100)) if total_active > 0 else 0

    # Gap type frequency
    gap_freq = {}
    for g in gaps:
        for gap_text in g['gaps']:
            gap_freq[gap_text] = gap_freq.get(gap_text, 0) + 1
    gap_freq_sorted = sorted(gap_freq.items(), key=lambda x: -x[1])

    conn.close()

    return render_template("gaps.html",
        gaps=gaps,
        total_active=total_active,
        deals_with_gaps=deals_with_gaps,
        clean_deals=clean_deals,
        gap_pct=gap_pct,
        gap_freq=gap_freq_sorted,
    )


# ---------- Deal Edit Actions ----------

@app.route("/deal/<int:deal_id>/edit", methods=["POST"])
def edit_deal(deal_id):
    conn = get_db()
    fields = ["name", "entity", "counterparty", "counterparty_email", "deal_type",
              "status", "stage", "priority", "value_estimate", "start_date",
              "next_action", "next_action_date", "notes"]
    updates = []
    values = []
    for field in fields:
        val = request.form.get(field)
        if val is not None:
            updates.append(f"{field} = ?")
            values.append(val.strip() if val.strip() else None)

    if updates:
        updates.append("updated_at = ?")
        values.append(datetime.utcnow().isoformat())
        values.append(deal_id)
        conn.execute(
            f"UPDATE deals SET {', '.join(updates)} WHERE id = ?",
            values
        )
        conn.commit()
        flash("Deal updated successfully")

    conn.close()
    return redirect(url_for("deal_detail", deal_id=deal_id))


@app.route("/deal/<int:deal_id>/milestone", methods=["POST"])
def add_milestone_route(deal_id):
    conn = get_db()
    title = request.form["title"].strip()
    due_date = request.form.get("due_date", "").strip() or None
    description = request.form.get("description", "").strip()
    add_milestone(conn, deal_id, title, due_date=due_date, description=description)
    conn.close()
    flash(f"Milestone '{title}' added")
    return redirect(url_for("deal_detail", deal_id=deal_id))


@app.route("/deal/<int:deal_id>/milestone/<int:milestone_id>/complete", methods=["POST"])
def complete_milestone(deal_id, milestone_id):
    conn = get_db()
    conn.execute(
        "UPDATE milestones SET status = 'completed', completed_date = ? WHERE id = ? AND deal_id = ?",
        (datetime.utcnow().isoformat(), milestone_id, deal_id)
    )
    conn.commit()
    conn.close()
    flash("Milestone completed")
    return redirect(url_for("deal_detail", deal_id=deal_id))


@app.route("/deal/<int:deal_id>/contact", methods=["POST"])
def add_contact_route(deal_id):
    conn = get_db()
    name = request.form["contact_name"].strip()
    email = request.form["contact_email"].strip()
    role = request.form.get("role", "").strip()
    link_contact(conn, deal_id, name, email, role=role)
    conn.close()
    flash(f"Contact '{name}' linked")
    return redirect(url_for("deal_detail", deal_id=deal_id))


@app.route("/deal/<int:deal_id>/stage", methods=["POST"])
def update_stage(deal_id):
    """Quick stage update from Kanban drag or button."""
    conn = get_db()
    new_stage = request.form.get("stage", "prospect")
    conn.execute(
        "UPDATE deals SET stage = ?, updated_at = ? WHERE id = ?",
        (new_stage, datetime.utcnow().isoformat(), deal_id)
    )
    conn.commit()
    conn.close()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"ok": True})
    flash(f"Stage updated to {new_stage}")
    return redirect(url_for("index"))


# ---------- API ----------

@app.route("/api/deals")
def api_deals():
    conn = get_db()
    deals = conn.execute("SELECT * FROM deals ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(d) for d in deals])


@app.route("/api/stats")
def api_stats():
    conn = get_db()
    stats = get_deal_stats(conn)
    conn.close()
    return jsonify(stats)


@app.route("/api/deal/<int:deal_id>")
def api_deal(deal_id):
    conn = get_db()
    detail = get_deal_with_details(conn, deal_id)
    conn.close()
    if not detail:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        'deal': dict(detail['deal']),
        'milestones': [dict(m) for m in detail['milestones']],
        'documents': [dict(d) for d in detail['documents']],
        'contacts': [dict(c) for c in detail['contacts']],
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5556, debug=False)
