"""GPU telemetry-health recording in the background metrics collector.

REAL INCIDENT THIS PINS (task #5595): both hub GPUs hit NVRM Xid 79 on
2026-08-04 and were dead for 8 days while every dashboard reported healthy.

lspci still lists a card that has fallen off the PCIe bus, so host_monitor kept
emitting BOTH devices -- with every telemetry field None. This collector then
recorded a metric only when the value was non-None:

    if util_pct is not None:
        record_metric(f"gpu_{idx}", util_pct)

so a blind GPU wrote NO ROWS AT ALL. In system_metrics_history that is
byte-identical to "this machine has no GPU" and to "the collector is not
running". The absence of a metric was carrying no information, and nothing could
alert on it.

host_monitor now publishes a per-GPU `telemetry_ok`; these tests pin that the
collector turns it into a POSITIVE row every cycle, so "present but unreadable"
becomes queryable instead of silent.
"""

import contextlib
import sys

import pytest

from kytran_system_operations.services import metrics_collector as mc


class _FakeApp:
    def app_context(self):
        return contextlib.nullcontext()


def _gpu(pci, model, telemetry_ok, util=None, vram_total=None, vram_used=None):
    return {
        "pci_address": pci,
        "vendor": "nvidia" if telemetry_ok is not None else "amd",
        "model": model,
        "utilization_percent": util,
        "vram_total_mb": vram_total,
        "vram_used_mb": vram_used,
        "telemetry_ok": telemetry_ok,
    }


@pytest.fixture
def recorded(monkeypatch):
    """Capture every record_metric call the collector makes."""
    calls = []

    import kytran_system_operations.routes.helpers as helpers
    import kytran_system_operations.routes.system_service as svc

    monkeypatch.setattr(helpers, "record_metric", lambda k, v: calls.append((k, v)))

    class _Svc:
        def get_overview(self):
            return {"cpu": {"usage_percent": 5.0}, "memory": {"usage_percent": 10.0}}

    monkeypatch.setattr(svc, "get_system_service", lambda: _Svc())
    return calls


def _run(monkeypatch, gpus):
    import kytran_system_operations.routes.helpers as helpers

    monkeypatch.setattr(
        helpers, "load_host_monitor_data", lambda: ({"gpu": gpus}, 10)
    )
    mc._collect_once(_FakeApp())


def test_dead_gpus_record_a_positive_zero_not_silence(monkeypatch, recorded):
    """THE #5595 CASE. Both cards present, neither readable."""
    _run(
        monkeypatch,
        [
            _gpu("04:00.0", "Quadro M4000", telemetry_ok=False),
            _gpu("05:00.0", "TITAN X", telemetry_ok=False),
        ],
    )

    keys = dict(recorded)
    assert keys["gpu_telemetry_ok_0"] == 0.0
    assert keys["gpu_telemetry_ok_1"] == 0.0
    assert keys["gpu_telemetry_ok"] == 0.0, "idx 0 also writes the unsuffixed series"
    # The util series is legitimately absent -- the flag is what explains WHY.
    assert "gpu_0" not in keys and "gpu_1" not in keys


def test_healthy_gpus_record_one_and_still_record_utilisation(monkeypatch, recorded):
    _run(
        monkeypatch,
        [
            _gpu("04:00.0", "Quadro M4000", True, util=3, vram_total=8192, vram_used=512),
            _gpu("05:00.0", "TITAN X", True, util=82, vram_total=12288, vram_used=4096),
        ],
    )

    keys = dict(recorded)
    assert keys["gpu_telemetry_ok_0"] == 1.0
    assert keys["gpu_telemetry_ok_1"] == 1.0
    assert keys["gpu_0"] == 3
    assert keys["gpu_1"] == 82


def test_mixed_state_flags_only_the_dead_card(monkeypatch, recorded):
    """The two hub cards died 12 minutes apart -- this state was real."""
    _run(
        monkeypatch,
        [
            _gpu("04:00.0", "Quadro M4000", True, util=3, vram_total=8192, vram_used=512),
            _gpu("05:00.0", "TITAN X", telemetry_ok=False),
        ],
    )

    keys = dict(recorded)
    assert keys["gpu_telemetry_ok_0"] == 1.0
    assert keys["gpu_telemetry_ok_1"] == 0.0
    assert keys["gpu_0"] == 3, "the surviving card must stay observable"


def test_non_nvidia_records_no_health_row(monkeypatch, recorded):
    """None = not applicable. Recording 0 here would alarm forever on iGPU boxes."""
    _run(monkeypatch, [_gpu("07:00.0", "AMD Cezanne", telemetry_ok=None)])

    keys = dict(recorded)
    assert not any(k.startswith("gpu_telemetry_ok") for k in keys)


def test_legacy_host_monitor_payload_without_the_key_is_safe(monkeypatch, recorded):
    """Old host_monitor output predates telemetry_ok — must not crash or invent."""
    legacy = {
        "pci_address": "04:00.0",
        "vendor": "nvidia",
        "model": "Quadro M4000",
        "utilization_percent": 7,
        "vram_total_mb": 8192,
        "vram_used_mb": 1024,
    }
    _run(monkeypatch, [legacy])

    keys = dict(recorded)
    assert not any(k.startswith("gpu_telemetry_ok") for k in keys)
    assert keys["gpu_0"] == 7, "legacy payloads keep working exactly as before"
