"""
Host Command Client — Container-side interface to the host command queue.

This module provides functions to submit commands to the host_monitor.py
command queue (via shared filesystem) and poll for results.

The host_monitor.py daemon (running as root on the host) picks up pending
commands, validates them, executes whitelisted operations, and writes results.
"""

import json
import os
import time
import uuid
from datetime import datetime

BASE_DIR = os.environ.get("KSM_BASE_DIR", "/")
COMMANDS_DIR = os.path.join(BASE_DIR, "host_commands")
PENDING_DIR = os.path.join(COMMANDS_DIR, "pending")
COMPLETED_DIR = os.path.join(COMMANDS_DIR, "completed")


# ============================================================================
# EXCEPTIONS
# ============================================================================


class HostCommandError(Exception):
    """Base error for host command operations"""

    pass


class HostCommandTimeout(HostCommandError):
    """Command was submitted but no result within timeout"""

    pass


class HostCommandQueueUnavailable(HostCommandError):
    """Command queue directories are not available"""

    pass


# ============================================================================
# QUEUE STATUS
# ============================================================================


def is_queue_available():
    """Check if the host command queue directories exist and are writable."""
    return os.path.isdir(PENDING_DIR) and os.path.isdir(COMPLETED_DIR) and os.access(PENDING_DIR, os.W_OK)


def get_queue_status():
    """Get current queue status counts."""
    status = {
        "available": is_queue_available(),
        "pending_count": 0,
        "completed_count": 0,
    }
    try:
        if os.path.isdir(PENDING_DIR):
            status["pending_count"] = len([f for f in os.listdir(PENDING_DIR) if f.endswith(".json")])
        if os.path.isdir(COMPLETED_DIR):
            status["completed_count"] = len([f for f in os.listdir(COMPLETED_DIR) if f.endswith(".json")])
    except OSError:
        pass
    return status


# ============================================================================
# SUBMIT & POLL
# ============================================================================


def submit_host_command(command_type, params, submitted_by="platform", user_id=None):
    """Submit a command to the host command queue.

    Args:
        command_type: One of the registered command types (e.g. 'lvm_extend')
        params: Dict of parameters for the command
        submitted_by: Identifier of the submitter
        user_id: Optional user ID for audit trail

    Returns:
        str: The command UUID

    Raises:
        HostCommandQueueUnavailable: If queue dirs don't exist
    """
    if not is_queue_available():
        raise HostCommandQueueUnavailable("Host command queue is not available. Is host_monitor.py running?")

    command_id = str(uuid.uuid4())
    command_data = {
        "command_id": command_id,
        "command_type": command_type,
        "params": params,
        "submitted_by": submitted_by,
        "user_id": user_id,
        "submitted_at": datetime.now().isoformat(),
    }

    pending_path = os.path.join(PENDING_DIR, f"{command_id}.json")
    with open(pending_path, "w") as f:
        json.dump(command_data, f, indent=2)

    # Make readable by host_monitor (which runs as root, so this is just courtesy)
    try:
        os.chmod(pending_path, 0o666)
    except OSError:
        pass

    return command_id


def poll_result(command_id, timeout=60, poll_interval=1):
    """Poll for a command result.

    Args:
        command_id: UUID of the command to check
        timeout: Max seconds to wait
        poll_interval: Seconds between checks

    Returns:
        dict: The result data from the completed command

    Raises:
        HostCommandTimeout: If no result within timeout
    """
    result_path = os.path.join(COMPLETED_DIR, f"{command_id}.json")
    deadline = time.time() + timeout

    while time.time() < deadline:
        if os.path.exists(result_path):
            try:
                with open(result_path, "r") as f:
                    data = json.load(f)
                return data
            except (json.JSONDecodeError, OSError):
                # File might be partially written, retry
                time.sleep(0.2)
                continue

        time.sleep(poll_interval)

    raise HostCommandTimeout(
        f"No result for command {command_id} within {timeout}s. " "The host_monitor.py daemon may not be running."
    )


def submit_and_wait(command_type, params, timeout=60, submitted_by="platform", user_id=None):
    """Submit a command and wait for its result.

    Args:
        command_type: Command type string
        params: Command parameters dict
        timeout: Max seconds to wait for result
        submitted_by: Submitter identifier
        user_id: Optional user ID

    Returns:
        dict: The result data from the completed command

    Raises:
        HostCommandQueueUnavailable: If queue not available
        HostCommandTimeout: If no result within timeout
    """
    command_id = submit_host_command(command_type, params, submitted_by=submitted_by, user_id=user_id)
    return poll_result(command_id, timeout=timeout)


def get_result(command_id):
    """Get the result of a previously submitted command (non-blocking).

    Returns:
        dict or None: The result data if available, None if still pending
    """
    result_path = os.path.join(COMPLETED_DIR, f"{command_id}.json")
    if not os.path.exists(result_path):
        return None
    try:
        with open(result_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
