"""
Background Metrics Collector
=============================
Daemon thread that continuously samples system metrics (CPU, memory, GPU)
and stores them to system_metrics_history for persistent graph history.

Without this, graphs only have data from when the user last viewed the
dashboard — resetting to the current hour on every page load.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

_COLLECT_INTERVAL = 5 * 60  # 5 minutes between samples
_STARTUP_DELAY = 30          # seconds to wait before first sample

_shutdown_event = threading.Event()
_thread = None


def _collect_once(app):
    """Sample all system metrics and persist them."""
    with app.app_context():
        try:
            from ..routes.system_service import get_system_service
            from ..routes.helpers import record_metric, load_host_monitor_data
        except ImportError:
            # Fallback import path used by some install layouts
            from kytran_system_operations.routes.system_service import get_system_service
            from kytran_system_operations.routes.helpers import record_metric, load_host_monitor_data

        try:
            service = get_system_service()
            data = service.get_overview()

            # Core metrics — always available
            cpu_pct = data.get("cpu", {}).get("usage_percent")
            mem_pct = data.get("memory", {}).get("usage_percent")
            if cpu_pct is not None:
                record_metric("cpu", cpu_pct)
                record_metric("cpu_0", cpu_pct)
            if mem_pct is not None:
                record_metric("memory", mem_pct)

            # GPU metrics from host monitor (container can't see GPU directly)
            host_data, host_age = load_host_monitor_data()
            if host_data and (host_age is None or host_age < 300):
                # Server power (#5140) — total watts lives in host_monitor_data.json,
                # NOT in get_overview()'s data. Record it here like GPU (same source).
                pw = host_data.get("power") or {}
                if isinstance(pw, dict) and pw.get("total_watts") is not None:
                    record_metric("power_total", pw["total_watts"])
                    # Per-component watts history (#5140 v1.1) — stacked breakdown
                    _pmap = {"GPU": "power_gpu", "CPU": "power_cpu", "RAM": "power_ram",
                             "Disks": "power_disks", "Board+fans": "power_board"}
                    for _c in (pw.get("components") or []):
                        _pk = _pmap.get(_c.get("name"))
                        if _pk and _c.get("watts") is not None:
                            record_metric(_pk, _c["watts"])
                host_gpus = host_data.get("gpu") or []
                for idx, gpu in enumerate(host_gpus):
                    vram_total = gpu.get("vram_total_mb")
                    vram_used = gpu.get("vram_used_mb")
                    util_pct = gpu.get("utilization_percent")

                    if util_pct is not None:
                        record_metric(f"gpu_{idx}", util_pct)
                        if idx == 0:
                            record_metric("gpu", util_pct)

                    if vram_total and vram_used is not None and vram_total > 0:
                        vram_pct = round((vram_used / vram_total) * 100, 1)
                        record_metric(f"gpu_vram_{idx}", vram_pct)
                        if idx == 0:
                            record_metric("gpu_vram", vram_pct)

            logger.debug(
                "Metrics collected — cpu=%.1f%% mem=%.1f%%",
                cpu_pct or 0,
                mem_pct or 0,
            )
        except Exception as exc:
            logger.error("Metrics collector: sample failed — %s", exc)


def _collector_loop(app):
    """Main loop: wait for startup, then sample on interval."""
    logger.info("Metrics collector: waiting %ds before first sample", _STARTUP_DELAY)
    if _shutdown_event.wait(timeout=_STARTUP_DELAY):
        logger.info("Metrics collector: shutdown before first sample")
        return

    while not _shutdown_event.is_set():
        _collect_once(app)
        if _shutdown_event.wait(timeout=_COLLECT_INTERVAL):
            break

    logger.info("Metrics collector: stopped")


def start_collector(app):
    """Start the background metrics collection thread."""
    global _thread
    if _thread is not None and _thread.is_alive():
        logger.warning("Metrics collector: already running")
        return

    _shutdown_event.clear()
    _thread = threading.Thread(
        target=_collector_loop,
        args=(app,),
        name="metrics-collector",
        daemon=True,
    )
    _thread.start()
    logger.info(
        "Metrics collector: started (sampling every %ds)",
        _COLLECT_INTERVAL,
    )


def stop_collector():
    """Signal the collector thread to stop cleanly."""
    global _thread
    if _thread is None or not _thread.is_alive():
        return
    logger.info("Metrics collector: requesting shutdown")
    _shutdown_event.set()
    _thread.join(timeout=10)
    _thread = None
