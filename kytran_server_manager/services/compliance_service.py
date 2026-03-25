"""
Compliance Scanning Engine (KSM Edition)
=========================================
Loads STIG/CIS rule packs, executes checks directly on the host,
stores results in SQLite compliance_* tables.
"""

import json
import logging
import os
import re
import subprocess
import uuid
from datetime import datetime

from ..db import get_db

logger = logging.getLogger(__name__)

RULE_PACKS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "rule_packs"
)

# ============================================================================
# File cache — avoids re-reading /etc/ssh/sshd_config 10 times per scan
# ============================================================================

_file_cache = {}


def _clear_file_cache():
    _file_cache.clear()


def _read_file(path, max_bytes=65536):
    """Read a file from the local filesystem with caching."""
    if path in _file_cache:
        return _file_cache[path]
    # Normalize /host/proc/* paths to /proc/* (KSM runs on host directly)
    if path.startswith("/host/proc/"):
        path = path.replace("/host/proc/", "/proc/", 1)
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                content = f.read(max_bytes)
            _file_cache[path] = content
            return content
    except (PermissionError, OSError):
        pass
    _file_cache[path] = None
    return None


def _run_cmd(command, timeout=10):
    """Run a shell command directly (KSM runs on host, not in Docker)."""
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return (proc.stdout + proc.stderr).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return f"(error: {e})"


# ============================================================================
# Rule Pack Loading
# ============================================================================


