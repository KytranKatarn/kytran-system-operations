"""
Background Compliance Scanner
=============================
Daemon thread that runs compliance scans at a configurable interval
(default 6 hours) and optionally reports results to the ARCHIE hub.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

_shutdown_event = threading.Event()
_thread = None


def _scanner_loop(app):
    """Main loop: wait for startup, then scan on interval."""
    interval = app.config.get("COMPLIANCE_SCAN_INTERVAL", 21600)

    # Let the app fully start before first scan
    logger.info("Compliance scheduler: waiting 60s before first scan (interval=%ds)", interval)
    if _shutdown_event.wait(timeout=60):
        logger.info("Compliance scheduler: shutdown requested during startup delay")
        return

    while not _shutdown_event.is_set():
        logger.info("Compliance scheduler: starting scheduled scan")
        scan_result = None
        try:
            from .compliance_service import run_scan
            scan_result = run_scan(triggered_by="scheduled")
            logger.info("Compliance scheduler: scan complete — %s", scan_result)
        except Exception as exc:
            logger.error("Compliance scheduler: scan failed — %s", exc)

        # Try reporting to hub (T6 will create hub_client)
        if scan_result is not None:
            try:
                from .hub_client import report_compliance
                report_compliance(scan_result)
                logger.info("Compliance scheduler: reported results to hub")
            except ImportError:
                logger.debug("Compliance scheduler: hub_client not available yet, skipping report")
            except Exception as exc:
                logger.warning("Compliance scheduler: hub reporting failed — %s", exc)

        # Sleep until next interval (or shutdown)
        if _shutdown_event.wait(timeout=interval):
            break

    logger.info("Compliance scheduler: stopped")


def start_scheduler(app):
    """Start the background compliance scan thread."""
    global _thread
    if _thread is not None and _thread.is_alive():
        logger.warning("Compliance scheduler: already running")
        return

    _shutdown_event.clear()
    _thread = threading.Thread(
        target=_scanner_loop,
        args=(app,),
        name="compliance-scheduler",
        daemon=True,
    )
    _thread.start()
    logger.info("Compliance scheduler: started (interval=%ds)",
                app.config.get("COMPLIANCE_SCAN_INTERVAL", 21600))


def stop_scheduler():
    """Signal the scheduler thread to stop cleanly."""
    global _thread
    if _thread is None or not _thread.is_alive():
        return
    logger.info("Compliance scheduler: requesting shutdown")
    _shutdown_event.set()
    _thread.join(timeout=10)
    _thread = None
