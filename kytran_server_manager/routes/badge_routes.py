"""Public SVG compliance badge endpoints — no auth required."""
from flask import Response
from ..db import get_db


def _score_to_grade(score):
    """Convert numeric score to letter grade."""
    if score >= 95:
        return "A+"
    elif score >= 90:
        return "A"
    elif score >= 85:
        return "B+"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    return "F"


def _score_to_color(score):
    """Convert numeric score to badge color."""
    if score >= 90:
        return "#22c55e"  # green
    elif score >= 80:
        return "#3b82f6"  # blue
    elif score >= 70:
        return "#f59e0b"  # yellow
    return "#ef4444"  # red


def _render_badge_svg(label, value, color):
    """Render a shields.io-style SVG badge."""
    label_width = len(label) * 7 + 12
    value_width = len(value) * 7.5 + 12
    total_width = label_width + value_width

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20">
  <defs>
    <linearGradient id="bg" x2="0" y2="100%">
      <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
      <stop offset="1" stop-opacity=".1"/>
    </linearGradient>
    <clipPath id="r"><rect width="{total_width}" height="20" rx="3" fill="#fff"/></clipPath>
  </defs>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="20" fill="#555"/>
    <rect x="{label_width}" width="{value_width}" height="20" fill="{color}"/>
    <rect width="{total_width}" height="20" fill="url(#bg)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{label_width / 2}" y="15" fill="#010101" fill-opacity=".3">{label}</text>
    <text x="{label_width / 2}" y="14">{label}</text>
    <text x="{label_width + value_width / 2}" y="15" fill="#010101" fill-opacity=".3">{value}</text>
    <text x="{label_width + value_width / 2}" y="14">{value}</text>
  </g>
</svg>"""


PACK_LABELS = {
    "ubuntu-stig": "Ubuntu STIG",
    "docker-stig": "Docker STIG",
    "network-stig": "Network STIG",
    "cis-ubuntu": "CIS Ubuntu",
    "hipaa": "HIPAA",
    "overall": "Compliance",
}


def _get_overall_score():
    """Get overall score from most recent compliance scan."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT score FROM compliance_scans ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        return row["score"] if row else None
    finally:
        conn.close()


def _get_pack_score(pack_id):
    """Calculate pack score from most recent scan results."""
    conn = get_db()
    try:
        # Find the latest scan that included this pack
        row = conn.execute(
            """SELECT scan_id FROM compliance_scan_results
               WHERE pack_id = ?
               ORDER BY rowid DESC LIMIT 1""",
            (pack_id,),
        ).fetchone()
        if not row:
            return None
        scan_id = row["scan_id"]
        stats = conn.execute(
            """SELECT
                 COUNT(*) AS total,
                 SUM(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) AS passed
               FROM compliance_scan_results
               WHERE scan_id = ? AND pack_id = ?""",
            (scan_id, pack_id),
        ).fetchone()
        if not stats or stats["total"] == 0:
            return None
        return round(stats["passed"] / stats["total"] * 100, 1)
    finally:
        conn.close()


def register_badge_routes(bp, admin_required):
    """Register public badge endpoints (no auth decorator applied)."""

    @bp.route("/api/compliance/badge/<pack_id>.svg")
    def compliance_badge(pack_id):
        if pack_id not in PACK_LABELS:
            svg = _render_badge_svg("compliance", "unknown pack", "#999")
            return Response(svg, mimetype="image/svg+xml")

        label = PACK_LABELS[pack_id]

        try:
            if pack_id == "overall":
                score = _get_overall_score()
            else:
                score = _get_pack_score(pack_id)
        except Exception:
            score = None

        if score is None:
            svg = _render_badge_svg(label, "N/A", "#999")
        else:
            grade = _score_to_grade(score)
            color = _score_to_color(score)
            value = f"{grade} {score:.0f}%"
            svg = _render_badge_svg(label, value, color)

        return Response(
            svg,
            mimetype="image/svg+xml",
            headers={"Cache-Control": "no-cache, max-age=300"},
        )