def load_rule_pack(pack_path):
    """Read a JSON rule pack file and upsert into compliance_rule_packs."""
    with open(pack_path, "r") as f:
        data = json.load(f)

    pack_id = data["pack_id"]
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO compliance_rule_packs (pack_id, name, rules, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(pack_id) DO UPDATE SET
                   name = excluded.name,
                   rules = excluded.rules,
                   updated_at = excluded.updated_at""",
            (pack_id, data["name"], json.dumps(data["rules"]), datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Loaded rule pack %s (%d rules)", pack_id, len(data["rules"]))
    return {"pack_id": pack_id, "rule_count": len(data["rules"])}


def load_all_packs():
    """Load every JSON file from the rule_packs/ directory."""
    results = []
    if not os.path.isdir(RULE_PACKS_DIR):
        logger.warning("Rule packs directory not found: %s", RULE_PACKS_DIR)
        return results
    for fname in sorted(os.listdir(RULE_PACKS_DIR)):
        if not fname.endswith(".json"):
            continue
        try:
            info = load_rule_pack(os.path.join(RULE_PACKS_DIR, fname))
            results.append(info)
        except Exception as exc:
            logger.error("Failed to load pack %s: %s", fname, exc)
            results.append({"file": fname, "error": str(exc)})
    return results


# ============================================================================
# Scanning
# ============================================================================


def run_scan(pack_ids=None, triggered_by="manual"):
    """Run a full compliance scan against loaded rule packs."""
    scan_id = str(uuid.uuid4())
    started_at = datetime.utcnow().isoformat()
    _clear_file_cache()

    # Load rules from DB
    conn = get_db()
    try:
        if pack_ids:
            placeholders = ",".join("?" for _ in pack_ids)
            rows = conn.execute(
                f"SELECT pack_id, name, rules FROM compliance_rule_packs WHERE pack_id IN ({placeholders})",
                pack_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT pack_id, name, rules FROM compliance_rule_packs"
            ).fetchall()
    finally:
        conn.close()

    packs = [dict(r) for r in rows]
    if not packs:
        return {"scan_id": scan_id, "error": "No rule packs loaded"}

    # Create scan record
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO compliance_scans
               (scan_id, triggered_by, started_at, pack_ids)
               VALUES (?, ?, ?, ?)""",
            (scan_id, triggered_by, started_at,
             json.dumps([p["pack_id"] for p in packs])),
        )
        conn.commit()
    finally:
        conn.close()

    total = 0
    passed = 0
    failed = 0
    errors = 0
    pack_scores = {}

    for pack in packs:
        pack_id = pack["pack_id"]
        rules = json.loads(pack["rules"]) if isinstance(pack["rules"], str) else pack["rules"]
        pack_pass = 0
        pack_total = 0

        for rule in rules:
            total += 1
            pack_total += 1
            rule_id = rule.get("rule_id", f"unknown-{total}")

            try:
                result = _execute_check(rule.get("check", {}))
            except Exception as exc:
                logger.error("Check %s failed: %s", rule_id, exc)
                result = {
                    "status": "error",
                    "actual": "",
                    "expected": rule.get("check", {}).get("expected", ""),
                    "details": str(exc),
                }

            status = result.get("status", "error")
            if status == "pass":
                passed += 1
                pack_pass += 1
            elif status == "fail":
                failed += 1
            else:
                errors += 1

            # Store individual result
            try:
                soc2 = rule.get("soc2_mapping", [])
                conn = get_db()
                try:
                    conn.execute(
                        """INSERT INTO compliance_scan_results
                           (scan_id, pack_id, rule_id, severity, status,
                            actual_value, expected_value, details, soc2_controls)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            scan_id, pack_id, rule_id,
                            rule.get("severity", "medium"), status,
                            result.get("actual", "")[:2000],
                            result.get("expected", "")[:2000],
                            result.get("details", ""),
                            json.dumps(soc2) if soc2 else None,
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:
                logger.error("Failed to store result for %s: %s", rule_id, exc)

        pack_scores[pack_id] = {
            "name": pack["name"],
            "passed": pack_pass,
            "total": pack_total,
            "score": round((pack_pass / pack_total) * 100, 1) if pack_total > 0 else 0,
        }

    # Calculate overall score
    score = round((passed / total) * 100, 1) if total > 0 else 0
    completed_at = datetime.utcnow().isoformat()

    # Update scan record
    conn = get_db()
    try:
        conn.execute(
            """UPDATE compliance_scans SET
               completed_at = ?, total_rules = ?, passed = ?,
               failed = ?, errors = ?, score = ?
               WHERE scan_id = ?""",
            (completed_at, total, passed, failed, errors, score, scan_id),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "scan_id": scan_id,
        "status": "completed",
        "score": score,
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "duration_seconds": round(
            (datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)).total_seconds(), 2
        ),
        "pack_scores": pack_scores,
    }


# ============================================================================
# Check Dispatching
# ============================================================================

_CHECK_HANDLERS = {}


def _execute_check(check):
    check_type = check.get("type", "")
    handler = _CHECK_HANDLERS.get(check_type)
    if handler is None:
        return {
            "status": "error", "actual": "", "expected": check.get("expected", ""),
            "details": f"Unknown check type: {check_type}",
        }
    return handler(check)


def _register_check(check_type):
    def decorator(fn):
        _CHECK_HANDLERS[check_type] = fn
        return fn
    return decorator


# ============================================================================
# Check Type Handlers
# ============================================================================


@_register_check("file_contains")
def _check_file_contains(check):
    path = check.get("path", "")
    expected = check.get("expected", "")
    match_line = check.get("match_line", "")
    try:
        content = _read_file(path)
        if content is None:
            return {"status": "fail", "actual": "(file not found)", "expected": expected,
                    "details": f"{path} does not exist"}
        if match_line:
            found = any(match_line in line and expected in line for line in content.splitlines())
            actual = "found" if found else "not found on matching line"
        else:
            found = expected in content
            actual = "found" if found else "not found"
        return {"status": "pass" if found else "fail", "actual": actual, "expected": expected,
                "details": "" if found else f"Pattern '{expected}' not found in {path}"}
    except Exception as exc:
        return {"status": "error", "actual": "", "expected": expected, "details": str(exc)}


@_register_check("file_content")
def _check_file_content(check):
    path = check.get("path", "")
    pattern = check.get("pattern", "")
    expected = check.get("expected", "")
    try:
        content = _read_file(path)
        if content is None:
            return {"status": "fail", "actual": "(file not found)", "expected": expected,
                    "details": f"{path} does not exist"}
        matched = [line.strip() for line in content.splitlines() if re.search(pattern, line)]
        actual = "\n".join(matched)
        if not matched:
            return {"status": "fail", "actual": "(not found)", "expected": expected,
                    "details": f"Pattern '{pattern}' not found in {path}"}
        if expected and expected in actual:
            return {"status": "pass", "actual": actual, "expected": expected, "details": ""}
        elif not expected:
            return {"status": "pass", "actual": actual, "expected": pattern, "details": ""}
        return {"status": "fail", "actual": actual, "expected": expected,
                "details": "Pattern found but value mismatch"}
    except Exception as exc:
        return {"status": "error", "actual": "", "expected": expected, "details": str(exc)}


@_register_check("command_output")
def _check_command_output(check):
    command = check.get("command", "")
    expected = check.get("expected", "")
    match_pattern = check.get("match_pattern", "")
    try:
        actual = _run_cmd(command, timeout=check.get("timeout", 10))
        if match_pattern:
            ok = bool(re.search(match_pattern, actual))
        elif expected:
            ok = expected in actual
        else:
            ok = len(actual) > 0
        return {"status": "pass" if ok else "fail", "actual": actual[:500],
                "expected": expected or match_pattern,
                "details": "" if ok else "Output does not match expected pattern"}
    except Exception as exc:
        return {"status": "error", "actual": "", "expected": expected or match_pattern,
                "details": str(exc)}


@_register_check("file_permission")
def _check_file_permission(check):
    import grp
    import pwd

    path = check.get("path", "")
    expected_mode = check.get("expected_mode", "")
    expected_owner = check.get("expected_owner", "")
    expected_group = check.get("expected_group", "")
    expected = check.get("expected", f"{expected_mode} {expected_owner} {expected_group}".strip())
    try:
        if not os.path.exists(path):
            return {"status": "fail", "actual": "(file not found)", "expected": expected,
                    "details": f"{path} does not exist"}
        st = os.stat(path)
        mode = oct(st.st_mode & 0o7777).replace("0o", "").zfill(4)
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        try:
            group = grp.getgrgid(st.st_gid).gr_name
        except KeyError:
            group = str(st.st_gid)

        actual = f"{mode} {owner} {group}"
        ok = True
        issues = []
        if expected_mode and mode.lstrip("0") != expected_mode.lstrip("0"):
            ok = False
            issues.append(f"mode {mode} != {expected_mode}")
        if expected_owner and owner != expected_owner:
            ok = False
            issues.append(f"owner {owner} != {expected_owner}")
        if expected_group and group != expected_group:
            ok = False
            issues.append(f"group {group} != {expected_group}")
        return {"status": "pass" if ok else "fail", "actual": actual, "expected": expected,
                "details": "; ".join(issues) if issues else ""}
    except Exception as exc:
        return {"status": "error", "actual": "", "expected": expected, "details": str(exc)}


@_register_check("service_status")
def _check_service_status(check):
    service = check.get("service", "")
    expected_enabled = check.get("expected_enabled", True)
    expected_active = check.get("expected_active", True)
    expected = check.get("expected", "enabled/active" if expected_enabled and expected_active else "")
    try:
        enabled = _run_cmd(f"systemctl is-enabled {service} 2>/dev/null", timeout=5)
        active = _run_cmd(f"systemctl is-active {service} 2>/dev/null", timeout=5)
        actual = f"{enabled}/{active}"

        ok = True
        issues = []
        if expected_enabled and enabled != "enabled":
            ok = False
            issues.append(f"not enabled ({enabled})")
        if not expected_enabled and enabled == "enabled":
            ok = False
            issues.append("should not be enabled")
        if expected_active and active != "active":
            ok = False
            issues.append(f"not active ({active})")
        if not expected_active and active == "active":
            ok = False
            issues.append("should not be active")
        return {"status": "pass" if ok else "fail", "actual": actual, "expected": expected,
                "details": "; ".join(issues) if issues else ""}
    except Exception as exc:
        return {"status": "error", "actual": "", "expected": expected, "details": str(exc)}


@_register_check("sysctl_value")
def _check_sysctl_value(check):
    param = check.get("param", "")
    expected = check.get("expected", "")
    try:
        # Try /proc/sys first, fall back to sysctl command
        proc_path = "/proc/sys/" + param.replace(".", "/")
        try:
            with open(proc_path, "r") as f:
                actual = f.read().strip()
        except (FileNotFoundError, PermissionError):
            actual = _run_cmd(f"sysctl -n {param} 2>/dev/null", timeout=5)
        ok = actual.strip() == expected.strip()
        return {"status": "pass" if ok else "fail", "actual": actual.strip(), "expected": expected,
                "details": "" if ok else f"Kernel param {param} mismatch"}
    except Exception as exc:
        return {"status": "error", "actual": "", "expected": expected, "details": str(exc)}


@_register_check("package_installed")
def _check_package_installed(check):
    package = check.get("package", "")
    should_be_installed = check.get("should_be_installed", True)
    expected = "installed" if should_be_installed else "not installed"
    try:
        out = _run_cmd(f"dpkg-query -W -f='${{Status}}' {package} 2>/dev/null", timeout=5)
        is_installed = "install ok installed" in out
        ok = is_installed == should_be_installed
        return {"status": "pass" if ok else "fail",
                "actual": "installed" if is_installed else "not installed",
                "expected": expected, "details": ""}
    except Exception as exc:
        return {"status": "error", "actual": "", "expected": expected, "details": str(exc)}


@_register_check("docker_config")
def _check_docker_config(check):
    config_key = check.get("config_key", "")
    expected = check.get("expected", "")
    try:
        content = _read_file("/etc/docker/daemon.json")
        if content is None:
            return {"status": "error", "actual": "(daemon.json not found)", "expected": expected,
                    "details": "/etc/docker/daemon.json does not exist"}
        try:
            cfg = json.loads(content)
        except json.JSONDecodeError:
            return {"status": "error", "actual": content[:200], "expected": expected,
                    "details": "Failed to parse daemon.json"}
        raw_val = cfg.get(config_key, "(not set)")
        actual = json.dumps(raw_val) if isinstance(raw_val, (dict, list)) else str(raw_val)
        ok = expected in actual if expected else actual != "(not set)"
        if not ok and expected:
            ok = expected.lower() in actual.lower()
        return {"status": "pass" if ok else "fail", "actual": actual[:500], "expected": expected,
                "details": "" if ok else f"Docker config mismatch for {config_key}"}
    except Exception as exc:
        return {"status": "error", "actual": "", "expected": expected, "details": str(exc)}


@_register_check("file_exists")
def _check_file_exists(check):
    path = check.get("path", "")
    if path.startswith("/host/proc/"):
        path = path.replace("/host/proc/", "/proc/", 1)
    should_exist = check.get("should_exist", True)
    expected = "exists" if should_exist else "missing"
    try:
        exists = os.path.exists(path)
        actual = "exists" if exists else "missing"
        ok = exists == should_exist
        return {"status": "pass" if ok else "fail", "actual": actual, "expected": expected, "details": ""}
    except Exception as exc:
        return {"status": "error", "actual": "", "expected": expected, "details": str(exc)}


@_register_check("firewall_rule")
def _check_firewall_rule(check):
    pattern = check.get("pattern", "")
    expected = check.get("expected", "ALLOW")
    should_exist = check.get("should_exist", True)
    try:
        output = _run_cmd("ufw status", timeout=5)
        matched = [line for line in output.splitlines() if re.search(pattern, line)]
        actual = "\n".join(matched).strip()
        found = len(matched) > 0
        ok = found == should_exist
        if found and expected:
            ok = ok and expected in actual
        return {"status": "pass" if ok else "fail",
                "actual": actual if actual else "(no matching rule)",
                "expected": f"{'Rule exists' if should_exist else 'No rule'}: {pattern}",
                "details": ""}
    except Exception as exc:
        return {"status": "error", "actual": "", "expected": expected, "details": str(exc)}


@_register_check("port_listening")
def _check_port_listening(check):
    port = str(check.get("port", ""))
    should_listen = check.get("should_listen", True)
    expected = f"port {port} {'listening' if should_listen else 'not listening'}"
    try:
        output = _run_cmd(f"ss -tlnp | grep ':{port} '", timeout=5)
        is_listening = bool(output.strip()) and "(error:" not in output
        actual = output.strip() if output.strip() else f"port {port} not listening"
        ok = is_listening == should_listen
        return {"status": "pass" if ok else "fail", "actual": actual, "expected": expected, "details": ""}
    except Exception as exc:
        return {"status": "error", "actual": "", "expected": expected, "details": str(exc)}


# ============================================================================
# Query Functions
# ============================================================================


def get_scan_results(scan_id, status=None, severity=None, pack_id=None):
    """Query scan results with optional filters."""
    query = "SELECT * FROM compliance_scan_results WHERE scan_id = ?"
    params = [scan_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    if severity:
        query += " AND severity = ?"
        params.append(severity)
    if pack_id:
        query += " AND pack_id = ?"
        params.append(pack_id)
    query += (" ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1"
              " WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, rule_id")

    conn = get_db()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_scan_history(limit=20):
    """Recent scans ordered by date."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT scan_id, triggered_by, started_at, completed_at,
                      total_rules, passed, failed, errors, score, pack_ids
               FROM compliance_scans ORDER BY started_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_latest_scores():
    """Get the most recent completed scan's scores with per-pack breakdown."""
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT scan_id, score, total_rules, passed, failed, errors,
                      started_at, completed_at, pack_ids
               FROM compliance_scans WHERE completed_at IS NOT NULL
               ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        if not row:
            return None

        result = dict(row)
        if result.get("score") is not None:
            result["score"] = float(result["score"])
        for k in ("total_rules", "passed", "failed", "errors"):
            if result.get(k) is not None:
                result[k] = int(result[k])

        scan_id = result["scan_id"]

        # Per-pack breakdown
        pack_rows = conn.execute(
            """SELECT r.pack_id, p.name,
                      COUNT(*) AS total,
                      SUM(CASE WHEN r.status = 'pass' THEN 1 ELSE 0 END) AS passed
               FROM compliance_scan_results r
               LEFT JOIN compliance_rule_packs p ON p.pack_id = r.pack_id
               WHERE r.scan_id = ?
               GROUP BY r.pack_id, p.name""",
            (scan_id,),
        ).fetchall()

        packs = {}
        pack_scores = {}
        for pr in pack_rows:
            pr = dict(pr)
            pid = pr["pack_id"]
            pname = pr.get("name") or pid
            pt = int(pr["total"] or 0)
            pp = int(pr["passed"] or 0)
            sc = round((pp / pt) * 100, 1) if pt > 0 else 0

            short_key = pname.lower()
            for k in ("ubuntu", "docker", "network", "hipaa", "cis"):
                if k in short_key:
                    short_key = k
                    break
            packs[short_key] = sc
            pack_scores[pid] = {"name": pname, "score": sc, "passed": pp, "total": pt}

        result["packs"] = packs
        result["pack_scores"] = pack_scores
        result["last_scan"] = result.get("completed_at") or result.get("started_at")
        result["latest_scan_id"] = scan_id

        # SOC 2 scores
        result["soc2"] = _compute_soc2_scores(conn, scan_id)
        return result
    finally:
        conn.close()


def _compute_soc2_scores(conn, scan_id):
    """Compute SOC 2 Trust Service Criteria scores from soc2_controls mappings."""
    rows = conn.execute(
        """SELECT soc2_controls, status FROM compliance_scan_results
           WHERE scan_id = ? AND soc2_controls IS NOT NULL""",
        (scan_id,),
    ).fetchall()

    # SQLite stores soc2_controls as JSON text; aggregate manually
    controls = {}
    for r in rows:
        ctrls = json.loads(r["soc2_controls"]) if r["soc2_controls"] else []
        for ctrl in ctrls:
            if ctrl not in controls:
                controls[ctrl] = {"total": 0, "passed": 0}
            controls[ctrl]["total"] += 1
            if r["status"] == "pass":
                controls[ctrl]["passed"] += 1

    for ctrl in controls:
        t = controls[ctrl]["total"]
        p = controls[ctrl]["passed"]
        controls[ctrl]["score"] = round((p / t) * 100, 1) if t > 0 else 0

    tsc_map = {
        "Security": ["CC6.1", "CC6.2", "CC6.3", "CC6.6", "CC6.7"],
        "Availability": ["CC7.1", "CC7.2", "CC7.3", "CC7.4", "A1.1", "A1.2"],
        "Processing Integrity": ["CC8.1", "PI1.3"],
        "Confidentiality": ["C1.1", "C1.2"],
        "Privacy": ["P1.1", "P1.2"],
    }

    criteria = {}
    total_score = 0
    total_weight = 0
    for tsc_name, ctrl_ids in tsc_map.items():
        tsc_total = 0
        tsc_passed = 0
        for cid in ctrl_ids:
            if cid in controls:
                tsc_total += controls[cid]["total"]
                tsc_passed += controls[cid]["passed"]
        score = round((tsc_passed / tsc_total) * 100, 1) if tsc_total > 0 else None
        criteria[tsc_name] = {
            "score": score, "passed": tsc_passed, "total": tsc_total,
            "controls": [
                {"id": cid, **(controls[cid] if cid in controls else {"total": 0, "passed": 0, "score": 0})}
                for cid in ctrl_ids
            ],
        }
        if score is not None:
            total_score += score
            total_weight += 1

    overall = round(total_score / total_weight, 1) if total_weight > 0 else 0

    # Evidence freshness
    evidence_rows = conn.execute(
        """SELECT artifact_type, MAX(collected_at) as last_collected
           FROM compliance_evidence GROUP BY artifact_type"""
    ).fetchall()
    evidence_freshness = {}
    for er in evidence_rows:
        last = er["last_collected"]
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                age_hours = (datetime.utcnow() - last_dt).total_seconds() / 3600
                evidence_freshness[er["artifact_type"]] = {
                    "last_collected": last, "age_hours": round(age_hours, 1),
                    "fresh": age_hours < 720,
                }
            except (ValueError, TypeError):
                pass

    return {"score": overall, "criteria": criteria, "controls": controls,
            "evidence_freshness": evidence_freshness}


def get_soc2_scores(scan_id=None):
    """Public wrapper for SOC 2 scores."""
    conn = get_db()
    try:
        if not scan_id:
            row = conn.execute(
                "SELECT scan_id FROM compliance_scans WHERE completed_at IS NOT NULL ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            scan_id = row["scan_id"]
        return _compute_soc2_scores(conn, scan_id)
    finally:
        conn.close()


# ============================================================================
# Evidence Collection
# ============================================================================


def collect_evidence(scan_id=None):
    """Auto-collect SOC 2 evidence artifacts from host systems."""
    evidence = []

    # Firewall config
    try:
        fw_output = _run_cmd("ufw status verbose", timeout=10)
        evidence.append({
            "control_id": "CC6.6", "artifact_type": "firewall_config",
            "artifact_name": "UFW Firewall Status", "artifact_data": fw_output,
        })
    except Exception as e:
        logger.warning("Evidence: firewall failed: %s", e)

    # User accounts
    try:
        users_output = _run_cmd("cat /etc/passwd | grep -v nologin | grep -v /bin/false", timeout=5)
        evidence.append({
            "control_id": "CC6.1", "artifact_type": "access_control_config",
            "artifact_name": "System User Accounts", "artifact_data": users_output,
        })
    except Exception as e:
        logger.warning("Evidence: user accounts failed: %s", e)

    # SSH config
    try:
        ssh_config = _read_file("/etc/ssh/sshd_config")
        if ssh_config:
            evidence.append({
                "control_id": "CC6.1", "artifact_type": "ssh_config",
                "artifact_name": "SSH Server Configuration", "artifact_data": ssh_config[:4000],
            })
    except Exception as e:
        logger.warning("Evidence: SSH config failed: %s", e)

    # Listening ports
    try:
        ports_output = _run_cmd("ss -tlnp", timeout=5)
        evidence.append({
            "control_id": "CC6.6", "artifact_type": "network_ports",
            "artifact_name": "Listening Network Ports", "artifact_data": ports_output,
        })
    except Exception as e:
        logger.warning("Evidence: ports failed: %s", e)

    # Docker info
    try:
        docker_output = _run_cmd("docker info --format json 2>/dev/null", timeout=10)
        evidence.append({
            "control_id": "CC6.1", "artifact_type": "docker_config",
            "artifact_name": "Docker Engine Configuration", "artifact_data": docker_output[:4000],
        })
    except Exception as e:
        logger.warning("Evidence: docker failed: %s", e)

    # Store evidence in DB
    conn = get_db()
    try:
        for item in evidence:
            data = item["artifact_data"]
            if not isinstance(data, str):
                data = json.dumps(data)
            conn.execute(
                """INSERT INTO compliance_evidence
                   (control_id, artifact_type, artifact_name, artifact_data, collected_at, scan_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (item["control_id"], item["artifact_type"], item["artifact_name"],
                 data, datetime.utcnow().isoformat(), scan_id),
            )
        conn.commit()
    finally:
        conn.close()

    return {"collected": len(evidence), "artifacts": [
        {"control_id": e["control_id"], "type": e["artifact_type"], "name": e["artifact_name"]}
        for e in evidence
    ]}
