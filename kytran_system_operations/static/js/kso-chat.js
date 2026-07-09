/**
 * kso-chat.js
 * B.A.S.E. chat overlay for Kytran System Operations standalone.
 * Proxied through KSO's /tools/archie-chat/api/ endpoint → ARCHIE platform.
 */
(function () {
    const AGENT_ID = 139;
    const CONFIG = {
        agentTarget: 'B.A.S.E.',
        label: 'B.A.S.E.',
        icon: '🖥️',
        accentColor: '#ef4444',
        moduleKey: 'kso_system_operations',
        expandUrl: '/dashboard',
        portraitUrl: null,
    };

    function applyConfig(portraitUrl) {
        function attempt() {
            var overlay = window.archieOverlay;
            if (!overlay) { setTimeout(attempt, 100); return; }
            overlay.configure(Object.assign({}, CONFIG, { portraitUrl: portraitUrl || null }));
        }
        attempt();
    }

    document.addEventListener('DOMContentLoaded', function () {
        fetch('/tools/media-studio/api/portraits/' + AGENT_ID, { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                applyConfig(d.canonical_image_path || d.avatar_url || null);
            })
            .catch(function () { applyConfig(null); });
    });
})();
