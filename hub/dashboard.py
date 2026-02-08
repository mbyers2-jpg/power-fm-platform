"""
Agent Hub — Master Dashboard
Links all agent dashboards and shows live system status.
Runs on http://localhost:5550
"""

import os
import sys
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_ROOT = os.path.dirname(AGENT_DIR)
sys.path.insert(0, AGENT_DIR)

from flask import Flask, render_template, jsonify

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = "agent-hub-key"


# Dashboard registry
DASHBOARDS = [
    {
        "name": "Song Tracker",
        "port": 5555,
        "url": "http://localhost:5555",
        "icon": "&#9835;",
        "color": "#3fb950",
        "description": "Catalog, revenue, splits, imports, analytics",
        "agent_dir": os.path.join(AGENTS_ROOT, "song-tracker"),
        "db_path": os.path.join(AGENTS_ROOT, "song-tracker", "data", "songs.db"),
    },
    {
        "name": "Deal Tracker",
        "port": 5556,
        "url": "http://localhost:5556",
        "icon": "&#128188;",
        "color": "#58a6ff",
        "description": "Pipeline, deal details, gap analysis",
        "agent_dir": os.path.join(AGENTS_ROOT, "deal-tracker"),
        "db_path": os.path.join(AGENTS_ROOT, "deal-tracker", "data", "deals.db"),
    },
    {
        "name": "Comms & Email",
        "port": 5557,
        "url": "http://localhost:5557",
        "icon": "&#9993;",
        "color": "#bc8cff",
        "description": "Follow-ups, drafts, contacts, email overview",
        "agent_dir": os.path.join(AGENTS_ROOT, "comms-agent"),
        "db_path": os.path.join(AGENTS_ROOT, "comms-agent", "data", "comms.db"),
    },
    {
        "name": "Ribbon",
        "port": 5558,
        "url": "http://localhost:5558",
        "icon": "&#127872;",
        "color": "#f0883e",
        "description": "Secure E2EE conferencing, chat, file sharing",
        "agent_dir": os.path.join(AGENTS_ROOT, "secure-call"),
        "db_path": os.path.join(AGENTS_ROOT, "secure-call", "data", "secure_call.db"),
    },
]

# Background agents (non-dashboard)
BACKGROUND_AGENTS = [
    {
        "name": "Email Agent",
        "agent_dir": os.path.join(AGENTS_ROOT, "email-agent"),
        "db_path": os.path.join(AGENTS_ROOT, "email-agent", "data", "email_agent.db"),
        "icon": "&#128231;",
        "color": "#f0883e",
    },
    {
        "name": "Doc Manager",
        "agent_dir": os.path.join(AGENTS_ROOT, "doc-manager"),
        "icon": "&#128193;",
        "color": "#d29922",
    },
    {
        "name": "Research Agent",
        "agent_dir": os.path.join(AGENTS_ROOT, "research-agent"),
        "db_path": os.path.join(AGENTS_ROOT, "research-agent", "data", "research.db"),
        "icon": "&#128270;",
        "color": "#f778ba",
    },
]


def check_dashboard_health(port):
    """Check if a dashboard is responding."""
    try:
        resp = urlopen(f"http://localhost:{port}/", timeout=2)
        return resp.status == 200
    except (URLError, OSError):
        return False


def get_db_stats(db_path):
    """Get basic stats from a SQLite database."""
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        total_rows = 0
        table_info = {}
        for t in tables:
            name = t["name"]
            if name.startswith("sqlite_"):
                continue
            count = conn.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
            table_info[name] = count
            total_rows += count
        conn.close()
        return {"tables": len(table_info), "rows": total_rows, "detail": table_info}
    except Exception:
        return None


def get_agent_pid(agent_dir):
    """Check if agent is running via PID file."""
    for pidfile in ["agent.pid", "dashboard.pid"]:
        pf = os.path.join(agent_dir, pidfile)
        if os.path.exists(pf):
            try:
                pid = int(open(pf).read().strip())
                os.kill(pid, 0)  # Check if process exists
                return pid
            except (ValueError, OSError):
                pass
    return None


def get_latest_report(agent_dir):
    """Find the most recent report file."""
    reports_dir = os.path.join(agent_dir, "reports")
    if not os.path.isdir(reports_dir):
        briefings_dir = os.path.join(agent_dir, "briefings")
        if os.path.isdir(briefings_dir):
            reports_dir = briefings_dir
        else:
            return None
    try:
        files = sorted(Path(reports_dir).glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        if files:
            return {
                "name": files[0].name,
                "modified": datetime.fromtimestamp(files[0].stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "size": files[0].stat().st_size,
            }
    except Exception:
        pass
    return None


@app.route("/")
def index():
    dashboards = []
    for d in DASHBOARDS:
        info = dict(d)
        info["healthy"] = check_dashboard_health(d["port"])
        info["db_stats"] = get_db_stats(d.get("db_path"))
        info["pid"] = get_agent_pid(d["agent_dir"])
        info["latest_report"] = get_latest_report(d["agent_dir"])
        dashboards.append(info)

    agents = []
    for a in BACKGROUND_AGENTS:
        info = dict(a)
        info["pid"] = get_agent_pid(a["agent_dir"])
        info["db_stats"] = get_db_stats(a.get("db_path"))
        info["latest_report"] = get_latest_report(a["agent_dir"])
        agents.append(info)

    return render_template("index.html",
        dashboards=dashboards,
        agents=agents,
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


@app.route("/api/status")
def api_status():
    status = {}
    for d in DASHBOARDS:
        status[d["name"]] = {
            "port": d["port"],
            "healthy": check_dashboard_health(d["port"]),
        }
    return jsonify(status)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5550, debug=False)
