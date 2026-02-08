"""
Song Tracker Dashboard
Web UI for viewing catalog, revenue, and analytics.
Runs on http://localhost:5555
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AGENT_DIR)

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
from database import (
    get_connection, init_db, list_songs, get_song, search_songs,
    get_song_earnings, get_catalog_value, get_splits, get_stream_revenue,
    add_rights_holder, add_song
)
from calculator import (
    calculate_song_analytics, global_revenue_summary, project_revenue,
    TERRITORY_MULTIPLIERS, PRO_RATES, DISTRIBUTOR_FEES
)
from importer import import_csv

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = "song-tracker-dashboard-key"

IMPORTS_DIR = os.path.join(AGENT_DIR, "imports")
os.makedirs(IMPORTS_DIR, exist_ok=True)


def get_db():
    conn = get_connection()
    init_db(conn)
    return conn


# ─── Dashboard ───────────────────────────────────────────────────────

@app.route("/")
def index():
    conn = get_db()
    songs = list_songs(conn)
    catalog, grand_total = get_catalog_value(conn)
    summary = global_revenue_summary(conn)

    # Artist rollup
    artists = {}
    for s in catalog:
        a = s["artist"]
        if a not in artists:
            artists[a] = {"name": a, "songs": 0, "total": 0}
        artists[a]["songs"] += 1
        artists[a]["total"] += s["total"]
    artist_list = sorted(artists.values(), key=lambda x: -x["total"])

    # Source breakdown for chart
    source_data = {r["source"]: r["total"] or 0 for r in summary["by_source"]}

    # Rate cards
    rate_cards = conn.execute(
        "SELECT platform, tier, rate FROM rate_cards ORDER BY rate DESC"
    ).fetchall()

    # Rights holder stats
    total_holders = conn.execute("SELECT COUNT(DISTINCT name) FROM rights_holders").fetchone()[0]
    songs_with_splits = conn.execute("SELECT COUNT(DISTINCT song_id) FROM rights_holders").fetchone()[0]

    conn.close()

    return render_template("index.html",
        songs=catalog,
        grand_total=grand_total,
        total_songs=len(songs),
        artists=artist_list,
        source_data=source_data,
        monthly_trend=summary["monthly_trend"],
        territory_data=summary["by_territory"],
        rate_cards=[dict(r) for r in rate_cards],
        total_holders=total_holders,
        songs_with_splits=songs_with_splits,
        territory_multipliers=TERRITORY_MULTIPLIERS,
        distributor_fees=DISTRIBUTOR_FEES,
        pro_rates=PRO_RATES,
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


# ─── Song Detail ─────────────────────────────────────────────────────

@app.route("/song/<int:song_id>")
def song_detail(song_id):
    conn = get_db()
    analytics = calculate_song_analytics(song_id, conn)
    if not analytics:
        conn.close()
        return "Song not found", 404

    song = analytics["song"]
    conn.close()
    return render_template("song.html", analytics=analytics, song=song)


# ─── Artist Detail ───────────────────────────────────────────────────

@app.route("/artist/<name>")
def artist_detail(name):
    conn = get_db()
    songs = list_songs(conn, artist=name)
    song_data = []
    artist_total = 0
    for s in songs:
        earnings = get_song_earnings(conn, s["id"])
        song_data.append({**dict(s), **earnings})
        artist_total += earnings["total"]

    conn.close()
    return render_template("artist.html",
        artist_name=name,
        songs=song_data,
        artist_total=artist_total,
    )


# ─── Rights Holders / Splits ────────────────────────────────────────

@app.route("/splits")
def splits_page():
    conn = get_db()
    songs = list_songs(conn)

    song_splits = []
    for s in songs:
        holders = get_splits(conn, s["id"])
        total_pct = sum(h["split_pct"] for h in holders)
        song_splits.append({
            "song": dict(s),
            "holders": [dict(h) for h in holders],
            "total_pct": total_pct,
            "complete": abs(total_pct - 100) < 0.01 if holders else False,
        })

    # Unique holders
    all_holders = conn.execute("""
        SELECT name, role, pro, publisher, COUNT(DISTINCT song_id) as song_count,
               AVG(split_pct) as avg_split
        FROM rights_holders
        GROUP BY name, role
        ORDER BY song_count DESC
    """).fetchall()

    conn.close()
    return render_template("splits.html",
        song_splits=song_splits,
        all_holders=[dict(h) for h in all_holders],
    )


@app.route("/splits/add", methods=["POST"])
def add_split():
    conn = get_db()
    song_id = int(request.form["song_id"])
    name = request.form["name"].strip()
    role = request.form["role"]
    split_pct = float(request.form["split_pct"])
    pro = request.form.get("pro", "").strip() or None
    publisher = request.form.get("publisher", "").strip() or None
    pub_split_pct = float(request.form.get("pub_split_pct", 0))

    add_rights_holder(conn, song_id, name, role, split_pct,
                      pro=pro, publisher=publisher, pub_split_pct=pub_split_pct)
    conn.close()
    flash(f"Added {name} ({role}) at {split_pct}% to song #{song_id}")
    return redirect(url_for("splits_page"))


@app.route("/splits/delete/<int:holder_id>", methods=["POST"])
def delete_split(holder_id):
    conn = get_db()
    conn.execute("DELETE FROM rights_holders WHERE id = ?", (holder_id,))
    conn.commit()
    conn.close()
    flash("Rights holder removed")
    return redirect(url_for("splits_page"))


@app.route("/splits/bulk", methods=["POST"])
def bulk_add_splits():
    """Add the same rights holder to multiple songs at once."""
    conn = get_db()
    song_ids = request.form.getlist("song_ids")
    name = request.form["name"].strip()
    role = request.form["role"]
    split_pct = float(request.form["split_pct"])
    pro = request.form.get("pro", "").strip() or None
    publisher = request.form.get("publisher", "").strip() or None
    pub_split_pct = float(request.form.get("pub_split_pct", 0))

    count = 0
    for sid in song_ids:
        try:
            add_rights_holder(conn, int(sid), name, role, split_pct,
                              pro=pro, publisher=publisher, pub_split_pct=pub_split_pct)
            count += 1
        except Exception:
            pass

    conn.close()
    flash(f"Added {name} to {count} songs at {split_pct}%")
    return redirect(url_for("splits_page"))


# ─── Import Management ──────────────────────────────────────────────

@app.route("/imports")
def imports_page():
    conn = get_db()

    # Import history
    history = conn.execute("""
        SELECT * FROM import_log ORDER BY imported_at DESC LIMIT 50
    """).fetchall()

    # Pending files in imports/
    pending = []
    imports_path = Path(IMPORTS_DIR)
    if imports_path.exists():
        for f in sorted(imports_path.iterdir()):
            if f.suffix in (".csv", ".json"):
                already = conn.execute(
                    "SELECT id FROM import_log WHERE filename = ?", (f.name,)
                ).fetchone()
                pending.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "imported": bool(already),
                })

    conn.close()
    return render_template("imports.html",
        history=[dict(h) for h in history],
        pending=pending,
        import_types=["spotify", "apple", "distrokid", "tunecore", "ascap", "bmi", "soundexchange", "catalog", "generic_streams"],
    )


@app.route("/imports/upload", methods=["POST"])
def upload_import():
    if "file" not in request.files:
        flash("No file selected")
        return redirect(url_for("imports_page"))

    f = request.files["file"]
    if f.filename == "":
        flash("No file selected")
        return redirect(url_for("imports_page"))

    import_type = request.form.get("import_type", "generic_streams")
    filename = secure_filename(f.filename)
    filepath = os.path.join(IMPORTS_DIR, filename)
    f.save(filepath)

    # Process immediately
    conn = get_db()
    try:
        imported, skipped, errors = import_csv(filepath, import_type, conn)
        flash(f"Imported {imported} records from {filename} (skipped: {skipped})")
        if errors:
            flash(f"Errors: {errors}")
    except Exception as e:
        flash(f"Import failed: {str(e)}")

    conn.close()
    return redirect(url_for("imports_page"))


# ─── Song Management ────────────────────────────────────────────────

@app.route("/songs/add", methods=["POST"])
def add_song_route():
    conn = get_db()
    title = request.form["title"].strip()
    artist = request.form["artist"].strip()
    kwargs = {}
    for field in ["album", "isrc", "genre", "subgenre", "label", "distributor", "release_date", "status"]:
        val = request.form.get(field, "").strip()
        if val:
            kwargs[field] = val
    if not kwargs.get("status"):
        kwargs["status"] = "active"

    add_song(conn, title, artist, **kwargs)
    conn.close()
    flash(f"Added '{title}' by {artist}")
    return redirect(url_for("index"))


@app.route("/songs/edit/<int:song_id>", methods=["POST"])
def edit_song(song_id):
    conn = get_db()
    fields = ["title", "artist", "album", "isrc", "genre", "subgenre",
              "label", "distributor", "release_date", "status"]
    updates = []
    values = []
    for field in fields:
        val = request.form.get(field)
        if val is not None:
            updates.append(f"{field} = ?")
            values.append(val.strip())

    if updates:
        values.append(song_id)
        conn.execute(
            f"UPDATE songs SET {', '.join(updates)}, updated_at = datetime('now') WHERE id = ?",
            values
        )
        conn.commit()
        flash("Song updated")

    conn.close()
    return redirect(url_for("song_detail", song_id=song_id))


# ─── Analytics / Reference ──────────────────────────────────────────

@app.route("/analytics")
def analytics_page():
    conn = get_db()
    catalog, grand_total = get_catalog_value(conn)
    summary = global_revenue_summary(conn)

    # Songs by artist
    artist_songs = {}
    for s in catalog:
        a = s["artist"]
        if a not in artist_songs:
            artist_songs[a] = []
        artist_songs[a].append(s)

    # Rights holder coverage
    songs_total = len(catalog)
    songs_with_splits = conn.execute(
        "SELECT COUNT(DISTINCT song_id) FROM rights_holders"
    ).fetchone()[0]

    conn.close()
    return render_template("analytics.html",
        catalog=catalog,
        grand_total=grand_total,
        summary=summary,
        artist_songs=artist_songs,
        songs_total=songs_total,
        songs_with_splits=songs_with_splits,
        territory_multipliers=TERRITORY_MULTIPLIERS,
        distributor_fees=DISTRIBUTOR_FEES,
        pro_rates=PRO_RATES,
    )


# ─── API ─────────────────────────────────────────────────────────────

@app.route("/api/songs")
def api_songs():
    conn = get_db()
    catalog, total = get_catalog_value(conn)
    conn.close()
    return jsonify({"songs": catalog, "total": total})


@app.route("/api/song/<int:song_id>")
def api_song(song_id):
    conn = get_db()
    analytics = calculate_song_analytics(song_id, conn)
    conn.close()
    if not analytics:
        return jsonify({"error": "not found"}), 404
    return jsonify(analytics)


@app.route("/api/splits/<int:song_id>")
def api_splits(song_id):
    conn = get_db()
    holders = get_splits(conn, song_id)
    conn.close()
    return jsonify([dict(h) for h in holders])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5555, debug=False)
