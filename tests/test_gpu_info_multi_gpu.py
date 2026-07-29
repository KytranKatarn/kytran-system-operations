"""get_gpu_info() must report EVERY GPU, not just the first (#5399).

nvidia-smi emits one line per GPU. The old implementation did
``result.stdout.strip().split(",")`` across the WHOLE output, so on the hub's
two-card host it produced 11 fields where the code indexed 6:

  * every card after the first vanished,
  * ``vram_mb`` reported 8192 (the Quadro) instead of describing 2 cards,
  * ``driver`` silently absorbed the next card's NAME:
        '535.309.01\\nNVIDIA TITAN X (Pascal)'

These tests drive get_gpu_info() with subprocess.run mocked, so they run against
the OLD implementation too — a test that only exercised the new helper would
merely raise AttributeError on the old code, which proves nothing about parsing.

REAL two-card output, captured from the hub 2026-07-29.
"""

from unittest.mock import patch

from kytran_system_operations.system_service import get_system_service

TWO_GPU = (
    "Quadro M4000, 8192, 4, 0, 38, 535.309.01\n"
    "NVIDIA TITAN X (Pascal), 12288, 6612, 0, 45, 535.309.01\n"
)
ONE_GPU = "Quadro M4000, 8192, 4, 0, 38, 535.309.01\n"


class _Result:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


def _gpu_info(stdout):
    with patch("kytran_system_operations.system_service.subprocess.run",
               return_value=_Result(stdout)):
        return get_system_service().get_gpu_info()


def test_both_gpus_are_reported():
    info = _gpu_info(TWO_GPU)
    assert info["available"] is True
    assert info["gpu_count"] == 2, "second card was dropped — the #5399 regression"
    models = [g["model"] for g in info["gpus"]]
    assert models == ["Quadro M4000", "NVIDIA TITAN X (Pascal)"]


def test_driver_is_not_polluted_by_the_next_card():
    """The signature failure: driver absorbed the second card's name."""
    info = _gpu_info(TWO_GPU)
    assert info["driver"] == "535.309.01"
    assert "\n" not in (info["driver"] or "")
    assert "TITAN" not in (info["driver"] or "")
    for g in info["gpus"]:
        assert g["driver"] == "535.309.01"


def test_vram_is_the_first_card_never_the_sum():
    """A VRAM figure anything sizes a model against must mean ONE card.

    The two cards are compute capability 6.1 and 5.2, so a model cannot span
    them — reporting 20480 would tell a router a model fits in a card that
    cannot hold it (.claude/rules/fleet.md).
    """
    info = _gpu_info(TWO_GPU)
    assert info["vram_mb"] == 8192
    assert info["vram_mb"] != 8192 + 12288
    assert info["gpus"][1]["vram_mb"] == 12288


def test_per_card_fields_are_parsed():
    info = _gpu_info(TWO_GPU)
    titan = info["gpus"][1]
    assert titan["vram_used_mb"] == 6612
    assert titan["temperature"] == 45
    assert titan["index"] == 1


def test_single_gpu_still_works():
    """Regression guard — a single-card host must be unaffected."""
    info = _gpu_info(ONE_GPU)
    assert info["available"] is True
    assert info["gpu_count"] == 1
    assert info["model"] == "Quadro M4000"
    assert info["vram_mb"] == 8192
    assert info["driver"] == "535.309.01"


def test_no_gpu_reports_unavailable():
    info = _gpu_info("")
    assert info["available"] is False
    assert info["gpu_count"] == 0
    assert info["gpus"] == []
