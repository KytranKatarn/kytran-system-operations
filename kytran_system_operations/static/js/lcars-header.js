/* lcars-header.js -- LCARS Header Component Behavior
   Handles: tab switching, alert state management, toast notifications, stardate.
   Works with lcars-header.html + lcars-header.css.
*/

(function () {
  "use strict";

  /* -- Tab Switching -- */

  window.lcarsHeaderSwitchTab = function (tabId) {
    var tabs = document.querySelectorAll(".lcars-header-tab");
    tabs.forEach(function (t) {
      t.classList.toggle("active", t.dataset.tab === tabId);
    });
    if (typeof window.onLcarsTabSwitch === "function") {
      window.onLcarsTabSwitch(tabId);
    }
  };

  /* -- Alert State Management -- */

  window.lcarsSetAlertState = function (state) {
    var header = document.getElementById("lcarsHeader");
    if (header) {
      header.dataset.alertState = state;
    }
  };

  /* -- Toast Notifications -- */

  var _toastId = 0;

  /**
   * lcarsToast(title, message, severity, options)
   * options: { durationMs, href, actionLabel }
   * - href: clicking toast navigates here
   * - actionLabel: text shown at bottom (e.g. "VIEW APPROVALS")
   */
  window.lcarsToast = function (title, message, severity, options) {
    severity = severity || "info";
    if (typeof options === "number") {
      options = { durationMs: options }; // backwards compat
    }
    options = options || {};
    var durationMs = typeof options.durationMs !== "undefined" ? options.durationMs
      : severity === "error" ? 0 : severity === "warning" ? 10000 : 5000;

    var container = document.getElementById("lcarsToastContainer");
    if (!container) return;

    var toast = document.createElement("div");
    toast.className = "lcars-toast";
    toast.dataset.severity = severity;
    toast.id = "lcarsToast" + (++_toastId);

    // Store dismiss callback on the element
    if (options.onDismiss) {
      toast._onDismiss = options.onDismiss;
    }

    // Click to navigate (also dismisses)
    if (options.href) {
      toast.addEventListener("click", function (e) {
        if (e.target.closest(".lcars-toast-close")) return;
        if (toast._onDismiss) toast._onDismiss();
        window.location.href = options.href;
      });
    }

    // LCARS elbow accent
    var accent = document.createElement("div");
    accent.className = "lcars-toast-accent";
    toast.appendChild(accent);

    // Content area
    var content = document.createElement("div");
    content.className = "lcars-toast-content";

    // Header row (title + close)
    var header = document.createElement("div");
    header.className = "lcars-toast-header";

    var titleEl = document.createElement("span");
    titleEl.className = "lcars-toast-title";
    titleEl.textContent = title;
    header.appendChild(titleEl);

    var closeBtn = document.createElement("button");
    closeBtn.className = "lcars-toast-close";
    closeBtn.textContent = "\u00d7";
    closeBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      _removeToast(toast);
    });
    header.appendChild(closeBtn);
    content.appendChild(header);

    // Message
    if (message) {
      var msgEl = document.createElement("span");
      msgEl.className = "lcars-toast-message";
      msgEl.textContent = message;
      content.appendChild(msgEl);
    }

    // Action hint
    if (options.actionLabel || options.href) {
      var actionEl = document.createElement("span");
      actionEl.className = "lcars-toast-action";
      actionEl.textContent = options.actionLabel || "TAP TO VIEW \u25B8";
      content.appendChild(actionEl);
    }

    toast.appendChild(content);
    container.appendChild(toast);

    if (durationMs > 0) {
      setTimeout(function () { _removeToast(toast); }, durationMs);
    }
  };

  function _removeToast(toast) {
    if (!toast || !toast.parentNode) return;
    if (toast._onDismiss) toast._onDismiss();
    toast.classList.add("removing");
    setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 300);
  }

  /* -- Notification Polling -- */

  var _notifPollInterval = null;

  function _pollNotifications() {
    fetch("/api/alerts/unified")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.success) return;
        // Cache for bell modal reuse
        window._lastUnifiedAlerts = data;

        var counts = data.counts || {};
        var unread = counts.unread || 0;
        var critical = counts.critical || 0;

        // Update elbow alert state — red flash only for critical
        if (critical > 0) {
          lcarsSetAlertState("critical");
        } else {
          lcarsSetAlertState("normal");
        }

        // Update badge counts (strip + elbow)
        var badges = ["lcarsStripBadge", "lcarsNotifBadge"];
        badges.forEach(function (id) {
          var badge = document.getElementById(id);
          if (badge) {
            if (unread > 0) {
              badge.textContent = unread > 99 ? "99+" : String(unread);
              badge.style.display = "inline-flex";
            } else {
              badge.style.display = "none";
            }
          }
        });
      })
      .catch(function () {}); // Silent fail — polling recovers next tick
  }

  // Expose for Mark All Read button
  window._pollNotifications = _pollNotifications;

  function _startNotifPolling() {
    _pollNotifications(); // Immediate first check
    _notifPollInterval = setInterval(_pollNotifications, 30000); // Every 30s
  }

  function _stopNotifPolling() {
    if (_notifPollInterval) {
      clearInterval(_notifPollInterval);
      _notifPollInterval = null;
    }
  }

  /* -- Notification Modal -- */

  window.lcarsOpenNotifications = function () {
    var overlay = document.getElementById("lcarsNotifModal");
    if (!overlay) return;

    overlay.classList.add("active");
    _loadNotificationList();
  };

  window.lcarsCloseNotifications = function () {
    var overlay = document.getElementById("lcarsNotifModal");
    if (overlay) overlay.classList.remove("active");
  };

  function _renderAlertItem(a) {
    var container = document.createElement("div");
    container.style.cssText = "border-bottom:1px solid rgba(var(--cat-accent-rgb,0,255,255),0.06);";

    // Summary row (always visible)
    var summary = document.createElement("div");
    summary.style.cssText = "display:flex;gap:10px;padding:8px 12px;cursor:pointer;transition:background 0.15s ease;align-items:flex-start;";
    summary.addEventListener("mouseenter", function () { summary.style.background = "rgba(var(--cat-accent-rgb,0,255,255),0.04)"; });
    summary.addEventListener("mouseleave", function () { summary.style.background = "none"; });

    // Severity dot
    var dot = document.createElement("span");
    var dotColor = a.type === "error" ? "#ef4444" : a.type === "warning" ? "#ff9f43" : "var(--cat-accent,#00ffff)";
    dot.style.cssText = "width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:5px;background:" + dotColor + ";box-shadow:0 0 6px " + dotColor + ";";
    summary.appendChild(dot);

    // Title
    var titleEl = document.createElement("div");
    titleEl.style.cssText = "flex:1;min-width:0;font-size:0.7rem;color:#e2e8f0;font-family:'Antonio',sans-serif;text-transform:uppercase;letter-spacing:0.3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
    titleEl.textContent = a.title || "Alert";
    summary.appendChild(titleEl);

    // Chevron
    var chevron = document.createElement("span");
    chevron.textContent = "\u25B8";
    chevron.style.cssText = "color:#64748b;font-size:0.7rem;flex-shrink:0;transition:transform 0.2s ease;";
    summary.appendChild(chevron);

    container.appendChild(summary);

    // Detail panel (hidden by default)
    var detail = document.createElement("div");
    detail.style.cssText = "display:none;padding:4px 12px 10px 30px;";

    // Message
    if (a.message) {
      var msgEl = document.createElement("div");
      msgEl.style.cssText = "font-size:0.65rem;color:#94a3b8;margin-bottom:6px;line-height:1.4;word-break:break-word;";
      msgEl.textContent = a.message;
      detail.appendChild(msgEl);
    }

    // Meta row: source + timestamp + priority
    var meta = document.createElement("div");
    meta.style.cssText = "display:flex;gap:8px;font-size:0.55rem;color:#475569;font-family:'IBM Plex Mono',monospace;";

    var srcEl = document.createElement("span");
    srcEl.textContent = (a.source || "system").replace(/_/g, " ").toUpperCase();
    srcEl.style.cssText = "background:rgba(var(--cat-accent-rgb,0,255,255),0.08);padding:1px 6px;border-radius:3px;";
    meta.appendChild(srcEl);

    var priEl = document.createElement("span");
    priEl.textContent = (a.priority || "medium").toUpperCase();
    var priColor = a.priority === "critical" ? "#ef4444" : a.priority === "high" ? "#ff9f43" : "#64748b";
    priEl.style.cssText = "color:" + priColor + ";";
    meta.appendChild(priEl);

    if (a.timestamp) {
      var tsEl = document.createElement("span");
      try { tsEl.textContent = new Date(a.timestamp).toLocaleString(); } catch (e) { tsEl.textContent = a.timestamp; }
      meta.appendChild(tsEl);
    }
    detail.appendChild(meta);

    // Mark read button
    var markBtn = document.createElement("button");
    markBtn.textContent = "MARK READ";
    markBtn.style.cssText = "margin-top:8px;background:rgba(var(--cat-accent-rgb,0,255,255),0.1);border:1px solid rgba(var(--cat-accent-rgb,0,255,255),0.2);color:var(--cat-accent,#00ffff);font-family:'Antonio',sans-serif;font-size:0.6rem;letter-spacing:1px;padding:3px 10px;border-radius:4px;cursor:pointer;";
    markBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      fetch("/api/alerts/unified/mark-read", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alert_key: a.id })
      }).then(function () {
        container.style.opacity = "0.4";
        a.is_read = true;
        markBtn.textContent = "READ";
        markBtn.disabled = true;
        _pollNotifications();
      });
    });
    detail.appendChild(markBtn);

    container.appendChild(detail);

    // Toggle detail on click
    var expanded = false;
    summary.addEventListener("click", function () {
      expanded = !expanded;
      detail.style.display = expanded ? "block" : "none";
      chevron.style.transform = expanded ? "rotate(90deg)" : "rotate(0deg)";
    });

    return container;
  }

  function _loadNotificationList() {
    var body = document.getElementById("lcarsNotifBody");
    if (!body) return;

    body.textContent = "";
    var loading = document.createElement("p");
    loading.style.color = "#94a3b8";
    loading.textContent = "Loading alerts...";
    body.appendChild(loading);

    var doRender = function (data) {
      body.textContent = "";
      var counts = data.counts || {};

      if (!data.success || counts.total === 0 || counts.unread === 0) {
        var empty = document.createElement("p");
        empty.style.color = "#64748b";
        empty.textContent = "All clear \u2014 no unread alerts";
        body.appendChild(empty);
        return;
      }

      var critical = data.critical || [];
      var categories = data.categories || {};
      var catOrder = ["infrastructure", "agents", "security", "jobs"];

      // Critical section
      if (critical.length > 0) {
        var critHdr = document.createElement("div");
        critHdr.style.cssText = "font-size:0.7rem;color:#ef4444;font-family:'Antonio',sans-serif;letter-spacing:0.8px;padding:6px 12px 4px;text-transform:uppercase;";
        critHdr.textContent = "CRITICAL (" + critical.length + ")";
        body.appendChild(critHdr);
        critical.forEach(function (a) { body.appendChild(_renderAlertItem(a)); });
      }

      // Category sections
      catOrder.forEach(function (cat) {
        var items = (categories[cat] || []).filter(function (a) { return !a.is_read; });
        if (items.length === 0) return;

        var hdr = document.createElement("div");
        hdr.style.cssText = "font-size:0.65rem;color:var(--cat-accent,#00ffff);font-family:'Antonio',sans-serif;letter-spacing:0.8px;padding:6px 12px 4px;text-transform:uppercase;border-top:1px solid rgba(var(--cat-accent-rgb,0,255,255),0.12);margin-top:4px;";
        hdr.textContent = cat.toUpperCase() + " (" + items.length + ")";
        body.appendChild(hdr);

        var shown = items.slice(0, 5);
        shown.forEach(function (a) { body.appendChild(_renderAlertItem(a)); });

        if (items.length > 5) {
          var more = document.createElement("div");
          more.style.cssText = "font-size:0.6rem;color:#64748b;padding:4px 12px 6px;font-family:'IBM Plex Mono',monospace;";
          more.textContent = "+" + (items.length - 5) + " more \u2014 see Dashboard";
          body.appendChild(more);
        }
      });

      // Dashboard link
      var link = document.createElement("a");
      link.href = "/dashboard#mission-briefing";
      link.style.cssText = "display:block;font-size:0.65rem;color:var(--cat-accent,#00ffff);font-family:'Antonio',sans-serif;letter-spacing:0.8px;padding:10px 12px;text-align:center;border-top:1px solid rgba(var(--cat-accent-rgb,0,255,255),0.15);margin-top:4px;text-decoration:none;";
      link.textContent = "VIEW FULL DASHBOARD \u25B8";
      body.appendChild(link);
    };

    if (window._lastUnifiedAlerts) {
      doRender(window._lastUnifiedAlerts);
    } else {
      fetch("/api/alerts/unified")
        .then(function (r) { return r.json(); })
        .then(function (data) {
          window._lastUnifiedAlerts = data;
          doRender(data);
        })
        .catch(function () {
          body.textContent = "";
          var err = document.createElement("p");
          err.style.color = "#ef4444";
          err.textContent = "Failed to load alerts";
          body.appendChild(err);
        });
    }
  }

  /* -- Stardate -- */

  function _updateStardate() {
    var el = document.getElementById("lcarsStardate");
    if (!el) return;
    var now = new Date();
    var year = now.getFullYear();
    var start = new Date(year, 0, 1);
    var diff = now - start;
    var oneDay = 86400000;
    var day = Math.floor(diff / oneDay);
    var fraction = Math.floor((diff % oneDay) / oneDay * 10);
    el.textContent = "SD " + year + day + "." + fraction;
  }

  /* -- Ops Summary Integration (replaces ops-alert-bar when LCARS header present) -- */

  var _opsLastSig = sessionStorage.getItem("lcars_ops_sig") || "";
  var _opsFirstPoll = true;
  var _opsPollInterval = null;
  var _SS_SIG = "lcars_ops_sig";
  var _SS_DISMISSED = "lcars_ops_dismissed";

  function _isOpsDismissed(key) {
    try {
      var d = JSON.parse(sessionStorage.getItem(_SS_DISMISSED) || "{}");
      return !!d[key];
    } catch (e) { return false; }
  }

  function _dismissOps(key) {
    try {
      var d = JSON.parse(sessionStorage.getItem(_SS_DISMISSED) || "{}");
      d[key] = Date.now();
      sessionStorage.setItem(_SS_DISMISSED, JSON.stringify(d));
    } catch (e) {}
  }

  function _pollOpsSummary() {
    fetch("/tools/department-hq/api/dispatch/ops-summary")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data) return;

        var approvals = (data.approvals && data.approvals.pending) || 0;
        var aj = data.active_jobs || {};
        var jobs = (aj.bridge_running || 0) + (aj.bridge_pending || 0);
        var e1h = data.errors_1h || {};
        var errors = (e1h.bridge_failed || 0) + (e1h.loop_errors || 0);

        // Build signature for change detection
        var sig = "a:" + approvals + "|e:" + errors + "|j:" + jobs;
        var changed = sig !== _opsLastSig;
        _opsLastSig = sig;
        sessionStorage.setItem(_SS_SIG, sig);

        // Update elbow alert state based on ops severity (always update, no toasts needed)
        if (errors > 0) {
          lcarsSetAlertState("critical");
        } else if (approvals > 0) {
          lcarsSetAlertState("warning");
        } else {
          lcarsSetAlertState("normal");
        }

        // On first poll (page load), just record state — don't toast
        if (_opsFirstPoll) {
          _opsFirstPoll = false;
          // Clear dismissed if state changed (new issue)
          if (changed) {
            sessionStorage.removeItem(_SS_DISMISSED);
          }
          return;
        }

        // Toast only when state CHANGES and not dismissed this session
        if (changed && approvals > 0 && !_isOpsDismissed("approvals")) {
          lcarsToast(
            "Approvals Pending",
            approvals + " approval" + (approvals > 1 ? "s" : "") + " require your review",
            "warning",
            {
              href: "/tools/department-hq/?tab=dispatch",
              actionLabel: "OPEN DISPATCH HUB \u25B8",
              onDismiss: function () { _dismissOps("approvals"); }
            }
          );
        }
        if (changed && errors > 0 && !_isOpsDismissed("errors")) {
          lcarsToast(
            "System Errors",
            errors + " error" + (errors > 1 ? "s" : "") + " detected in the last hour",
            "error",
            {
              href: "/tools/department-hq/?tab=dispatch",
              actionLabel: "VIEW DISPATCH HUB \u25B8",
              onDismiss: function () { _dismissOps("errors"); }
            }
          );
        }
      })
      .catch(function () {}); // Silent fail — recovers next tick
  }

  /* -- Admin Notification Channel Integration -- */

  function _initAdminNotifChannel() {
    if (typeof BroadcastChannel === "undefined") return;
    try {
      var channel = new BroadcastChannel("archie_admin_notifications");
      channel.addEventListener("message", function (ev) {
        var d = ev.data;
        if (d && d.type === "show" && d.notification) {
          var n = d.notification;
          var severity = n.type === "error" ? "error" : n.type === "warning" ? "warning" : "info";
          lcarsToast(n.title || "System Alert", n.text || "", severity, {
            href: n.href || null,
            actionLabel: n.actionLabel || null
          });
        }
      });
    } catch (e) {}
  }

  /* -- Init -- */

  document.addEventListener("DOMContentLoaded", function () {
    _updateStardate();
    setInterval(_updateStardate, 60000);
    _startNotifPolling();

    // Ops summary polling (replaces ops-alert-bar)
    _pollOpsSummary();
    _opsPollInterval = setInterval(_pollOpsSummary, 30000);

    // Listen for admin notification broadcasts
    _initAdminNotifChannel();

    if (typeof lucide !== "undefined" && lucide.createIcons) {
      lucide.createIcons();
    }

    // Wire elbow click to open notifications
    var elbow = document.getElementById("lcarsHeaderElbow");
    if (elbow) {
      elbow.style.cursor = "pointer";
      elbow.addEventListener("click", function (e) {
        if (e.target.closest(".lcars-header-back") || e.target.closest(".lcars-header-util")) return;
        lcarsOpenNotifications();
      });
    }
  });
})();
