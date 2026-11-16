"""Host view for a KSO that runs in a container without the docker CLI (Daystrom): tab title and icon, container list,
detected stacks and the read-only firewall view all come from the host monitor's data file."""

import json
import os

import pytest

from kytran_system_operations import theme as theme_mod


def test_tab_title_and_favicon_can_be_set_per_instance(monkeypatch):
    monkeypatch.delenv("SYSOPS_TAB_TITLE", raising=False)
    monkeypatch.delenv("SYSOPS_FAVICON", raising=False)
    base = theme_mod.load_theme("kytran")
    assert base["tab_title"] is None and base["favicon"].endswith("kytran-logo.png")
    monkeypatch.setenv("SYSOPS_TAB_TITLE", "Daystrom")
    assert theme_mod.load_theme("kytran")["tab_title"] == "Daystrom"
    assert theme_mod.load_theme("no-such-theme")["tab_title"] == "Daystrom"  # a missing theme file keeps the override


def test_the_default_favicon_is_a_file_that_ships():
    root = os.path.dirname(theme_mod.__file__)
    assert os.path.exists(os.path.join(root, "static", "img", "kytran-logo.png"))


HOST = {
    "docker_health": [
        {"name": "network_adguard", "state": "running", "health": "none", "image": "adguard/adguardhome", "compose_project": "archie-adguard"},
        {"name": "archie_node_api", "state": "running", "health": "healthy", "image": "archie/node", "compose_project": "archie-node"},
        {"name": "archie_node_db", "state": "running", "health": "healthy", "image": "pgvector", "compose_project": "archie-node"},
        {"name": "guestfs-ro", "state": "exited", "health": "none", "image": "x", "compose_project": ""},
    ],
    "firewall": {
        "status": {"success": True, "active": True, "output": "Status: active"},
        "rules": {"success": True, "output": "[ 1] 22/tcp                     ALLOW IN    Anywhere\n[ 2] 8085/tcp                   ALLOW IN    100.64.0.0/10\n", "from_config": False},
    },
}


@pytest.fixture()
def host_data(monkeypatch):
    from kytran_system_operations.routes import helpers

    monkeypatch.setattr(helpers, "load_host_monitor_data", lambda: (HOST, 5))
    return HOST


def test_container_list_falls_back_to_the_host_data(host_data):
    from kytran_system_operations.system_service import SystemService

    containers = SystemService()._docker_containers_from_host_data()
    by_name = {c["name"]: c for c in containers}
    assert len(containers) == 4
    assert by_name["archie_node_db"]["status"] == "Up (healthy)" and by_name["archie_node_db"]["running"] is True
    assert by_name["guestfs-ro"]["running"] is False and by_name["guestfs-ro"]["status"] == "Exited"


def test_stacks_are_detected_from_compose_projects_and_never_duplicate_managed_ones(host_data):
    from kytran_system_operations.routes.stack_routes import _detected_stacks

    found = {s["name"]: s for s in _detected_stacks([])}
    assert set(found) == {"archie-adguard", "archie-node"}
    assert found["archie-node"]["containers_total"] == 2 and found["archie-node"]["status"] == "running"
    assert found["archie-node"]["detected"] is True
    assert {s["name"] for s in _detected_stacks([{"name": "archie-node"}])} == {"archie-adguard"}


def test_firewall_view_is_read_only_and_only_when_the_host_collected_it(host_data):
    from kytran_system_operations.routes.firewall_routes import _host_firewall_view

    status = _host_firewall_view("status")
    assert status["active"] is True and status["read_only"] is True
    rules = _host_firewall_view("rules")
    assert [r["port"] for r in rules["rules"]] == ["22/tcp", "8085/tcp"] and rules["read_only"] is True


def test_no_firewall_view_without_host_data(monkeypatch):
    from kytran_system_operations.routes import firewall_routes, helpers

    monkeypatch.setattr(helpers, "load_host_monitor_data", lambda: ({}, None))
    assert firewall_routes._host_firewall_view("status") is None
    assert firewall_routes._host_firewall_view("rules") is None
