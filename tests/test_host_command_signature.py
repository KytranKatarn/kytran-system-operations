"""Contract for the host-command HMAC gate (Full Security Review Tier-0).

Privileged compliance_fix_* commands must carry an HMAC so a file dropped into
the shared host-command queue cannot trigger a root operation. The signing
algorithm lives in two places (container client + host daemon) that MUST agree;
these tests lock the algorithm and guard that host_monitor.py stays in sync.
"""

import hashlib
import hmac
import json
import os
import re
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kytran_system_operations.services.host_command_client import (  # noqa: E402
    SIGNED_COMMAND_TYPES,
    command_signature,
)


def _find_host_monitor():
    candidates = [
        "/mnt/archie_brain/scripts/host_monitor.py",
        os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "host_monitor.py"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


_HOST_MONITOR = _find_host_monitor()


def _reference(command_type, params, secret):
    msg = command_type + ":" + json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def test_signature_matches_reference_algorithm():
    sig = command_signature("compliance_fix_command", {"command": "echo hi"}, "sekret")
    assert sig == _reference("compliance_fix_command", {"command": "echo hi"}, "sekret")


def test_signature_is_deterministic_and_order_independent():
    a = command_signature("compliance_fix_file_line", {"path": "/etc/x", "line": "y"}, "k")
    b = command_signature("compliance_fix_file_line", {"line": "y", "path": "/etc/x"}, "k")
    assert a == b  # canonical sort_keys makes param order irrelevant


def test_signature_sensitive_to_command_type():
    assert command_signature("compliance_fix_command", {"command": "x"}, "k") != command_signature(
        "compliance_fix_service", {"command": "x"}, "k"
    )


def test_signature_sensitive_to_params():
    assert command_signature("compliance_fix_command", {"command": "reboot"}, "k") != command_signature(
        "compliance_fix_command", {"command": "rm -rf /"}, "k"
    )


def test_signature_sensitive_to_secret():
    assert command_signature("compliance_fix_command", {"command": "x"}, "k1") != command_signature(
        "compliance_fix_command", {"command": "x"}, "k2"
    )


def test_all_privileged_compliance_types_are_signed():
    # Every host handler that runs a privileged (sudo) mutation must be gated.
    for t in ("compliance_fix_command", "compliance_fix_sysctl", "compliance_fix_file_line", "compliance_fix_service"):
        assert t in SIGNED_COMMAND_TYPES


def test_hub_deploy_is_gated_like_a_privileged_command():
    """Hub auto-deploy (git pull + container restart as root) is a privileged
    mutation — it must be HMAC-signed exactly like the compliance_fix_* family."""
    assert "hub_deploy" in SIGNED_COMMAND_TYPES
    sig = command_signature("hub_deploy", {}, "sekret")
    assert sig == _reference("hub_deploy", {}, "sekret")
    # Empty params are the canonical payload; a non-empty/different payload differs.
    assert sig != command_signature("hub_deploy", {"dry_run": True}, "sekret")


def test_hub_deploy_handler_and_registry_exist_on_host():
    import pytest

    if not _HOST_MONITOR:
        pytest.skip("host_monitor.py not reachable in this environment")
    src = open(_HOST_MONITOR).read()
    # Handler defined and dispatchable.
    assert "def handle_hub_deploy" in src
    assert 'handle_hub_deploy": handle_hub_deploy' in src
    # Registry entry names the handler.
    assert '"hub_deploy": {' in src
    # The handler refuses to fast-forward when the tree is diverged (--ff-only) and
    # only then restarts — the safety spine must not drift away.
    assert '"--ff-only"' in src
    assert "origin/main" in src


def test_hub_deploy_restart_is_scoped_to_two_containers():
    import pytest

    if not _HOST_MONITOR:
        pytest.skip("host_monitor.py not reachable in this environment")
    src = open(_HOST_MONITOR).read()
    # The restart must target ONLY the two hub containers, never the whole stack.
    assert '["docker", "compose", "restart", "archie_platform", "archie_scheduler"]' in src


def test_host_monitor_stays_in_sync():
    """host_monitor.py must use the identical canonical message + signed set."""
    import pytest

    if not _HOST_MONITOR:
        pytest.skip("host_monitor.py not reachable in this environment")
    src = open(_HOST_MONITOR).read()
    # Same canonical message construction.
    assert 'command_type + ":" + json.dumps(params or {}, sort_keys=True, separators=(",", ":"))' in src
    # Same signed command-type set.
    for t in SIGNED_COMMAND_TYPES:
        assert f'"{t}"' in src
    # The gate is actually wired into the queue processor.
    assert "_verify_command_signature(cmd_data)" in src
    assert re.search(r"if not sig_ok", src)


# --- a signed command type must never be queued unsigned (#7542) -------------------------------
import pytest  # noqa: E402

from kytran_system_operations.services import host_command_client as hcc  # noqa: E402


@pytest.fixture
def queue(tmp_path, monkeypatch):
    pending = tmp_path / "pending"
    pending.mkdir()
    monkeypatch.setattr(hcc, "PENDING_DIR", str(pending))
    monkeypatch.setattr(hcc, "is_queue_available", lambda: True)
    monkeypatch.delenv("HOST_COMMAND_HMAC_KEY", raising=False)
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    return pending


@pytest.mark.parametrize("command_type", sorted(SIGNED_COMMAND_TYPES))
def test_signed_type_without_a_key_is_refused_not_queued_unsigned(queue, command_type):
    with pytest.raises(hcc.HostCommandError, match="must be signed"):
        hcc.submit_host_command(command_type, {})
    assert list(queue.iterdir()) == []  # nothing was written for the host to reject


def test_signed_type_with_a_key_carries_the_hmac(queue, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "k" * 16)
    cid = hcc.submit_host_command("hub_deploy", {})
    data = json.loads((queue / f"{cid}.json").read_text())
    assert data["hmac"] == command_signature("hub_deploy", {}, "k" * 16)


def test_unsigned_types_still_queue_without_a_key(queue):
    cid = hcc.submit_host_command("lvm_extend", {"x": 1})
    assert "hmac" not in json.loads((queue / f"{cid}.json").read_text())
