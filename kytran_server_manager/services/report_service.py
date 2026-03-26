"""Report service — compliance scan reports with optional PDF generation."""

import json
import os
import socket
from datetime import datetime, timezone

from flask import current_app, render_template

from ..db import get_db

# WeasyPrint is optional — PDF generation degrades gracefully
try:
    from weasyprint import HTML as WeasyHTML

    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Severity sort order (high first)
# ---------------------------------------------------------------------------
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _reports_dir():
    """Return (and create) the reports output directory."""
    data_dir = current_app.config.get(
        "DATA_DIR", "/var/lib/kytran-server-manager"
    )
    path = os.path.join(data_dir, "reports")
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# 1. Fetch all data needed for a report
# ---------------------------------------------------------------------------
def get_scan_report_data(scan_id):
    """Return a dict with everything needed to render a compliance report.

    Keys:
        scan        – the compliance_scans row (dict)
        pack_scores – list of {pack_id, total, passed, failed, errors, score}
        findings    – all scan results sorted high→medium→low
        evidence    – dict keyed by soc2_mapping (TSC category)
        server      – {hostname, generated_at}

    Returns None when *scan_id* does not exist.
    """
    conn = get_db()
    try:
        # -- scan record --------------------------------------------------
        row = conn.execute(
            "SELECT * FROM compliance_scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        if row is None:
            return None
        scan = dict(row)

        # -- pack scores (aggregate from results) -------------------------
        pack_rows = conn.execute(
            """
            SELECT
                pack_id,
                COUNT(*)                                       AS total,
                SUM(CASE WHEN status = 'pass'  THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN status = 'fail'  THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors
            FROM compliance_scan_results
            WHERE scan_id = ?
            GROUP BY pack_id
            """,
            (scan_id,),
        ).fetchall()

        pack_scores = []
        for pr in pack_rows:
            d = dict(pr)
            d["score"] = (
                round(d["passed"] / d["total"] * 100, 1)
                if d["total"] > 0
                else 0.0
            )
            pack_scores.append(d)

        # -- findings (all results, sorted by severity) --------------------
        finding_rows = conn.execute(
            """
            SELECT * FROM compliance_scan_results
            WHERE scan_id = ?
            ORDER BY
                CASE severity
                    WHEN 'high'   THEN 0
                    WHEN 'medium' THEN 1
                    WHEN 'low'    THEN 2
                    ELSE 3
                END,
                rule_id
            """,
            (scan_id,),
        ).fetchall()
        findings = [dict(f) for f in finding_rows]

        # Parse soc2_controls JSON where present
        for f in findings:
            raw = f.get("soc2_controls", "[]")
            try:
                f["soc2_controls"] = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                f["soc2_controls"] = []

        # -- evidence grouped by SOC 2 TSC mapping ------------------------
        ev_rows = conn.execute(
            """
            SELECT * FROM compliance_evidence
            WHERE scan_id = ?
            ORDER BY soc2_mapping, collected_at
            """,
            (scan_id,),
        ).fetchall()

        evidence = {}
        for ev in ev_rows:
            d = dict(ev)
            key = d.get("soc2_mapping") or "Unmapped"
            evidence.setdefault(key, []).append(d)

        # -- server info ---------------------------------------------------
        server = {
            "hostname": socket.gethostname(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        return {
            "scan": scan,
            "pack_scores": pack_scores,
            "findings": findings,
            "evidence": evidence,
            "server": server,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Render an HTML report string
# ---------------------------------------------------------------------------
def render_html_report(scan_id):
    """Render compliance_report.html and return the HTML string, or None."""
    data = get_scan_report_data(scan_id)
    if data is None:
        return None
    return render_template("compliance_report.html", **data)


# ---------------------------------------------------------------------------
# 3. Generate a PDF report
# ---------------------------------------------------------------------------
def generate_pdf_report(scan_id):
    """Generate a PDF from the HTML report.

    Returns:
        (pdf_bytes, filename)  on success
        (None, None)           if scan not found or WeasyPrint unavailable
    """
    if not WEASYPRINT_AVAILABLE:
        current_app.logger.warning(
            "WeasyPrint not installed — PDF generation unavailable"
        )
        return None, None

    html_string = render_html_report(scan_id)
    if html_string is None:
        return None, None

    pdf_bytes = WeasyHTML(string=html_string).write_pdf()

    # Persist to disk
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"compliance_{scan_id}_{timestamp}.pdf"
    filepath = os.path.join(_reports_dir(), filename)
    with open(filepath, "wb") as fh:
        fh.write(pdf_bytes)

    return pdf_bytes, filename


# ---------------------------------------------------------------------------
# 4. List previously generated reports
# ---------------------------------------------------------------------------
def list_reports():
    """Return a list of dicts describing saved PDF reports.

    Each dict: {filename, size_bytes, created_at}
    """
    reports_path = _reports_dir()
    results = []
    for name in sorted(os.listdir(reports_path), reverse=True):
        if not name.endswith(".pdf"):
            continue
        full = os.path.join(reports_path, name)
        stat = os.stat(full)
        results.append(
            {
                "filename": name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        )
    return results
