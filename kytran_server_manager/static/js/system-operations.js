// ArchieTime stub — standalone replacement for platform's archie-time.js
if (typeof ArchieTime === 'undefined') {
    window.ArchieTime = {
        format(isoStr, style) {
            try {
                const d = new Date(isoStr);
                if (isNaN(d)) return isoStr || '—';
                if (style === 'short') return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
                if (style === 'datetime') return d.toLocaleString();
                return d.toLocaleString();
            } catch { return isoStr || '—'; }
        },
        time(isoStr) {
            try { return new Date(isoStr).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}); }
            catch { return isoStr || '—'; }
        }
    };
}

// Verify Chart.js loaded
if (typeof Chart === 'undefined') {
    console.error('Chart.js failed to load from primary CDN');
}

// State
let autoRefreshEnabled = true;
let refreshInterval = null;
let cpuChart = null;
let memChart = null;
let gpuChart = null;
let bandwidthChart = null;
let cpuHistory = [];
let memHistory = [];
let gpuHistory = {};  // Multi-GPU: { gpu_0: [], gpu_1: [], ... }
let gpuDeviceCount = 0;  // Number of GPUs detected
let historyHours = 24;  // Default: 24 hours of history

// Format ISO UTC timestamp to local time, smart format based on hours range
function formatHistoryTime(isoStr, hours) {
    return hours > 24 ? ArchieTime.format(isoStr, 'short') : ArchieTime.time(isoStr);
}

function changeHistoryRange(hours) {
    historyHours = hours;
    // Update active button
    document.querySelectorAll('#history-range-selector .btn').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.hours) === hours);
    });
    loadHistoricalData();
}

// Color palette for multi-device charts
const deviceColors = [
    { border: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.15)' },  // Purple (GPU 0)
    { border: '#f59e0b', bg: 'rgba(245, 158, 11, 0.15)' },  // Amber (GPU 1)
    { border: '#10b981', bg: 'rgba(16, 185, 129, 0.15)' },  // Emerald (GPU 2)
    { border: '#ef4444', bg: 'rgba(239, 68, 68, 0.15)' },   // Red (GPU 3)
];
// Compute line colors (lighter/dashed variants per GPU)
const computeColors = [
    { border: '#06b6d4', bg: 'rgba(6, 182, 212, 0.08)' },   // Cyan (GPU 0 compute)
    { border: '#fb923c', bg: 'rgba(251, 146, 60, 0.08)' },   // Orange (GPU 1 compute)
    { border: '#34d399', bg: 'rgba(52, 211, 153, 0.08)' },   // Mint (GPU 2 compute)
    { border: '#f87171', bg: 'rgba(248, 113, 113, 0.08)' },  // Light red (GPU 3 compute)
];
let pendingAction = null;

// ===== Operation Tracker System =====
const activeOperations = new Map();
let activityTimerInterval = null;

function trackOperation(commandId, label) {
    const startTime = Date.now();
    activeOperations.set(commandId, {
        label: label,
        startTime: startTime,
        stage: 'Queued...',
        commandId: commandId
    });
    updateActivityStatusBar();
    startActivityTimer();

    // Broadcast to global admin notification system (cross-tab, cross-module)
    if (typeof broadcastAdminNotification === 'function') {
        broadcastAdminNotification({
            id: commandId,
            type: 'operation',
            title: 'Storage Operation',
            text: label + ' - Queued...',
            showTimer: true,
            startTime: startTime
        });
    }
}

function updateOperationStage(commandId, stage) {
    const op = activeOperations.get(commandId);
    if (op) {
        op.stage = stage;
        updateActivityStatusBar();

        // Update global admin notification
        if (typeof updateAdminNotification === 'function') {
            updateAdminNotification({
                text: op.label + ' - ' + stage
            });
        }
    }
}

function completeOperation(commandId, success = true, message = '') {
    const op = activeOperations.get(commandId);
    activeOperations.delete(commandId);
    updateActivityStatusBar();
    if (activeOperations.size === 0) {
        stopActivityTimer();
    }

    // Show completion in global admin notification
    if (typeof broadcastAdminNotification === 'function' && op) {
        broadcastAdminNotification({
            id: commandId + '_complete',
            type: success ? 'success' : 'error',
            title: success ? 'Operation Complete' : 'Operation Failed',
            text: message || (op.label + (success ? ' completed successfully' : ' failed')),
            showTimer: false,
            autoDismiss: 8000  // Auto-dismiss after 8 seconds for completions
        });
    }
}

function startActivityTimer() {
    if (activityTimerInterval) return; // Already running
    activityTimerInterval = setInterval(updateActivityStatusBar, 1000);
}

function stopActivityTimer() {
    if (activityTimerInterval) {
        clearInterval(activityTimerInterval);
        activityTimerInterval = null;
    }
}

function formatElapsedTime(ms) {
    const seconds = Math.floor(ms / 1000);
    if (seconds < 120) {
        return seconds + 's';
    }
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return minutes + 'm ' + secs + 's';
}

// ===== Pre-flight Check System =====
let preflightPendingOperation = null;

async function runPreflightCheck(device, mountpoint, operation, onProceed) {
    try {
        const res = await fetch('/dashboard/api/storage/preflight', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device, mountpoint, operation })
        });
        const data = await res.json();

        if (!data.success) {
            showToast('error', 'Pre-flight check failed: ' + (data.error || 'Unknown error'));
            return;
        }

        if (data.warnings && data.warnings.length > 0) {
            // Show pre-flight modal with warnings
            preflightPendingOperation = onProceed;
            showPreflightModal(data.warnings, data.can_proceed);
        } else {
            // No warnings, proceed directly
            onProceed();
        }
    } catch (err) {
        // If pre-flight fails, show warning but allow proceeding
        console.error('Pre-flight check error:', err);
        showToast('warning', 'Pre-flight check unavailable, proceeding...');
        onProceed();
    }
}

function showPreflightModal(warnings, canProceed) {
    const container = document.getElementById('preflightWarnings');
    container.innerHTML = '';

    warnings.forEach(w => {
        const div = document.createElement('div');
        div.className = 'preflight-warning severity-' + (w.severity || 'info');

        const iconName = w.severity === 'high' ? 'alert-octagon' :
                        w.severity === 'warning' ? 'alert-triangle' : 'info';

        const icon = document.createElement('i');
        icon.setAttribute('data-lucide', iconName);
        icon.className = 'warning-icon';

        const content = document.createElement('div');
        content.className = 'warning-content';

        const message = document.createElement('div');
        message.className = 'warning-message';
        message.textContent = w.message;
        content.appendChild(message);

        if (w.details) {
            const details = document.createElement('div');
            details.className = 'warning-details';
            if (Array.isArray(w.details)) {
                const list = document.createElement('div');
                list.className = 'warning-list';
                w.details.forEach(d => {
                    const span = document.createElement('span');
                    span.textContent = d;
                    list.appendChild(span);
                });
                content.appendChild(list);
            } else {
                details.textContent = w.details;
                content.appendChild(details);
            }
        }

        div.appendChild(icon);
        div.appendChild(content);
        container.appendChild(div);
    });

    // Update proceed button based on can_proceed
    const proceedBtn = document.getElementById('preflightProceedBtn');
    if (!canProceed) {
        proceedBtn.disabled = true;
        proceedBtn.textContent = 'Cannot Proceed (Fix Issues First)';
        proceedBtn.classList.remove('btn-warning');
        proceedBtn.classList.add('btn-ghost');
    } else {
        proceedBtn.disabled = false;
        proceedBtn.innerHTML = '<i data-lucide="chevron-right" style="width:14px; height:14px; display:inline-block; vertical-align:middle; margin-right:4px;"></i> Proceed Anyway';
        proceedBtn.classList.remove('btn-ghost');
        proceedBtn.classList.add('btn-warning');
    }

    document.getElementById('preflightModal').classList.add('active');
    lucide.createIcons();
}

function closePreflightModal() {
    document.getElementById('preflightModal').classList.remove('active');
    preflightPendingOperation = null;
}

function proceedAfterPreflight() {
    const op = preflightPendingOperation;
    closePreflightModal();
    if (op) op();
}

function updateActivityStatusBar() {
    const statusBar = document.getElementById('activityStatusBar');
    const indicator = document.getElementById('activityIndicator');

    if (activeOperations.size === 0) {
        statusBar.classList.remove('active');
        return;
    }

    statusBar.classList.add('active');

    if (activeOperations.size === 1) {
        // Single operation - simple display
        const op = activeOperations.values().next().value;
        const elapsed = formatElapsedTime(Date.now() - op.startTime);
        indicator.innerHTML = '';

        const dot = document.createElement('span');
        dot.className = 'pulse-dot';

        const text = document.createElement('span');
        text.className = 'activity-text';
        text.textContent = op.label;

        const timer = document.createElement('span');
        timer.className = 'activity-timer';
        timer.textContent = elapsed;

        const stage = document.createElement('span');
        stage.className = 'activity-stage';
        stage.textContent = op.stage ? '(' + op.stage + ')' : '';

        indicator.appendChild(dot);
        indicator.appendChild(text);
        indicator.appendChild(timer);
        indicator.appendChild(stage);
    } else {
        // Multiple operations - stacked display
        indicator.innerHTML = '';
        const multi = document.createElement('div');
        multi.className = 'activity-multi';

        activeOperations.forEach((op, id) => {
            const elapsed = formatElapsedTime(Date.now() - op.startTime);
            const item = document.createElement('div');
            item.className = 'activity-item';

            const dot = document.createElement('span');
            dot.className = 'pulse-dot';

            const text = document.createElement('span');
            text.className = 'activity-text';
            text.textContent = op.label;

            const timer = document.createElement('span');
            timer.className = 'activity-timer';
            timer.textContent = elapsed;

            const stage = document.createElement('span');
            stage.className = 'activity-stage';
            stage.textContent = op.stage ? '(' + op.stage + ')' : '';

            item.appendChild(dot);
            item.appendChild(text);
            item.appendChild(timer);
            item.appendChild(stage);
            multi.appendChild(item);
        });

        indicator.appendChild(multi);
    }
}

let allProcesses = [];
let processPageSize = 20;      // Process table page size
let processCurrentPage = 1;    // Current page for process table
let filteredProcesses = [];    // Filtered processes for pagination
let hostStorageLoaded = false;  // Flag: host monitor provided storage data
let storageData = null;        // Cached storage drive data
let lastStorageRefresh = null; // Timestamp of last storage data refresh
let selectedPartition = null;  // Currently selected treemap partition element
// storageOverallChart removed - replaced with DOM-based disk breakdown display
// Per-drive donut charts removed - Disk Usage breakdown provides all needed info
let reauthCallback = null;     // Callback for re-auth modal
let dockerMounts = {};         // Docker stack mount mappings (path -> stacks)
let storagePollingInterval = null;  // Auto-refresh interval for storage tab

// Load cached values from localStorage immediately
function loadCachedValues() {
    try {
        const cached = localStorage.getItem('sysops_last_values');
        if (cached) {
            const data = JSON.parse(cached);
            // Apply cached values to stat cards
            if (data.cpu) document.getElementById('cpu-value').textContent = data.cpu + '%';
            if (data.memory) document.getElementById('mem-value').textContent = data.memory + '%';
            if (data.cpuModel) document.getElementById('cpu-model').textContent = data.cpuModel;
            if (data.memDetail) document.getElementById('mem-detail').textContent = data.memDetail;
            console.log('Loaded cached values:', data);
        }
    } catch (e) {
        console.log('No cached values available');
    }

    // Also load cached memory hardware data (for Memory tab)
    loadCachedMemoryHardware();

    // Load cached dashboard memory hardware (DIMMs, Type, Speed, Channels)
    try {
        const cachedMemHw = localStorage.getItem('archie_memory_hardware');
        if (cachedMemHw) {
            renderDashboardMemoryHardware(JSON.parse(cachedMemHw));
        }
    } catch (e) {}
}

// Save current values to localStorage
function saveCachedValues(cpuPct, memPct, cpuModel, memDetail) {
    try {
        localStorage.setItem('sysops_last_values', JSON.stringify({
            cpu: cpuPct.toFixed(1),
            memory: memPct.toFixed(1),
            cpuModel: cpuModel,
            memDetail: memDetail,
            timestamp: Date.now()
        }));
    } catch (e) {}
}

// ===== Tab Data Caching Functions =====
// Cache keys for different tabs
const CACHE_KEYS = {
    storage: 'archie_storage_cache',
    hardware: 'archie_hardware_cache',
    docker: 'archie_docker_cache',
    portMap: 'archie_portmap_cache',
    bandwidth: 'archie_bandwidth_cache'
};

// Generic cache save function
function saveToCache(key, data) {
    try {
        localStorage.setItem(key, JSON.stringify({
            data: data,
            timestamp: Date.now()
        }));
    } catch (e) {
        console.log('Cache save failed for', key);
    }
}

// Generic cache load function (returns null if no cache or expired)
function loadFromCache(key, maxAgeMs = 300000) { // 5 min default
    try {
        const cached = localStorage.getItem(key);
        if (cached) {
            const parsed = JSON.parse(cached);
            const age = Date.now() - (parsed.timestamp || 0);
            if (age < maxAgeMs) {
                return parsed.data;
            }
        }
    } catch (e) {}
    return null;
}

// Load all cached tab data on startup
function loadAllCachedTabData() {
    // Storage cache
    const storageCache = loadFromCache(CACHE_KEYS.storage, 600000); // 10 min
    if (storageCache) {
        storageData = storageCache;
        console.log('Loaded storage from cache');
    }

    // Hardware cache
    const hardwareCache = loadFromCache(CACHE_KEYS.hardware, 3600000); // 1 hour (rarely changes)
    if (hardwareCache) {
        window.cachedHardwareData = hardwareCache;
        console.log('Loaded hardware from cache');
    }

    // Docker cache
    const dockerCache = loadFromCache(CACHE_KEYS.docker, 300000); // 5 min
    if (dockerCache) {
        window.cachedDockerData = dockerCache;
        console.log('Loaded docker from cache');
    }

    // Port Map cache
    const portMapCache = loadFromCache(CACHE_KEYS.portMap, 300000); // 5 min
    if (portMapCache) {
        window.cachedPortMapData = portMapCache;
        console.log('Loaded port map from cache');
    }

    // Bandwidth cache
    const bandwidthCache = loadFromCache(CACHE_KEYS.bandwidth, 120000); // 2 min
    if (bandwidthCache) {
        window.cachedBandwidthData = bandwidthCache;
        console.log('Loaded bandwidth from cache');
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    console.log('System Operations initializing...');

    // Load cached values IMMEDIATELY (before anything else)
    loadCachedValues();
    loadAllCachedTabData();

    if (typeof lucide !== 'undefined') lucide.createIcons();
    initTabs();
    initEventListeners();

    // Handle URL parameters for deep linking from other modules
    const urlParams = new URLSearchParams(window.location.search);
    const tabParam = urlParams.get('tab');
    const stackParam = urlParams.get('stack');

    if (tabParam) {
        // Find and click the matching tab button
        const tabBtn = document.querySelector(`.tab-btn[data-tab="${tabParam}"]`);
        if (tabBtn) {
            tabBtn.click();
            console.log(`Switched to tab: ${tabParam}`);
        }
    }

    // If a stack was specified, open it after Docker tab loads
    if (stackParam && tabParam === 'docker') {
        // Wait for Docker tab to load, then open stack detail
        setTimeout(() => {
            if (typeof openStackDetail === 'function') {
                openStackDetail(stackParam);
                console.log(`Opened stack detail: ${stackParam}`);
            }
        }, 500);
    }

    // Initialize charts (Chart.js should be loaded by now)
    initCharts();
    console.log('Charts initialized, Chart available:', typeof Chart !== 'undefined');

    // Load historical data FIRST before live data
    try {
        await loadHistoricalData();
        console.log('Historical data loaded');
    } catch (e) {
        console.error('Historical data error:', e);
    }

    // Then load current overview
    try {
        await refreshAll();
        console.log('Initial refresh complete');
    } catch (e) {
        console.error('Refresh error:', e);
    }

    startAutoRefresh();
    console.log('System Operations initialized');
});

let storageTabLoaded = false;
let hardwareTabLoaded = false;
let memoryTabLoaded = false;
let firewallTabLoaded = false;
let pendingDeleteRuleNumber = null;  // Rule number pending deletion

function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');

            // Load storage data when entering the tab
            if (btn.dataset.tab === 'storage') {
                if (!storageTabLoaded) {
                    storageTabLoaded = true;
                    loadStorageData();
                }
                // Start auto-polling every 30 seconds to detect new drives
                if (!storagePollingInterval) {
                    storagePollingInterval = setInterval(function() {
                        console.log('Storage auto-refresh...');
                        loadStorageData();
                    }, 30000);
                }
            } else {
                // Stop polling when leaving storage tab
                if (storagePollingInterval) {
                    clearInterval(storagePollingInterval);
                    storagePollingInterval = null;
                }
                // Reset so returning to storage tab re-fetches fresh data
                storageTabLoaded = false;
            }

            // Load hardware data when entering the tab
            if (btn.dataset.tab === 'hardware') {
                if (!hardwareTabLoaded) {
                    hardwareTabLoaded = true;
                    loadHardwareData();
                }
            }
            // Don't reset hardware tab - hardware info doesn't change often

            // Load memory hardware data when entering the tab
            if (btn.dataset.tab === 'memory') {
                if (!memoryTabLoaded) {
                    memoryTabLoaded = true;
                    loadMemoryHardware();
                }
            }
            // Don't reset memory tab - hardware info doesn't change often

            // Load health alerts when entering Docker tab
            if (btn.dataset.tab === 'docker') {
                loadHealthAlerts();
            }

            // Load firewall data when entering the tab
            if (btn.dataset.tab === 'firewall') {
                if (!firewallTabLoaded) {
                    firewallTabLoaded = true;
                    loadFirewallData();
                }
            } else {
                // Reset when leaving so returning re-fetches fresh data
                firewallTabLoaded = false;
            }

            lucide.createIcons();
        });
    });
}

function initCharts() {
    if (typeof Chart === 'undefined') {
        console.error('Chart.js not available');
        return;
    }

    const accentColor = getComputedStyle(document.documentElement).getPropertyValue('--archie-accent').trim();
    const accentAlpha = getComputedStyle(document.documentElement).getPropertyValue('--archie-accent-alpha-10').trim() || 'rgba(0, 255, 255, 0.1)';

    const chartConfig = {
        type: 'line',
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                title: { display: false }
            },
            scales: {
                y: { min: 0, max: 100, grid: { color: '#30363d' }, ticks: { color: '#8b949e' } },
                x: { grid: { color: '#30363d' }, ticks: { color: '#8b949e', maxTicksLimit: 8 } }
            },
            elements: { point: { radius: 0 }, line: { tension: 0.4 } }
        }
    };

    // Initialize with placeholder "Loading..." label
    cpuChart = new Chart(document.getElementById('cpuChart'), {
        ...chartConfig,
        data: {
            labels: ['Loading...'],
            datasets: [{ data: [null], borderColor: accentColor, backgroundColor: accentAlpha, fill: true, borderWidth: 2 }]
        }
    });

    memChart = new Chart(document.getElementById('memChart'), {
        ...chartConfig,
        data: {
            labels: ['Loading...'],
            datasets: [{ data: [null], borderColor: '#00ff88', backgroundColor: 'rgba(0, 255, 136, 0.1)', fill: true, borderWidth: 2 }]
        }
    });

    // GPU chart config - VRAM (solid) + Compute (dashed) per GPU
    const gpuChartConfig = {
        type: 'line',
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { color: '#8b949e', boxWidth: 12, padding: 8, font: { size: 10 } }
                },
                title: { display: false }
            },
            scales: {
                y: { min: 0, max: 100, grid: { color: '#30363d' }, ticks: { color: '#8b949e' } },
                x: { grid: { color: '#30363d' }, ticks: { color: '#8b949e', maxTicksLimit: 8 } }
            },
            elements: { point: { radius: 0 }, line: { tension: 0.4 } }
        },
        data: {
            labels: ['Loading...'],
            datasets: [{
                label: 'GPU 0 VRAM',
                data: [null],
                borderColor: '#8b5cf6',
                backgroundColor: 'rgba(139, 92, 246, 0.1)',
                fill: true,
                borderWidth: 2
            }, {
                label: 'GPU 0 Compute',
                data: [null],
                borderColor: '#06b6d4',
                backgroundColor: 'rgba(6, 182, 212, 0.08)',
                fill: false,
                borderWidth: 1.5,
                borderDash: [5, 3]
            }]
        }
    };
    gpuChart = new Chart(document.getElementById('gpuChart'), gpuChartConfig);

    // Bandwidth chart config - shows RX/TX rates over time
    const bandwidthCanvas = document.getElementById('bandwidthChart');
    if (bandwidthCanvas) {
        const bandwidthChartConfig = {
            type: 'line',
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: { color: '#8b949e', boxWidth: 12, padding: 8, font: { size: 10 } }
                    },
                    title: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                return context.dataset.label + ': ' + formatBandwidthRate(context.raw);
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        min: 0,
                        grid: { color: '#30363d' },
                        ticks: {
                            color: '#8b949e',
                            callback: function(value) { return formatBandwidthRate(value); }
                        }
                    },
                    x: { grid: { color: '#30363d' }, ticks: { color: '#8b949e', maxTicksLimit: 10 } }
                },
                elements: { point: { radius: 0 }, line: { tension: 0.4 } }
            },
            data: {
                labels: ['Loading...'],
                datasets: [
                    {
                        label: 'Download (RX)',
                        data: [null],
                        borderColor: '#00D4AA',
                        backgroundColor: 'rgba(0, 212, 170, 0.1)',
                        fill: true,
                        borderWidth: 2
                    },
                    {
                        label: 'Upload (TX)',
                        data: [null],
                        borderColor: '#00D4FF',
                        backgroundColor: 'rgba(0, 212, 255, 0.1)',
                        fill: true,
                        borderWidth: 2
                    }
                ]
            }
        };
        bandwidthChart = new Chart(bandwidthCanvas, bandwidthChartConfig);
    }
}

function initEventListeners() {
    document.getElementById('autoRefresh').addEventListener('change', (e) => {
        autoRefreshEnabled = e.target.checked;
        document.getElementById('liveIndicator').style.display = autoRefreshEnabled ? 'flex' : 'none';
        if (autoRefreshEnabled) startAutoRefresh();
        else stopAutoRefresh();
    });

    document.getElementById('process-sort').addEventListener('change', loadProcesses);
    document.getElementById('service-filter').addEventListener('change', loadServices);
    document.getElementById('process-filter').addEventListener('input', filterProcesses);
}

function startAutoRefresh() {
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(refreshAll, 5000);
    document.getElementById('liveIndicator').style.display = 'flex';
}

function stopAutoRefresh() {
    if (refreshInterval) {
        clearInterval(refreshInterval);
        refreshInterval = null;
    }
    document.getElementById('liveIndicator').style.display = 'none';
}

// API Calls
async function refreshAll() {
    console.log('refreshAll called');
    try {
        // Load host data FIRST so it sets hostStorageLoaded before loadDisks runs
        await loadHostData();

        const results = await Promise.allSettled([
            loadOverview(),
            loadDisks(),
            loadProcesses(),
            loadServices(),
            loadDocker(),
            loadNetwork(),
            loadDockerMounts(),
            loadDashboardMemoryHardware(),
            loadHealthAlerts()
            // Storage excluded from auto-refresh: data changes rarely and
            // re-rendering destroys treemap selection/action panel state.
            // Storage loads on tab activation and manual Refresh button.
        ]);
        // Log any failures
        results.forEach((result, i) => {
            if (result.status === 'rejected') {
                console.error(`Load function ${i} failed:`, result.reason);
            }
        });
    } catch (e) {
        console.error('Refresh error:', e);
    }
}

async function loadHistoricalData() {
    const hours = historyHours;
    console.log('loadHistoricalData: Starting with hours=' + hours);

    // Load CPU history
    try {
        const cpuRes = await fetch('/dashboard/api/history?type=cpu&hours=' + hours);
        if (cpuRes.ok) {
            const cpuJson = await cpuRes.json();
            if (cpuJson.success && cpuJson.data && cpuJson.data.values && cpuJson.data.values.length > 0) {
                cpuHistory = cpuJson.data.labels.map((label, i) => ({
                    time: formatHistoryTime(label, hours),
                    value: cpuJson.data.values[i]
                }));
                if (cpuChart) {
                    cpuChart.data.labels = cpuHistory.map(h => h.time);
                    cpuChart.data.datasets[0].data = cpuHistory.map(h => h.value);
                    cpuChart.update('none');
                }
            }
        }
    } catch (e) {
        console.error('loadHistoricalData: CPU error:', e);
    }

    // Load Memory history
    try {
        const memRes = await fetch('/dashboard/api/history?type=memory&hours=' + hours);
        if (memRes.ok) {
            const memJson = await memRes.json();
            if (memJson.success && memJson.data && memJson.data.values && memJson.data.values.length > 0) {
                memHistory = memJson.data.labels.map((label, i) => ({
                    time: formatHistoryTime(label, hours),
                    value: memJson.data.values[i]
                }));
                if (memChart) {
                    memChart.data.labels = memHistory.map(h => h.time);
                    memChart.data.datasets[0].data = memHistory.map(h => h.value);
                    memChart.update('none');
                }
            }
        }
    } catch (e) {
        console.error('loadHistoricalData: Memory error:', e);
    }

    // Load GPU history — BOTH VRAM and Compute lines per GPU
    try {
        const [vramRes, computeRes] = await Promise.all([
            fetch('/dashboard/api/history/multi?type=gpu_vram&hours=' + hours),
            fetch('/dashboard/api/history/multi?type=gpu&hours=' + hours)
        ]);
        const vramJson = vramRes.ok ? await vramRes.json() : null;
        const computeJson = computeRes.ok ? await computeRes.json() : null;

        gpuHistory = {};
        const datasets = [];
        let longestLabels = [];

        // VRAM datasets (solid lines)
        if (vramJson && vramJson.success && vramJson.data && vramJson.data.devices) {
            const vDevices = vramJson.data.devices;
            Object.keys(vDevices).sort().forEach(key => {
                const idx = parseInt(key.replace('gpu_vram_', ''));
                const histKey = `gpu_${idx}_vram`;
                gpuHistory[histKey] = vDevices[key].labels.map((label, i) => ({
                    time: formatHistoryTime(label, hours),
                    value: vDevices[key].values[i]
                }));
                if (gpuHistory[histKey].length > longestLabels.length) {
                    longestLabels = gpuHistory[histKey].map(h => h.time);
                }
                datasets.push({
                    label: `GPU ${idx} VRAM`,
                    data: gpuHistory[histKey].map(h => h.value),
                    borderColor: deviceColors[idx % deviceColors.length].border,
                    backgroundColor: deviceColors[idx % deviceColors.length].bg,
                    fill: true,
                    borderWidth: 2
                });
            });
        }

        // Compute datasets (dashed lines)
        if (computeJson && computeJson.success && computeJson.data && computeJson.data.devices) {
            const cDevices = computeJson.data.devices;
            Object.keys(cDevices).sort().forEach(key => {
                const idx = parseInt(key.replace('gpu_', ''));
                const histKey = `gpu_${idx}_compute`;
                gpuHistory[histKey] = cDevices[key].labels.map((label, i) => ({
                    time: formatHistoryTime(label, hours),
                    value: cDevices[key].values[i]
                }));
                if (gpuHistory[histKey].length > longestLabels.length) {
                    longestLabels = gpuHistory[histKey].map(h => h.time);
                }
                datasets.push({
                    label: `GPU ${idx} Compute`,
                    data: gpuHistory[histKey].map(h => h.value),
                    borderColor: computeColors[idx % computeColors.length].border,
                    backgroundColor: computeColors[idx % computeColors.length].bg,
                    fill: false,
                    borderWidth: 1.5,
                    borderDash: [5, 3]
                });
            });
        }

        gpuDeviceCount = Math.max(
            Object.keys(gpuHistory).filter(k => k.endsWith('_vram')).length,
            Object.keys(gpuHistory).filter(k => k.endsWith('_compute')).length
        );

        if (gpuChart && datasets.length > 0) {
            gpuChart.data.labels = longestLabels;
            gpuChart.data.datasets = datasets;
            gpuChart.options.plugins.legend.display = true;
            gpuChart.update('none');
        }
    } catch (e) {
        console.error('loadHistoricalData: GPU error:', e);
    }

    console.log('loadHistoricalData: Complete');
}

async function loadHostData() {
    // Load host system data (LVM, RAID, Disks, Services) from host monitor file
    try {
        const res = await fetch('/dashboard/api/host-data');
        const json = await res.json();
        if (json.success && json.data) {
            renderHostLVM(json.data.lvm);
            renderHostDisks(json.data.disks);
            renderHostRaid(json.data.raid);
            renderHostServices(json.data.services);

            // Override container-level storage data with host data
            hostStorageLoaded = true;
            if (json.data.disks && json.data.disks.length > 0) {
                const hostPartitions = [];
                for (const disk of json.data.disks) {
                    for (const part of (disk.partitions || [])) {
                        // Add the partition itself if it has a mountpoint and usage
                        if (part.mountpoint && part.usage) {
                            hostPartitions.push({
                                device: part.device,
                                mountpoint: part.mountpoint,
                                fstype: part.fstype || '--',
                                parent_disk: `${disk.name} (${disk.model})`,
                                total_gb: part.usage.total_gb || 0,
                                used_gb: part.usage.used_gb || 0,
                                free_gb: part.usage.free_gb || 0,
                                usage_percent: part.usage.percent || 0,
                                is_lvm: false,
                                lvm_info: null
                            });
                        }
                        // Check LVM/child partitions
                        for (const child of (part.children || [])) {
                            if (child.mountpoint && child.usage) {
                                const isLvm = child.type === 'lvm';
                                hostPartitions.push({
                                    device: child.device,
                                    mountpoint: child.mountpoint,
                                    fstype: child.fstype || '--',
                                    parent_disk: `${disk.name} (${disk.model})`,
                                    total_gb: child.usage.total_gb || 0,
                                    used_gb: child.usage.used_gb || 0,
                                    free_gb: child.usage.free_gb || 0,
                                    usage_percent: child.usage.percent || 0,
                                    is_lvm: isLvm,
                                    lvm_info: isLvm ? { lv_name: child.lv_name || child.name, vg_name: child.vg_name || '', can_extend: false, vg_free_gb: 0 } : null
                                });
                            }
                        }
                    }
                }
                if (hostPartitions.length > 0) {
                    renderPartitions(hostPartitions);
                }
            }

            // Note: renderHostLVM already called above (line 3137) - don't override with container-level renderLVM
        }
    } catch (e) {
        // Host data not available - container-only mode
        console.log('Host data not available (running in container-only mode)');
    }
}

async function loadOverview() {
    try {
        const res = await fetch('/dashboard/api/overview');
        const json = await res.json();
        if (json.success) updateDashboard(json.data);
    } catch (e) {
        console.error('Overview error:', e);
    }
}

async function loadDockerMounts() {
    try {
        const res = await fetch('/dashboard/api/storage/docker-mounts');
        const json = await res.json();
        if (json.success && json.data?.path_mappings) {
            dockerMounts = json.data.path_mappings;
        }
    } catch (e) {
        console.error('Docker mounts error:', e);
    }
}

function getStacksForPath(mountpoint) {
    // Find which Docker stacks use this mountpoint
    if (!mountpoint || !dockerMounts) return [];

    const stacks = [];
    const seenStacks = new Set();

    for (const [path, stackList] of Object.entries(dockerMounts)) {
        // Check if this path is under the mountpoint
        let matches = false;
        if (mountpoint === '/') {
            // Root filesystem - all absolute paths fall under it
            matches = path.startsWith('/');
        } else {
            matches = path.startsWith(mountpoint + '/') || path === mountpoint;
        }
        if (matches) {
            for (const stack of stackList) {
                if (!seenStacks.has(stack.stack_name)) {
                    seenStacks.add(stack.stack_name);
                    stacks.push(stack);
                }
            }
        }
    }

    return stacks;
}

function updateDashboard(data) {
    // CPU
    const cpuPct = data.cpu?.usage_percent || 0;
    const cpuModel = (data.cpu?.model || 'Unknown').substring(0, 45);
    const cpuCores = data.cpu?.physical_cores && data.cpu?.logical_cores
        ? ` (${data.cpu.physical_cores}c/${data.cpu.logical_cores}t)` : '';
    document.getElementById('cpu-value').textContent = cpuPct.toFixed(1) + '%';
    document.getElementById('cpu-model').textContent = cpuModel + cpuCores;
    document.getElementById('cpu-bar').style.width = cpuPct + '%';
    document.getElementById('cpu-bar').className = 'progress-fill ' + getColorClass(cpuPct);
    document.getElementById('cpu-load').textContent = (data.cpu?.load_avg || [0,0,0]).map(v => typeof v === 'number' ? v.toFixed(2) : v).join(' / ');

    // CPU Temperature
    const cpuTempEl = document.getElementById('cpu-temp');
    const cpuTempValueEl = document.getElementById('cpu-temp-value');
    if (data.cpu?.temperature !== null && data.cpu?.temperature !== undefined) {
        cpuTempEl.style.display = 'flex';
        cpuTempValueEl.textContent = data.cpu.temperature + '°C';
        const tempHigh = data.cpu.temp_high || 70;
        const tempCritical = data.cpu.temp_critical || 90;
        cpuTempEl.classList.remove('warning', 'critical');
        if (data.cpu.temperature >= tempCritical) {
            cpuTempEl.classList.add('critical');
        } else if (data.cpu.temperature >= tempHigh) {
            cpuTempEl.classList.add('warning');
        }
    } else {
        cpuTempEl.style.display = 'none';
    }

    // Memory
    const memPct = data.memory?.usage_percent || 0;
    const memDetail = `${data.memory?.used_gb || 0} / ${data.memory?.total_gb || 0} GB`;
    document.getElementById('mem-value').textContent = memPct.toFixed(1) + '%';
    document.getElementById('mem-detail').textContent = memDetail;
    document.getElementById('mem-bar').style.width = memPct + '%';
    document.getElementById('mem-bar').className = 'progress-fill ' + getColorClass(memPct);
    document.getElementById('mem-swap').textContent = `${data.memory?.swap_used_gb || 0} / ${data.memory?.swap_total_gb || 0} GB`;

    // Cache values for next page load
    saveCachedValues(cpuPct, memPct, cpuModel, memDetail);

    // GPU 0 and GPU 1 display
    const gpuTempBadge = document.getElementById('gpu-temp');
    const gpuTempValue = document.getElementById('gpu-temp-value');
    const gpuValueEl = document.getElementById('gpu-value');
    const gpuModelEl = document.getElementById('gpu-model');
    const gpuBarEl = document.getElementById('gpu-bar');
    const vramEl = document.getElementById('gpu-vram');
    const gpuComputeEl = document.getElementById('gpu-compute');
    const gpu1ValueEl = document.getElementById('gpu1-value');
    const gpu1ModelEl = document.getElementById('gpu1-model');
    const gpusArray = data.gpus || (data.gpu?.available ? [data.gpu] : []);

    // GPU 0
    if (gpusArray.length > 0) {
        const gpu0 = gpusArray[0];
        const gpu0Name = (gpu0.model || 'GPU').substring(0, 25);
        const gpu0Driver = gpu0.driver || '';

        if (gpu0.has_detailed_stats && gpu0.vram_percent != null) {
            // Primary display: VRAM percentage
            gpuValueEl.textContent = gpu0.vram_percent.toFixed(1) + '%';
            gpuModelEl.textContent = gpu0Name + (gpu0Driver ? ' (' + gpu0Driver + ')' : '');
            gpuBarEl.style.width = gpu0.vram_percent + '%';
            gpuBarEl.className = 'progress-fill ' + getColorClass(gpu0.vram_percent);
        } else if (gpu0.has_detailed_stats && gpu0.usage_percent != null) {
            // Fallback: compute utilization if no VRAM data
            gpuValueEl.textContent = gpu0.usage_percent.toFixed(1) + '%';
            gpuModelEl.textContent = gpu0Name + (gpu0Driver ? ' (' + gpu0Driver + ')' : '');
            gpuBarEl.style.width = gpu0.usage_percent + '%';
            gpuBarEl.className = 'progress-fill ' + getColorClass(gpu0.usage_percent);
        } else {
            gpuValueEl.textContent = gpu0Name;
            gpuValueEl.style.fontSize = '1.2rem';
            gpuBarEl.style.width = '0%';
            gpuModelEl.textContent = gpu0Driver ? gpu0Driver + ' (no stats)' : 'No usage stats';
        }

        // Secondary display: compute utilization
        if (gpu0.usage_percent != null) {
            gpuComputeEl.textContent = gpu0.usage_percent.toFixed(1) + '%';
        } else {
            gpuComputeEl.textContent = 'N/A';
        }

        // Temperature badge for GPU 0
        if (gpu0.temperature) {
            gpuTempValue.textContent = gpu0.temperature + '\u00B0C';
            gpuTempBadge.style.display = 'flex';
            gpuTempBadge.className = 'stat-badge' + (gpu0.temperature >= 85 ? ' critical' : gpu0.temperature >= 70 ? ' warning' : '');
        } else {
            gpuTempBadge.style.display = 'none';
        }

        // VRAM for GPU 0
        if (gpu0.vram_total_mb && gpu0.vram_used_mb != null) {
            vramEl.textContent = (gpu0.vram_used_mb / 1024).toFixed(1) + ' / ' + (gpu0.vram_total_mb / 1024).toFixed(1) + ' GB';
        } else if (gpu0.vram_total_mb) {
            vramEl.textContent = (gpu0.vram_total_mb / 1024).toFixed(1) + ' GB total';
        } else {
            vramEl.textContent = gpu0Driver === 'nouveau' ? 'N/A (nouveau)' : 'N/A';
        }
    } else {
        gpuValueEl.textContent = 'N/A';
        gpuValueEl.style.fontSize = '1.4rem';
        gpuModelEl.textContent = 'No GPU detected';
        gpuTempBadge.style.display = 'none';
        vramEl.textContent = '--';
        gpuComputeEl.textContent = '--';
    }

    // GPU 1
    if (gpusArray.length > 1) {
        const gpu1 = gpusArray[1];
        const gpu1Name = (gpu1.model || 'GPU').substring(0, 25);
        const gpu1Driver = gpu1.driver || '';

        if (gpu1.has_detailed_stats && gpu1.usage_percent != null) {
            gpu1ValueEl.textContent = gpu1.usage_percent.toFixed(1) + '%';
            gpu1ValueEl.style.color = '';
            gpu1ValueEl.style.fontSize = '1.4rem';
            gpu1ModelEl.textContent = gpu1Name + (gpu1Driver ? ' (' + gpu1Driver + ')' : '');
        } else {
            gpu1ValueEl.textContent = gpu1Name;
            gpu1ValueEl.style.fontSize = '1.1rem';
            gpu1ValueEl.style.color = '';
            gpu1ModelEl.textContent = gpu1Driver ? gpu1Driver + ' (no stats)' : 'No usage stats';
        }
    } else {
        gpu1ValueEl.textContent = 'Unavailable';
        gpu1ValueEl.style.color = 'var(--archie-text-muted)';
        gpu1ValueEl.style.fontSize = '1.4rem';
        gpu1ModelEl.textContent = 'No second GPU detected';
    }

    // Disk - per-drive detail with partition breakdown
    const diskContainer = document.getElementById('disk-drives-container');
    const hostDisks = data.host_disks;
    diskContainer.textContent = '';

    function formatDiskSize(gb) {
        if (gb >= 1000) return (gb / 1000).toFixed(1) + ' TB';
        return gb.toFixed(1) + ' GB';
    }

    if (hostDisks && hostDisks.length > 0) {
        hostDisks.forEach(function(disk, idx) {
            const driveDiv = document.createElement('div');
            if (idx > 0) {
                driveDiv.style.marginTop = '14px';
                driveDiv.style.borderTop = '1px solid var(--archie-border)';
                driveDiv.style.paddingTop = '10px';
            }

            // Drive header: model + total size
            const header = document.createElement('div');
            header.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;';
            const label = document.createElement('span');
            label.style.cssText = 'font-size: 0.82rem; font-weight: 600; color: var(--archie-text-primary);';
            label.textContent = (disk.model || disk.device || 'Drive ' + (idx + 1)).substring(0, 30);
            const sizeLabel = document.createElement('span');
            sizeLabel.style.cssText = 'font-size: 0.75rem; color: var(--archie-text-muted);';
            sizeLabel.textContent = formatDiskSize(disk.size_gb || 0);
            header.appendChild(label);
            header.appendChild(sizeLabel);
            driveDiv.appendChild(header);

            // Summary stats line: Alloc / Unalloc / Free / Total
            const totalGb = disk.size_gb || 0;
            const unallocatedGb = disk.unallocated_gb || 0;
            const allocatedGb = totalGb - unallocatedGb;
            let usedGb = 0;
            let freeGb = 0;
            (disk.partitions || []).forEach(function(part) {
                if (part.usage) { usedGb += part.usage.used_gb || 0; freeGb += part.usage.free_gb || 0; }
                (part.children || []).forEach(function(child) {
                    if (child.usage) { usedGb += child.usage.used_gb || 0; freeGb += child.usage.free_gb || 0; }
                });
            });
            const usePct = totalGb > 0 ? (usedGb / totalGb * 100) : 0;

            // Progress bar
            const barOuter = document.createElement('div');
            barOuter.className = 'progress-bar';
            const barFill = document.createElement('div');
            barFill.className = 'progress-fill ' + getColorClass(usePct, 80, 90);
            barFill.style.width = usePct + '%';
            barOuter.appendChild(barFill);
            driveDiv.appendChild(barOuter);

            // Summary line
            const summary = document.createElement('div');
            summary.style.cssText = 'font-size: 0.72rem; color: var(--archie-text-muted); margin-top: 3px; display: flex; gap: 8px; flex-wrap: wrap;';
            function addSummary(parent, lbl, val, color) {
                var s = document.createElement('span');
                var l = document.createElement('span');
                l.textContent = lbl + ': ';
                var v = document.createElement('span');
                v.style.cssText = 'color:' + color + '; font-weight: 600;';
                v.textContent = val;
                s.appendChild(l); s.appendChild(v); parent.appendChild(s);
            }
            addSummary(summary, 'Alloc', formatDiskSize(allocatedGb), 'var(--archie-cyan)');
            addSummary(summary, 'Unalloc', formatDiskSize(unallocatedGb), 'var(--archie-text-muted)');
            addSummary(summary, 'Free', formatDiskSize(freeGb), 'var(--archie-green)');
            addSummary(summary, 'Total', formatDiskSize(totalGb), 'var(--archie-text-secondary)');
            driveDiv.appendChild(summary);

            // Partition breakdown
            var parts = disk.partitions || [];
            if (parts.length > 0) {
                var partList = document.createElement('div');
                partList.style.cssText = 'margin-top: 6px; padding-left: 8px; border-left: 2px solid var(--archie-border); font-size: 0.72rem;';
                parts.forEach(function(part) {
                    var row = document.createElement('div');
                    row.style.cssText = 'display: flex; justify-content: space-between; padding: 2px 0; color: var(--archie-text-secondary);';
                    var nameSpan = document.createElement('span');
                    nameSpan.style.fontWeight = '500';
                    var partName = part.name || part.device || '?';
                    var partType = part.fstype || '';
                    var partMount = part.mountpoint ? ' @ ' + part.mountpoint : '';
                    nameSpan.textContent = partName;
                    var infoSpan = document.createElement('span');
                    infoSpan.style.color = 'var(--archie-text-muted)';
                    var partSizeGb = part.size_gb || 0;
                    var infoText = formatDiskSize(partSizeGb) + ' ' + partType + partMount;
                    if (part.usage) {
                        infoText += ' (' + (part.usage.percent || 0).toFixed(0) + '% used)';
                    }
                    infoSpan.textContent = infoText;
                    row.appendChild(nameSpan);
                    row.appendChild(infoSpan);
                    partList.appendChild(row);

                    // Show Docker stacks using this partition's mount
                    if (part.mountpoint) {
                        var partStacks = getStacksForPath(part.mountpoint);
                        if (partStacks.length > 0) {
                            var stackRow = document.createElement('div');
                            stackRow.style.cssText = 'display: flex; gap: 4px; flex-wrap: wrap; padding: 2px 0 2px 12px;';
                            partStacks.forEach(function(stack) {
                                var badge = document.createElement('span');
                                badge.style.cssText = 'padding: 1px 5px; font-size: 0.65rem; border-radius: 3px; background: ' + (stack.color || '#6366f1') + '33; color: ' + (stack.color || '#6366f1') + '; border: 1px solid ' + (stack.color || '#6366f1') + '44;';
                                badge.textContent = stack.display_name;
                                badge.title = 'Docker: ' + stack.service + ' → ' + stack.container_path;
                                stackRow.appendChild(badge);
                            });
                            partList.appendChild(stackRow);
                        }
                    }

                    // Show LVM children under partition
                    var childrenTotalGb = 0;
                    (part.children || []).forEach(function(child) {
                        var childRow = document.createElement('div');
                        childRow.style.cssText = 'display: flex; justify-content: space-between; padding: 2px 0 2px 12px; color: var(--archie-text-secondary);';
                        var cName = document.createElement('span');
                        cName.style.cssText = 'font-weight: 500; color: var(--archie-cyan);';
                        cName.textContent = '\u2514 ' + (child.name || child.device || '?');
                        var cInfo = document.createElement('span');
                        cInfo.style.color = 'var(--archie-text-muted)';
                        var childSizeGb = child.size_gb || 0;
                        childrenTotalGb += childSizeGb;
                        var cText = formatDiskSize(childSizeGb) + ' ' + (child.fstype || '');
                        if (child.mountpoint) cText += ' @ ' + child.mountpoint;
                        if (child.usage) cText += ' (' + (child.usage.percent || 0).toFixed(0) + '% used)';
                        cInfo.textContent = cText;
                        childRow.appendChild(cName);
                        childRow.appendChild(cInfo);
                        partList.appendChild(childRow);

                        // Show Docker stacks using this LVM child's mount
                        if (child.mountpoint) {
                            var childStacks = getStacksForPath(child.mountpoint);
                            if (childStacks.length > 0) {
                                var childStackRow = document.createElement('div');
                                childStackRow.style.cssText = 'display: flex; gap: 4px; flex-wrap: wrap; padding: 2px 0 2px 24px;';
                                childStacks.forEach(function(stack) {
                                    var badge = document.createElement('span');
                                    badge.style.cssText = 'padding: 1px 5px; font-size: 0.65rem; border-radius: 3px; background: ' + (stack.color || '#6366f1') + '33; color: ' + (stack.color || '#6366f1') + '; border: 1px solid ' + (stack.color || '#6366f1') + '44;';
                                    badge.textContent = stack.display_name;
                                    badge.title = 'Docker: ' + stack.service + ' → ' + stack.container_path;
                                    childStackRow.appendChild(badge);
                                });
                                partList.appendChild(childStackRow);
                            }
                        }
                    });

                    // Show VG free space for LVM parent partitions
                    if (part.fstype === 'LVM2_member' && part.children && part.children.length > 0) {
                        var vgFreeGb = Math.max(0, (part.size_gb || 0) - childrenTotalGb);
                        if (vgFreeGb > 0.1) {
                            var freeRow = document.createElement('div');
                            freeRow.style.cssText = 'display: flex; justify-content: space-between; padding: 2px 0 2px 12px; color: var(--archie-text-secondary);';
                            var fName = document.createElement('span');
                            fName.style.cssText = 'font-weight: 500; color: var(--archie-green);';
                            fName.textContent = '\u2514 VG Free';
                            var fInfo = document.createElement('span');
                            fInfo.style.cssText = 'color: var(--archie-green);';
                            fInfo.textContent = formatDiskSize(vgFreeGb) + ' unallocated';
                            freeRow.appendChild(fName);
                            freeRow.appendChild(fInfo);
                            partList.appendChild(freeRow);
                        }
                    }
                });
                driveDiv.appendChild(partList);
            }

            diskContainer.appendChild(driveDiv);
        });
    } else {
        // Fallback to container psutil data
        var rootFs = (data.disk && data.disk.root_fs) || ((data.disk && data.disk.partitions) ? data.disk.partitions.find(function(p) { return p.mountpoint === '/' || p.mountpoint === '/app'; }) : null) || {};
        var diskTotal = rootFs.total_gb || 0;
        var diskUsed = rootFs.used_gb || 0;
        var diskPct = rootFs.usage_percent || (diskTotal > 0 ? (diskUsed / diskTotal * 100) : 0);
        var valEl = document.createElement('div');
        valEl.className = 'stat-value';
        valEl.textContent = diskPct.toFixed(1) + '%';
        diskContainer.appendChild(valEl);
        var sub = document.createElement('div');
        sub.className = 'stat-subtitle';
        sub.textContent = diskUsed.toFixed(0) + ' / ' + diskTotal.toFixed(0) + ' GB';
        diskContainer.appendChild(sub);
        var barO = document.createElement('div');
        barO.className = 'progress-bar';
        var barF = document.createElement('div');
        barF.className = 'progress-fill ' + getColorClass(diskPct, 80, 90);
        barF.style.width = diskPct + '%';
        barO.appendChild(barF);
        diskContainer.appendChild(barO);
    }

    // System Info - now from host monitor data
    document.getElementById('sys-hostname').textContent = data.system?.hostname || '--';
    document.getElementById('sys-distro').textContent = data.system?.distribution || data.system?.platform || '--';
    document.getElementById('sys-kernel').textContent = data.system?.release || '--';
    document.getElementById('sys-uptime').textContent = formatUptime(data.system?.uptime_seconds || 0);
    document.getElementById('sys-ip').textContent = data.network?.primary_ip || '--';
    // Boot time: host monitor provides "YYYY-MM-DD HH:MM" format
    const bootTime = data.system?.boot_time;
    if (bootTime) {
        // Handle both ISO format and "YYYY-MM-DD HH:MM" format
        const bootDate = new Date(bootTime.includes('T') ? bootTime : bootTime.replace(' ', 'T'));
        document.getElementById('sys-boot').textContent = isNaN(bootDate) ? bootTime : ArchieTime.format(bootTime, 'datetime');
    } else {
        document.getElementById('sys-boot').textContent = '--';
    }
    document.getElementById('sys-processes').textContent = data.system?.process_count || '--';
    document.getElementById('sys-users').textContent = data.system?.users_logged_in ?? '0';

    // Charts - only update if charts are initialized
    const now = new Date().toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    cpuHistory.push({ time: now, value: cpuPct });
    memHistory.push({ time: now, value: memPct });

    // Multi-GPU history collection — VRAM and Compute per GPU
    const gpus = data.gpus || (data.gpu?.available ? [data.gpu] : []);
    gpus.forEach((gpu, idx) => {
        if (gpu.has_detailed_stats) {
            if (gpu.vram_percent != null) {
                const vKey = `gpu_${idx}_vram`;
                if (!gpuHistory[vKey]) gpuHistory[vKey] = [];
                gpuHistory[vKey].push({ time: now, value: gpu.vram_percent });
                if (gpuHistory[vKey].length > 720) gpuHistory[vKey].shift();
            }
            if (gpu.usage_percent != null) {
                const cKey = `gpu_${idx}_compute`;
                if (!gpuHistory[cKey]) gpuHistory[cKey] = [];
                gpuHistory[cKey].push({ time: now, value: gpu.usage_percent });
                if (gpuHistory[cKey].length > 720) gpuHistory[cKey].shift();
            }
        }
    });
    if (gpus.length > 0 && gpus.some(g => g.has_detailed_stats)) {
        gpuDeviceCount = gpus.filter(g => g.has_detailed_stats).length;
    }

    if (cpuHistory.length > 720) cpuHistory.shift();
    if (memHistory.length > 720) memHistory.shift();

    if (cpuChart) {
        cpuChart.data.labels = cpuHistory.map(h => h.time);
        cpuChart.data.datasets[0].data = cpuHistory.map(h => h.value);
        cpuChart.update('none');
    }

    if (memChart) {
        memChart.data.labels = memHistory.map(h => h.time);
        memChart.data.datasets[0].data = memHistory.map(h => h.value);
        memChart.update('none');
    }

    // Update multi-GPU chart (VRAM + Compute per GPU)
    if (gpuChart) {
        const allKeys = Object.keys(gpuHistory).sort();
        // Find longest array for labels
        const longestKey = allKeys.reduce((a, b) =>
            (gpuHistory[a] || []).length >= (gpuHistory[b] || []).length ? a : b, allKeys[0]);
        if (allKeys.length > 0 && gpuHistory[longestKey] && gpuHistory[longestKey].length > 0) {
            gpuChart.data.labels = gpuHistory[longestKey].map(h => h.time);

            // Build datasets: VRAM lines first (solid), then Compute (dashed)
            const vramKeys = allKeys.filter(k => k.endsWith('_vram')).sort();
            const computeKeys = allKeys.filter(k => k.endsWith('_compute')).sort();
            const datasets = [];

            vramKeys.forEach(key => {
                const idx = parseInt(key.replace('gpu_', '').replace('_vram', ''));
                datasets.push({
                    label: `GPU ${idx} VRAM`,
                    data: gpuHistory[key].map(h => h.value),
                    borderColor: deviceColors[idx % deviceColors.length].border,
                    backgroundColor: deviceColors[idx % deviceColors.length].bg,
                    fill: true,
                    borderWidth: 2
                });
            });
            computeKeys.forEach(key => {
                const idx = parseInt(key.replace('gpu_', '').replace('_compute', ''));
                datasets.push({
                    label: `GPU ${idx} Compute`,
                    data: gpuHistory[key].map(h => h.value),
                    borderColor: computeColors[idx % computeColors.length].border,
                    backgroundColor: computeColors[idx % computeColors.length].bg,
                    fill: false,
                    borderWidth: 1.5,
                    borderDash: [5, 3]
                });
            });

            gpuChart.data.datasets = datasets;
            gpuChart.options.plugins.legend.display = true;
            gpuChart.update('none');
        }
    }
}

async function loadDisks() {
    try {
        const res = await fetch('/dashboard/api/disks');
        const json = await res.json();
        if (json.success) {
            // Only render container data if host monitor hasn't provided better data
            if (!hostStorageLoaded) {
                renderPartitions(json.data.partitions || []);
                renderRaid(json.data.raid);
                renderLVM(json.data.lvm);
            }
        }
    } catch (e) {
        console.error('Disks error:', e);
    }
}

async function loadProcesses() {
    try {
        const sort = document.getElementById('process-sort').value;
        const res = await fetch(`/dashboard/api/processes?sort=${sort}&limit=100`);
        const json = await res.json();
        if (json.success) {
            allProcesses = json.data || [];
            filterProcesses();

            // Show source indicator
            const srcEl = document.getElementById('process-source-badge');
            if (srcEl) {
                if (json.source === 'host') {
                    const age = json.data_age != null ? `${json.data_age}s ago` : '';
                    srcEl.innerHTML = `<span class="host-badge">HOST</span> ${age}`;
                } else {
                    srcEl.innerHTML = '<span style="color: var(--archie-accent-amber);">Container only</span>';
                }
            }
        }
    } catch (e) {
        console.error('Processes error:', e);
    }
}

async function loadServices() {
    try {
        const filter = document.getElementById('service-filter').value;
        const res = await fetch(`/dashboard/api/services?filter=${filter}`);
        const json = await res.json();
        if (json.success) renderServices(json.data || []);
    } catch (e) {
        console.error('Services error:', e);
    }
}

// ===== Hardware Tab =====

async function loadHardwareData() {
    // Show cached data immediately if available
    if (window.cachedHardwareData) {
        renderHardwareTab(window.cachedHardwareData);
        document.getElementById('hardware-data-age').textContent = '(from cache)';
    }

    // Fetch fresh data
    try {
        const res = await fetch('/dashboard/api/hardware');
        const json = await res.json();

        if (json.success && json.data) {
            window.cachedHardwareData = json.data;
            saveToCache(CACHE_KEYS.hardware, json.data);
            renderHardwareTab(json.data);
            if (json.data_age) {
                document.getElementById('hardware-data-age').textContent = '(updated ' + json.data_age + 's ago)';
            }
        } else {
            document.getElementById('hw-cpu-content').textContent = json.error || 'Hardware info not available';
        }
    } catch (e) {
        console.error('Hardware data error:', e);
        if (!window.cachedHardwareData) {
            document.getElementById('hw-cpu-content').textContent = 'Failed to load hardware info';
        }
    }
}

function renderHardwareTab(data) {
    var upgrade = data.upgrade_summary || {};
    renderHardwareSummary(data);
    renderCpuSection(data.cpu || {}, upgrade.cpu || {});
    renderHwMemorySection(data.memory || {}, data.memory_config || {}, upgrade.memory || {});
    renderGpuSection(data.gpu || [], upgrade.pci || {});
    renderMotherboardSection(data.motherboard || {});
    renderPciSlotsTable(data.pci_slots || []);
    renderSataPortsSection(data.sata_ports || {});
    renderUpgradeSummaryCompact(upgrade);
    lucide.createIcons();
}

function renderHardwareSummary(data) {
    var cpu = data.cpu || {};
    var mem = data.memory || {};
    var pci = data.pci_slots || [];
    var gpu = data.gpu || [];

    // CPU sockets
    var populated = cpu.populated_sockets || 0;
    var maxSockets = cpu.max_processors || 1;
    document.getElementById('hw-cpu-sockets').textContent = populated + '/' + maxSockets;
    document.getElementById('hw-cpu-socket-type').textContent = cpu.socket_type || 'Unknown';

    // Memory slots
    var memPopulated = mem.populated_slots || 0;
    var memTotal = mem.total_slots || 0;
    document.getElementById('hw-mem-slots').textContent = memPopulated + '/' + memTotal;
    document.getElementById('hw-mem-capacity').textContent = mem.total_capacity_gb ? mem.total_capacity_gb + ' GB installed' : 'Unknown';

    // PCI slots
    var pciAvailable = 0;
    for (var i = 0; i < pci.length; i++) {
        if ((pci[i].current_usage || '').toLowerCase() === 'available') pciAvailable++;
    }
    document.getElementById('hw-pci-slots').textContent = pciAvailable + '/' + pci.length;
    document.getElementById('hw-pci-available').textContent = pciAvailable > 0 ? pciAvailable + ' available' : 'All in use';

    // GPU
    document.getElementById('hw-gpu-count').textContent = gpu.length > 0 ? gpu.length : '0';
    document.getElementById('hw-gpu-model').textContent = gpu.length > 0 ? (gpu[0].model || 'Unknown') : 'No GPU detected';
}

function renderCpuSection(cpu, cpuUpgrade) {
    var container = document.getElementById('hw-cpu-content');
    var procs = cpu.processors || [];

    if (procs.length === 0 && !cpu.model) {
        container.textContent = 'CPU hardware information not available';
        return;
    }

    var proc = procs[0] || {};
    var model = proc.model || cpu.model || 'Unknown';
    var cores = proc.cores || cpu.physical_cores || '--';
    var threads = proc.threads || cpu.logical_cores || '--';

    container.textContent = '';

    // Current CPU info
    var currentSection = document.createElement('div');
    currentSection.style.cssText = 'margin-bottom: 15px;';

    var currentLabel = document.createElement('div');
    currentLabel.style.cssText = 'font-size: 0.7rem; text-transform: uppercase; color: var(--archie-text-muted); margin-bottom: 8px;';
    currentLabel.textContent = 'Current Processor';
    currentSection.appendChild(currentLabel);

    var grid = document.createElement('div');
    grid.className = 'info-grid';
    grid.style.gridTemplateColumns = 'repeat(2, 1fr)';

    function addItem(label, value, color) {
        var item = document.createElement('div');
        item.className = 'info-item';
        var lbl = document.createElement('span');
        lbl.className = 'info-label';
        lbl.textContent = label;
        var val = document.createElement('span');
        val.className = 'info-value';
        val.textContent = value;
        if (color) val.style.color = color;
        item.appendChild(lbl);
        item.appendChild(val);
        grid.appendChild(item);
    }

    addItem('Model', model);
    addItem('Socket', cpu.socket_type || proc.socket || '--');
    addItem('Cores / Threads', cores + 'C / ' + threads + 'T');
    addItem('Current Speed', cpu.current_speed_mhz ? cpu.current_speed_mhz + ' MHz' : '--');
    addItem('Max Speed', cpu.max_speed_mhz ? cpu.max_speed_mhz + ' MHz' : '--');
    addItem('Voltage', cpu.voltage || '--');

    currentSection.appendChild(grid);
    container.appendChild(currentSection);

    // Upgrade info section
    if (cpuUpgrade && (cpuUpgrade.max_supported || cpuUpgrade.upgrade_note)) {
        var upgradeSection = document.createElement('div');
        upgradeSection.style.cssText = 'padding: 12px; background: var(--archie-bg-tertiary); border-radius: 8px; border-left: 3px solid var(--archie-green);';

        var upgradeLabel = document.createElement('div');
        upgradeLabel.style.cssText = 'font-size: 0.7rem; text-transform: uppercase; color: var(--archie-green); margin-bottom: 8px; font-weight: 600;';
        upgradeLabel.textContent = 'Maximum Supported CPU';
        upgradeSection.appendChild(upgradeLabel);

        if (cpuUpgrade.max_supported) {
            var maxDiv = document.createElement('div');
            maxDiv.style.cssText = 'font-size: 0.95rem; font-weight: 500; margin-bottom: 6px;';
            maxDiv.textContent = cpuUpgrade.max_supported;
            upgradeSection.appendChild(maxDiv);
        }

        var detailsGrid = document.createElement('div');
        detailsGrid.style.cssText = 'display: flex; gap: 20px; font-size: 0.8rem; margin-bottom: 6px;';

        if (cpuUpgrade.max_tdp) {
            var tdpSpan = document.createElement('span');
            tdpSpan.innerHTML = '<span style="color: var(--archie-text-muted);">Max TDP:</span> ' + cpuUpgrade.max_tdp + 'W';
            detailsGrid.appendChild(tdpSpan);
        }

        if (cpuUpgrade.socket_type) {
            var socketSpan = document.createElement('span');
            socketSpan.innerHTML = '<span style="color: var(--archie-text-muted);">Socket:</span> ' + cpuUpgrade.socket_type;
            detailsGrid.appendChild(socketSpan);
        }

        upgradeSection.appendChild(detailsGrid);

        if (cpuUpgrade.upgrade_note) {
            var noteDiv = document.createElement('div');
            noteDiv.style.cssText = 'font-size: 0.8rem; color: var(--archie-text-muted); font-style: italic;';
            noteDiv.textContent = cpuUpgrade.upgrade_note;
            upgradeSection.appendChild(noteDiv);
        }

        container.appendChild(upgradeSection);
    }
}

function renderHwMemorySection(mem, memConfig, memUpgrade) {
    var container = document.getElementById('hw-memory-content');

    if (!mem.total_slots && !mem.total_capacity_gb) {
        container.textContent = 'Memory hardware information not available';
        return;
    }

    var totalSlots = mem.total_slots || 8;
    var populatedSlots = mem.populated_slots || 0;
    var maxCapacity = mem.max_capacity_gb || 0;
    var currentCapacity = mem.total_capacity_gb || 0;
    var dimms = mem.dimms || [];
    var memType = mem.memory_type || 'DDR';
    var memSpeed = mem.max_speed_mhz || 0;

    container.textContent = '';

    // Two-column layout: Current on left, Optimal on right
    var mainGrid = document.createElement('div');
    mainGrid.style.cssText = 'display: grid; grid-template-columns: 1fr 1fr; gap: 20px;';

    // === CURRENT CONFIGURATION (Left) ===
    var currentSection = document.createElement('div');

    var currentHeader = document.createElement('div');
    currentHeader.style.cssText = 'font-size: 0.7rem; text-transform: uppercase; color: var(--archie-cyan); margin-bottom: 10px; font-weight: 600; padding-bottom: 6px; border-bottom: 2px solid var(--archie-cyan);';
    currentHeader.textContent = 'Current Configuration';
    currentSection.appendChild(currentHeader);

    // Summary line
    var currentSummary = document.createElement('div');
    currentSummary.style.cssText = 'font-size: 1.1rem; font-weight: 600; margin-bottom: 10px;';
    currentSummary.textContent = currentCapacity + ' GB (' + populatedSlots + '/' + totalSlots + ' slots)';
    currentSection.appendChild(currentSummary);

    // Slot list
    var slotList = document.createElement('div');
    slotList.style.cssText = 'font-size: 0.8rem;';

    for (var i = 0; i < totalSlots; i++) {
        var slotDiv = document.createElement('div');
        slotDiv.style.cssText = 'padding: 4px 0; display: flex; justify-content: space-between; border-bottom: 1px solid var(--archie-border-subtle);';

        var slotLabel = document.createElement('span');
        slotLabel.style.color = 'var(--archie-text-muted)';
        slotLabel.textContent = 'Slot ' + (i + 1);

        var slotValue = document.createElement('span');
        if (dimms[i] && dimms[i].size_gb) {
            slotValue.textContent = dimms[i].size_gb + ' GB ' + (dimms[i].type || memType) + ' @ ' + (dimms[i].speed_mhz || memSpeed) + ' MT/s';
        } else if (i < populatedSlots) {
            // Fallback if we don't have individual DIMM data
            var avgSize = Math.round(currentCapacity / populatedSlots);
            slotValue.textContent = avgSize + ' GB ' + memType + ' @ ' + memSpeed + ' MT/s';
        } else {
            slotValue.style.color = 'var(--archie-text-muted)';
            slotValue.textContent = 'Empty';
        }

        slotDiv.appendChild(slotLabel);
        slotDiv.appendChild(slotValue);
        slotList.appendChild(slotDiv);
    }

    currentSection.appendChild(slotList);
    mainGrid.appendChild(currentSection);

    // === OPTIMAL CONFIGURATION (Right) ===
    var optimalSection = document.createElement('div');

    var optimalHeader = document.createElement('div');
    optimalHeader.style.cssText = 'font-size: 0.7rem; text-transform: uppercase; color: var(--archie-green); margin-bottom: 10px; font-weight: 600; padding-bottom: 6px; border-bottom: 2px solid var(--archie-green);';
    optimalHeader.textContent = 'Optimal / Maximum';
    optimalSection.appendChild(optimalHeader);

    // Summary line
    var optimalSummary = document.createElement('div');
    optimalSummary.style.cssText = 'font-size: 1.1rem; font-weight: 600; margin-bottom: 10px; color: var(--archie-green);';
    optimalSummary.textContent = maxCapacity + ' GB (' + totalSlots + '/' + totalSlots + ' slots)';
    optimalSection.appendChild(optimalSummary);

    // Optimal slot list
    var optimalList = document.createElement('div');
    optimalList.style.cssText = 'font-size: 0.8rem;';

    var maxPerSlot = Math.floor(maxCapacity / totalSlots);

    for (var j = 0; j < totalSlots; j++) {
        var optSlotDiv = document.createElement('div');
        optSlotDiv.style.cssText = 'padding: 4px 0; display: flex; justify-content: space-between; border-bottom: 1px solid var(--archie-border-subtle);';

        var optSlotLabel = document.createElement('span');
        optSlotLabel.style.color = 'var(--archie-text-muted)';
        optSlotLabel.textContent = 'Slot ' + (j + 1);

        var optSlotValue = document.createElement('span');
        optSlotValue.style.color = 'var(--archie-green)';
        optSlotValue.textContent = maxPerSlot + ' GB ' + memType + ' (max)';

        optSlotDiv.appendChild(optSlotLabel);
        optSlotDiv.appendChild(optSlotValue);
        optimalList.appendChild(optSlotDiv);
    }

    optimalSection.appendChild(optimalList);
    mainGrid.appendChild(optimalSection);

    container.appendChild(mainGrid);

    // Upgrade recommendation footer
    if (memUpgrade && (memUpgrade.expandable_gb > 0 || memUpgrade.empty_slots > 0)) {
        var upgradeFooter = document.createElement('div');
        upgradeFooter.style.cssText = 'margin-top: 15px; padding: 10px; background: var(--archie-bg-tertiary); border-radius: 6px; border-left: 3px solid var(--archie-green); font-size: 0.85rem;';

        var upgradeText = 'Upgrade Potential: ';
        if (memUpgrade.empty_slots > 0) {
            upgradeText += memUpgrade.empty_slots + ' empty slot' + (memUpgrade.empty_slots !== 1 ? 's' : '');
        }
        if (memUpgrade.expandable_gb > 0) {
            if (memUpgrade.empty_slots > 0) upgradeText += ', ';
            upgradeText += '+' + memUpgrade.expandable_gb + ' GB available';
        }

        upgradeFooter.innerHTML = '<span style="color: var(--archie-green); font-weight: 600;">&#x2713;</span> ' + upgradeText;
        container.appendChild(upgradeFooter);
    }

    // Memory config recommendation if available
    if (memConfig && memConfig.optimal && memConfig.optimal.recommendation) {
        var recFooter = document.createElement('div');
        recFooter.style.cssText = 'margin-top: 10px; font-size: 0.8rem; color: var(--archie-text-muted); font-style: italic;';
        recFooter.textContent = memConfig.optimal.recommendation;
        container.appendChild(recFooter);
    }
}

function renderGpuSection(gpus, pciUpgrade) {
    var container = document.getElementById('hw-gpu-content');
    container.textContent = '';

    // Current GPU section
    var currentSection = document.createElement('div');
    currentSection.style.cssText = 'margin-bottom: 15px;';

    var currentLabel = document.createElement('div');
    currentLabel.style.cssText = 'font-size: 0.7rem; text-transform: uppercase; color: var(--archie-text-muted); margin-bottom: 8px;';
    currentLabel.textContent = 'Current Graphics';
    currentSection.appendChild(currentLabel);

    if (!gpus || gpus.length === 0) {
        var noGpu = document.createElement('div');
        noGpu.style.cssText = 'color: var(--archie-text-muted); font-style: italic;';
        noGpu.textContent = 'No dedicated GPU detected';
        currentSection.appendChild(noGpu);
    } else {
        for (var idx = 0; idx < gpus.length; idx++) {
            var gpu = gpus[idx];
            var grid = document.createElement('div');
            grid.className = 'info-grid';
            grid.style.gridTemplateColumns = 'repeat(2, 1fr)';

            function addGpuItem(label, value) {
                var item = document.createElement('div');
                item.className = 'info-item';
                var lbl = document.createElement('span');
                lbl.className = 'info-label';
                lbl.textContent = label;
                var val = document.createElement('span');
                val.className = 'info-value';
                val.textContent = value;
                item.appendChild(lbl);
                item.appendChild(val);
                grid.appendChild(item);
            }

            addGpuItem('Model', gpu.model || 'Unknown');
            addGpuItem('Vendor', gpu.vendor || '--');

            if (gpu.vram_total_mb) {
                addGpuItem('VRAM', Math.round(gpu.vram_total_mb / 1024) + ' GB');
            }
            if (gpu.driver) {
                addGpuItem('Driver', gpu.driver);
            }

            currentSection.appendChild(grid);
        }
    }

    container.appendChild(currentSection);

    // PCIe Slot specs section
    var gpuSlot = pciUpgrade.gpu_slot_info || {};
    if (gpuSlot.primary_slot || gpuSlot.pcie_version) {
        var slotSection = document.createElement('div');
        slotSection.style.cssText = 'padding: 12px; background: var(--archie-bg-tertiary); border-radius: 8px; border-left: 3px solid var(--archie-purple);';

        var slotLabel = document.createElement('div');
        slotLabel.style.cssText = 'font-size: 0.7rem; text-transform: uppercase; color: var(--archie-purple); margin-bottom: 8px; font-weight: 600;';
        slotLabel.textContent = 'GPU Slot Specifications';
        slotSection.appendChild(slotLabel);

        var slotGrid = document.createElement('div');
        slotGrid.style.cssText = 'display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; font-size: 0.85rem;';

        function addSlotStat(label, value) {
            var stat = document.createElement('div');
            stat.innerHTML = '<div style="color: var(--archie-text-muted); font-size: 0.7rem; text-transform: uppercase;">' + label + '</div>' +
                '<div style="font-weight: 500;">' + (value || '--') + '</div>';
            slotGrid.appendChild(stat);
        }

        addSlotStat('Slot', gpuSlot.primary_slot);
        addSlotStat('PCIe Version', gpuSlot.pcie_version ? gpuSlot.pcie_version : null);
        addSlotStat('Lanes', gpuSlot.lanes ? 'x' + gpuSlot.lanes : null);
        addSlotStat('Bandwidth', gpuSlot.bandwidth);
        addSlotStat('Max Length', gpuSlot.max_length || 'Full-length');

        slotSection.appendChild(slotGrid);

        if (gpuSlot.power_note) {
            var powerNote = document.createElement('div');
            powerNote.style.cssText = 'font-size: 0.75rem; color: var(--archie-text-muted); margin-top: 10px; font-style: italic;';
            powerNote.textContent = gpuSlot.power_note;
            slotSection.appendChild(powerNote);
        }

        container.appendChild(slotSection);
    }
}

function renderMotherboardSection(mb) {
    var container = document.getElementById('hw-motherboard-content');

    if (!mb.system_product && !mb.product_name && !mb.manufacturer) {
        container.textContent = 'Motherboard information not available';
        return;
    }

    container.textContent = '';
    var grid = document.createElement('div');
    grid.className = 'info-grid';
    grid.style.gridTemplateColumns = 'repeat(2, 1fr)';

    function addItem(label, value) {
        var item = document.createElement('div');
        item.className = 'info-item';
        var lbl = document.createElement('span');
        lbl.className = 'info-label';
        lbl.textContent = label;
        var val = document.createElement('span');
        val.className = 'info-value';
        val.textContent = value;
        item.appendChild(lbl);
        item.appendChild(val);
        grid.appendChild(item);
    }

    addItem('System', mb.system_product || '--');
    addItem('Manufacturer', mb.system_manufacturer || mb.manufacturer || '--');
    addItem('Board', mb.product_name || '--');
    addItem('Chipset', mb.chipset || '--');

    if (mb.bios_vendor || mb.bios_version) {
        var biosInfo = (mb.bios_vendor || '') + ' ' + (mb.bios_version || '');
        if (mb.bios_date) {
            biosInfo += ' (' + mb.bios_date + ')';
        }
        addItem('BIOS', biosInfo);
    }

    container.appendChild(grid);
}

function renderPciSlotsTable(slots) {
    var tbody = document.getElementById('pci-slots-body');
    tbody.textContent = '';

    if (!slots || slots.length === 0) {
        var tr = document.createElement('tr');
        var td = document.createElement('td');
        td.colSpan = 5;
        td.style.cssText = 'text-align: center; color: var(--archie-text-muted);';
        td.textContent = 'No PCI slot information available';
        tr.appendChild(td);
        tbody.appendChild(tr);
        return;
    }

    for (var i = 0; i < slots.length; i++) {
        var slot = slots[i];
        var usage = (slot.current_usage || '').toLowerCase();
        var isAvailable = usage === 'available' || usage === 'empty';

        var tr = document.createElement('tr');
        if (isAvailable) {
            tr.style.background = 'var(--archie-green-alpha-10)';
        }

        var tdSlot = document.createElement('td');
        var code = document.createElement('code');
        code.textContent = slot.designation || '--';
        tdSlot.appendChild(code);
        tr.appendChild(tdSlot);

        var tdType = document.createElement('td');
        tdType.textContent = slot.type || '--';
        tr.appendChild(tdType);

        var tdLength = document.createElement('td');
        tdLength.textContent = slot.length || '--';
        tr.appendChild(tdLength);

        var tdStatus = document.createElement('td');
        var badge = document.createElement('span');
        badge.className = 'badge';
        if (isAvailable) {
            badge.classList.add('badge-success');
            badge.textContent = 'Available';
        } else {
            badge.style.cssText = 'background: var(--archie-cyan-alpha-20); color: var(--archie-cyan);';
            badge.textContent = 'In Use';
        }
        tdStatus.appendChild(badge);
        tr.appendChild(tdStatus);

        var tdDevice = document.createElement('td');
        tdDevice.textContent = slot.device || (isAvailable ? '—' : 'Unknown device');
        if (!slot.device) tdDevice.style.color = 'var(--archie-text-muted)';
        tr.appendChild(tdDevice);

        tbody.appendChild(tr);
    }
}

function renderSataPortsSection(sata) {
    var container = document.getElementById('hw-sata-content');
    container.textContent = '';

    // Summary row
    var summary = document.createElement('div');
    summary.style.cssText = 'display: flex; gap: 30px; margin-bottom: 15px; padding: 15px; background: var(--archie-bg-tertiary); border-radius: 8px;';

    var totalPorts = sata.total_ports || 0;
    var usedPorts = sata.used_ports || 0;
    var availPorts = sata.available_ports || 0;

    function addStat(label, value, color) {
        var stat = document.createElement('div');
        stat.innerHTML = '<div style="font-size: 0.75rem; color: var(--archie-text-muted); text-transform: uppercase;">' + label + '</div>' +
            '<div style="font-size: 1.5rem; font-weight: 600; color: ' + color + ';">' + value + '</div>';
        summary.appendChild(stat);
    }

    addStat('Total Ports', totalPorts, 'var(--archie-text-primary)');
    addStat('In Use', usedPorts, 'var(--archie-cyan)');
    addStat('Available', availPorts, availPorts > 0 ? 'var(--archie-green)' : 'var(--archie-text-muted)');

    container.appendChild(summary);

    // Ports table
    var ports = sata.ports || [];
    if (ports.length === 0) {
        var noData = document.createElement('div');
        noData.style.cssText = 'color: var(--archie-text-muted); text-align: center; padding: 20px;';
        noData.textContent = 'No SATA port information available';
        container.appendChild(noData);
        return;
    }

    var table = document.createElement('table');
    table.style.cssText = 'width: 100%; border-collapse: collapse; background: #2d2d3a; border-radius: 8px; overflow: hidden;';

    var thead = document.createElement('thead');
    thead.innerHTML = '<tr style="border-bottom: 1px solid #3d3d4a; background: #252532;">' +
        '<th style="padding: 10px; text-align: left; color: #888; font-weight: 500; font-size: 0.8rem; text-transform: uppercase;">Port</th>' +
        '<th style="padding: 10px; text-align: left; color: #888; font-weight: 500; font-size: 0.8rem; text-transform: uppercase;">Status</th>' +
        '<th style="padding: 10px; text-align: left; color: #888; font-weight: 500; font-size: 0.8rem; text-transform: uppercase;">Device</th>' +
        '<th style="padding: 10px; text-align: left; color: #888; font-weight: 500; font-size: 0.8rem; text-transform: uppercase;">Model</th>' +
        '</tr>';
    table.appendChild(thead);

    var tbody = document.createElement('tbody');
    table.appendChild(tbody);

    for (var i = 0; i < ports.length; i++) {
        var port = ports[i];
        var tr = document.createElement('tr');
        tr.style.cssText = 'border-bottom: 1px solid #3d3d4a; background: #2d2d3a;';

        var tdPort = document.createElement('td');
        tdPort.style.cssText = 'padding: 10px; color: #e0e0e0; background: #2d2d3a;';
        tdPort.innerHTML = '<strong>' + (port.name || 'SATA ' + (port.port_number || i)) + '</strong>';
        tr.appendChild(tdPort);

        var tdStatus = document.createElement('td');
        tdStatus.style.cssText = 'padding: 10px; background: #2d2d3a;';
        var badge = document.createElement('span');
        if (port.in_use || port.device) {
            badge.style.cssText = 'background: rgba(0, 188, 212, 0.2); color: #00bcd4; padding: 4px 8px; border-radius: 4px; font-size: 0.75rem;';
            badge.textContent = 'Connected';
        } else {
            badge.style.cssText = 'background: rgba(34, 197, 94, 0.2); color: #22c55e; padding: 4px 8px; border-radius: 4px; font-size: 0.75rem;';
            badge.textContent = 'Available';
        }
        tdStatus.appendChild(badge);
        tr.appendChild(tdStatus);

        var tdDevice = document.createElement('td');
        tdDevice.style.cssText = 'padding: 10px; background: #2d2d3a; color: ' + (port.device ? '#e0e0e0' : '#666') + ';';
        tdDevice.textContent = port.device || '—';
        tr.appendChild(tdDevice);

        var tdModel = document.createElement('td');
        tdModel.style.cssText = 'padding: 10px; background: #2d2d3a; color: ' + (port.device_model ? '#e0e0e0' : '#666') + ';';
        tdModel.textContent = port.device_model || '—';
        tr.appendChild(tdModel);

        tbody.appendChild(tr);
    }

    container.appendChild(table);
}

function renderUpgradeSummaryCompact(upgrade) {
    var container = document.getElementById('hw-upgrade-content');
    var mem = upgrade.memory || {};
    var pci = upgrade.pci || {};
    var cpu = upgrade.cpu || {};
    var sata = upgrade.sata || {};

    container.textContent = '';

    // Simple summary grid - just quick stats since details are in each section
    var wrapper = document.createElement('div');
    wrapper.style.cssText = 'display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px;';

    function createMiniCard(title, value, isPositive) {
        var card = document.createElement('div');
        card.style.cssText = 'padding: 12px; background: var(--archie-bg-tertiary); border-radius: 6px; text-align: center;';

        var titleDiv = document.createElement('div');
        titleDiv.style.cssText = 'font-size: 0.7rem; color: var(--archie-text-muted); text-transform: uppercase; margin-bottom: 4px;';
        titleDiv.textContent = title;
        card.appendChild(titleDiv);

        var valDiv = document.createElement('div');
        valDiv.style.cssText = 'font-size: 1rem; font-weight: 600; color: ' + (isPositive ? 'var(--archie-green)' : 'var(--archie-text-muted)') + ';';
        valDiv.textContent = value;
        card.appendChild(valDiv);

        return card;
    }

    // Memory
    var memExpand = mem.expandable_gb || 0;
    wrapper.appendChild(createMiniCard('Memory', memExpand > 0 ? '+' + memExpand + ' GB' : 'Maxed', memExpand > 0));

    // PCI
    var pciAvail = pci.available_slots || 0;
    wrapper.appendChild(createMiniCard('PCI Slots', pciAvail > 0 ? pciAvail + ' Free' : 'Full', pciAvail > 0));

    // SATA
    var sataAvail = sata.available_ports || 0;
    wrapper.appendChild(createMiniCard('SATA', sataAvail > 0 ? sataAvail + ' Free' : 'Full', sataAvail > 0));

    // CPU
    var cpuCanAdd = cpu.can_add_cpu;
    wrapper.appendChild(createMiniCard('CPU', cpuCanAdd ? '+1 Socket' : '1/1', cpuCanAdd));

    container.appendChild(wrapper);

    // Note that details are above
    var noteDiv = document.createElement('div');
    noteDiv.style.cssText = 'margin-top: 10px; font-size: 0.75rem; color: var(--archie-text-muted); text-align: center;';
    noteDiv.textContent = 'See detailed upgrade info in each section above';
    container.appendChild(noteDiv);
}

// ===== Memory Hardware =====
let memoryHardwareLoaded = false;

// Load cached data immediately on page load
function loadCachedMemoryHardware() {
    try {
        const cached = localStorage.getItem('archie_memory_hardware');
        if (cached) {
            const data = JSON.parse(cached);
            renderMemoryHardware(data);
            memoryHardwareLoaded = true;
            console.log('Loaded cached memory hardware data');
        }
    } catch (e) {
        console.log('No cached memory hardware data');
    }
}

async function loadMemoryHardware() {
    try {
        const res = await fetch('/dashboard/api/memory-hardware');
        const json = await res.json();

        if (json.success && json.data) {
            renderMemoryHardware(json.data);
            memoryHardwareLoaded = true;
            // Cache the data for instant load next time
            localStorage.setItem('archie_memory_hardware', JSON.stringify(json.data));
        } else {
            document.getElementById('dimm-table-body').innerHTML = `
                <tr>
                    <td colspan="8" style="text-align: center; color: var(--archie-text-muted);">
                        ${json.error || 'Memory hardware info not available'}
                    </td>
                </tr>
            `;
        }
    } catch (e) {
        console.error('Memory hardware error:', e);
        document.getElementById('dimm-table-body').innerHTML = `
            <tr>
                <td colspan="8" style="text-align: center; color: var(--archie-danger);">
                    Failed to load memory hardware info
                </td>
            </tr>
        `;
    }
}

function renderMemoryHardware(data) {
    // Summary cards
    document.getElementById('mem-hw-total').textContent = data.total_capacity_gb
        ? (data.total_capacity_gb >= 1024 ? (data.total_capacity_gb / 1024).toFixed(1) + ' TB' : data.total_capacity_gb + ' GB')
        : '--';
    document.getElementById('mem-hw-slots').textContent = `${data.populated_slots || 0} of ${data.total_slots || '?'} slots used`;

    document.getElementById('mem-hw-speed').textContent = data.max_speed_mhz ? data.max_speed_mhz + ' MT/s' : '--';
    document.getElementById('mem-hw-type').textContent = data.memory_type || '--';

    document.getElementById('mem-hw-channels').textContent = data.channels || '--';
    document.getElementById('mem-hw-config').textContent = data.populated_slots
        ? `${data.populated_slots} DIMM${data.populated_slots > 1 ? 's' : ''} installed`
        : '--';

    document.getElementById('mem-hw-max').textContent = data.max_capacity_gb
        ? (data.max_capacity_gb >= 1024 ? (data.max_capacity_gb / 1024).toFixed(0) + ' TB' : data.max_capacity_gb + ' GB')
        : '--';
    const expandable = data.max_capacity_gb && data.total_capacity_gb
        ? data.max_capacity_gb - data.total_capacity_gb
        : 0;
    document.getElementById('mem-hw-expandable').textContent = expandable > 0
        ? `+${expandable} GB available`
        : 'Fully populated';

    // DIMM count badge
    document.getElementById('memory-dimm-count').textContent = `(${data.populated_slots || 0} DIMMs)`;

    // Controller info
    document.getElementById('mem-ctrl-ecc').textContent = data.ecc_supported ? 'ECC Supported' : 'Non-ECC';
    document.getElementById('mem-ctrl-supported').textContent = data.memory_type || '--';
    document.getElementById('mem-ctrl-maxspeed').textContent = data.max_speed_mhz ? data.max_speed_mhz + ' MT/s' : '--';
    document.getElementById('mem-ctrl-type').textContent = data.channels || 'Unknown';

    // DIMM table
    const tbody = document.getElementById('dimm-table-body');
    if (!data.dimms || data.dimms.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align: center; color: var(--archie-text-muted);">
                    No DIMM information available
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = data.dimms.map(dimm => {
        const populated = dimm.populated;
        const rowClass = populated ? '' : 'style="opacity: 0.5;"';
        const statusBadge = populated
            ? '<span class="badge badge-success">Populated</span>'
            : '<span class="badge" style="background: var(--archie-bg-tertiary);">Empty</span>';

        return `
            <tr ${rowClass}>
                <td><code>${dimm.slot || '--'}</code></td>
                <td>${populated && dimm.size_gb ? dimm.size_gb + ' GB' : '--'}</td>
                <td>${dimm.type || '--'}</td>
                <td>${dimm.configured_speed_mhz ? dimm.configured_speed_mhz + ' MT/s' : (dimm.speed_mhz ? dimm.speed_mhz + ' MT/s' : '--')}</td>
                <td>${dimm.manufacturer || '--'}</td>
                <td><code style="font-size: 0.75rem;">${dimm.part_number || '--'}</code></td>
                <td><code style="font-size: 0.7rem; color: var(--archie-text-muted);">${dimm.serial ? dimm.serial.substring(0, 12) : '--'}</code></td>
                <td>${statusBadge}</td>
            </tr>
        `;
    }).join('');
}

// Dashboard memory hardware summary (compact view)
let dashboardMemHwLoaded = false;

function renderDashboardMemoryHardware(data) {
    document.getElementById('dash-mem-dimms').textContent =
        `${data.populated_slots || 0}/${data.total_slots || '?'} slots`;
    document.getElementById('dash-mem-type').textContent =
        data.memory_type || '--';
    document.getElementById('dash-mem-speed').textContent =
        data.max_speed_mhz ? data.max_speed_mhz + ' MT/s' : '--';
    document.getElementById('dash-mem-channels').textContent =
        data.channels || '--';
}

async function loadDashboardMemoryHardware() {
    // Load from cache first for instant display
    try {
        const cached = localStorage.getItem('archie_memory_hardware');
        if (cached && !dashboardMemHwLoaded) {
            renderDashboardMemoryHardware(JSON.parse(cached));
        }
    } catch (e) {}

    if (dashboardMemHwLoaded) return;  // Only fetch once
    try {
        const res = await fetch('/dashboard/api/memory-hardware');
        const json = await res.json();
        if (json.success && json.data) {
            renderDashboardMemoryHardware(json.data);
            // Cache for next page load
            localStorage.setItem('archie_memory_hardware', JSON.stringify(json.data));
            dashboardMemHwLoaded = true;
        }
    } catch (e) {
        console.error('Dashboard memory hardware error:', e);
    }
}

// ===== Health Alerts Functions =====

async function loadHealthAlerts() {
    try {
        const res = await fetch('/dashboard/api/health/alerts');
        const data = await res.json();
        if (data.success) {
            renderHealthAlerts(data.alerts || [], data.counts || {});
        }
    } catch (e) {
        console.error('Health alerts error:', e);
    }
}

function renderHealthAlerts(alerts, counts) {
    const panel = document.getElementById('health-alerts-panel');
    const badge = document.getElementById('health-alert-badge');

    const totalAlerts = counts.total || 0;

    // Update badge
    if (totalAlerts > 0) {
        badge.textContent = totalAlerts;
        badge.style.display = 'inline';
    } else {
        badge.style.display = 'none';
    }

    // Clear panel
    panel.textContent = '';

    if (!alerts || alerts.length === 0) {
        const emptyState = document.createElement('div');
        emptyState.className = 'empty-state';
        emptyState.style.cssText = 'padding: var(--archie-space-4); text-align: center; color: var(--archie-text-muted);';

        const icon = document.createElement('i');
        icon.setAttribute('data-lucide', 'check-circle');
        icon.style.cssText = 'width: 24px; height: 24px; color: var(--archie-green);';
        emptyState.appendChild(icon);

        const p = document.createElement('p');
        p.style.marginTop = 'var(--archie-space-2)';
        p.textContent = 'No active alerts';
        emptyState.appendChild(p);
        panel.appendChild(emptyState);
        if (typeof lucide !== 'undefined') lucide.createIcons();
        return;
    }

    alerts.forEach(alert => {
        const item = document.createElement('div');
        item.className = 'health-alert-item ' + alert.severity;

        const content = document.createElement('div');
        content.className = 'health-alert-content';

        // Header row
        const header = document.createElement('div');
        header.className = 'health-alert-header';

        const severityBadge = document.createElement('span');
        severityBadge.className = 'health-alert-severity ' + alert.severity;
        severityBadge.textContent = alert.severity;

        const container = document.createElement('span');
        container.className = 'health-alert-container';
        container.textContent = alert.container_name || alert.stack_name;

        header.appendChild(severityBadge);
        header.appendChild(container);

        // Message
        const message = document.createElement('div');
        message.className = 'health-alert-message';
        message.textContent = alert.message;

        // Time
        const time = document.createElement('div');
        time.className = 'health-alert-time';
        time.textContent = ArchieTime.format(alert.created_at, 'short');

        content.appendChild(header);
        content.appendChild(message);
        content.appendChild(time);

        // Actions
        const actions = document.createElement('div');
        actions.className = 'health-alert-actions';

        if (!alert.acknowledged) {
            const ackBtn = document.createElement('button');
            ackBtn.className = 'btn btn-sm btn-ghost';
            ackBtn.textContent = 'Ack';
            ackBtn.onclick = () => acknowledgeAlert(alert.id);
            actions.appendChild(ackBtn);
        }

        const resolveBtn = document.createElement('button');
        resolveBtn.className = 'btn btn-sm btn-primary';
        resolveBtn.textContent = 'Resolve';
        resolveBtn.onclick = () => resolveAlert(alert.id);
        actions.appendChild(resolveBtn);

        item.appendChild(content);
        item.appendChild(actions);
        panel.appendChild(item);
    });
}

async function acknowledgeAlert(alertId) {
    try {
        const res = await fetch('/dashboard/api/health/alerts/' + alertId + '/acknowledge', {
            method: 'POST'
        });
        const data = await res.json();
        if (data.success) {
            showToast('success', 'Alert acknowledged');
            loadHealthAlerts();
        } else {
            showToast('error', data.error || 'Failed to acknowledge alert');
        }
    } catch (e) {
        showToast('error', 'Error: ' + e.message);
    }
}

async function resolveAlert(alertId) {
    try {
        const res = await fetch('/dashboard/api/health/alerts/' + alertId + '/resolve', {
            method: 'POST'
        });
        const data = await res.json();
        if (data.success) {
            showToast('success', 'Alert resolved');
            loadHealthAlerts();
        } else {
            showToast('error', data.error || 'Failed to resolve alert');
        }
    } catch (e) {
        showToast('error', 'Error: ' + e.message);
    }
}

function showAlertConfigModal() {
    // Show info about configuration
    showToast('info', 'Alert thresholds: CPU 80%/95%, Memory 85%/95%, Restarts 3/5. Configure via API.');
}

// ===== Multi-Stack State =====
let currentStackName = null;
let stacksData = [];

async function loadDocker() {
    // Show cached data immediately if available
    if (window.cachedDockerData && window.cachedDockerData.length > 0) {
        stacksData = window.cachedDockerData;
        renderStackOverview(stacksData);
    }

    // Fetch fresh data
    try {
        // Fetch stacks and health data in parallel
        const [stacksRes, healthRes] = await Promise.all([
            fetch('/dashboard/api/stacks'),
            fetch('/dashboard/api/docker/health')
        ]);
        const stacksJson = await stacksRes.json();
        const healthJson = await healthRes.json();

        if (stacksJson.success) {
            stacksData = stacksJson.data || [];

            // Merge health data if available
            if (healthJson.success && healthJson.stacks) {
                const healthMap = {};
                healthJson.stacks.forEach(h => { healthMap[h.name] = h; });

                stacksData.forEach(s => {
                    // Try matching by stack name first
                    let health = healthMap[s.name];

                    // If no match, try compose directory basename (Docker uses dir name as project)
                    if (!health && s.compose_directory) {
                        const dirName = s.compose_directory.split('/').pop();
                        health = healthMap[dirName];
                    }

                    // If still no match, try removing common suffixes like -stack
                    if (!health) {
                        const baseName = s.name.replace(/-stack$/, '');
                        health = healthMap[baseName];
                    }

                    if (health) {
                        s.health_summary = health.health_summary;
                        s.total_cpu = health.total_cpu;
                        s.total_mem = health.total_mem;
                        s.restart_total = health.restart_total;
                    }
                });
            }

            // Save to cache
            window.cachedDockerData = stacksData;
            saveToCache(CACHE_KEYS.docker, stacksData);

            renderStackOverview(stacksData);
            // If detail view is active, refresh it too
            if (currentStackName) loadStackDetail(currentStackName);
        }
    } catch (e) {
        console.error('Docker/Stacks error:', e);
    }
}

async function loadNetwork() {
    try {
        const res = await fetch('/dashboard/api/network');
        const json = await res.json();
        if (json.success) renderNetwork(json.data || {});
        // Reset lazy-load flags and re-fetch active sub-tab
        portMapLoaded = false;
        topologyLoaded = false;
        bandwidthLoaded = false;
        const activeBtn = document.querySelector('.net-subtab.active');
        if (activeBtn) {
            const tabName = activeBtn.id.replace('net-subtab-btn-', '');
            if (tabName === 'portmap') loadPortMap();
            else if (tabName === 'topology') loadTopology();
            else if (tabName === 'bandwidth') loadBandwidthData();
        }
    } catch (e) {
        console.error('Network error:', e);
    }
}

// ===== Storage Tab Functions =====

// Flag to skip cache after storage operations
let skipStorageCache = false;

async function loadStorageData(forceRefresh = false) {
    // Show cached data immediately if available (unless forced refresh after operation)
    if (!forceRefresh && !skipStorageCache && (window.cachedStorageData || storageData)) {
        const cached = window.cachedStorageData || storageData;
        renderStorageOverview(cached.disks || [], cached.lvm);
        renderStorageTreemap(cached.disks || [], cached.lvm, cached.vg_free || {}, cached.docker_mounts || {});
        renderStorageCharts(cached.disks || []);
        renderLVM(cached.lvm);
        renderRaid(cached.raid);
        renderSmartHealth(cached.smart_details || {});
        renderCapacityAlerts(cached.capacity_alerts || []);
    }

    // Fetch fresh data with cache-busting timestamp
    try {
        const cacheBuster = forceRefresh || skipStorageCache ? '?t=' + Date.now() : '';
        console.log('loadStorageData: fetching with forceRefresh=' + forceRefresh + ', cacheBuster=' + cacheBuster);
        const res = await fetch('/dashboard/api/storage/drives' + cacheBuster);
        const json = await res.json();
        console.log('loadStorageData: response success=' + json.success);
        if (json.success && json.data) {
            storageData = json.data;
            window.cachedStorageData = json.data;
            lastStorageRefresh = new Date();  // Track when data was refreshed
            console.log('loadStorageData: set lastStorageRefresh to', lastStorageRefresh);
            saveToCache(CACHE_KEYS.storage, json.data);
            console.log('Docker mounts received:', json.data.docker_mounts);
            renderStorageOverview(json.data.disks || [], json.data.lvm);
            renderStorageTreemap(json.data.disks || [], json.data.lvm, json.data.vg_free || {}, json.data.docker_mounts || {});
            renderStorageCharts(json.data.disks || []);
            renderLVM(json.data.lvm);
            renderRaid(json.data.raid);
            renderSmartHealth(json.data.smart_details || {});
            renderCapacityAlerts(json.data.capacity_alerts || []);
            // Update refresh timestamp display and icons
            updateStorageRefreshTime();
            lucide.createIcons();
        }
    } catch (e) {
        console.error('Storage data error:', e);
    }
    // Check host command queue status
    checkHostCommandQueue();
}

// Update the storage refresh timestamp display
function updateStorageRefreshTime() {
    const el = document.getElementById('storageRefreshTime');
    if (el && lastStorageRefresh) {
        // Format as "Last refresh: 10:22 PM" - uses browser's local timezone
        const timeStr = lastStorageRefresh.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
        el.textContent = 'Last refresh: ' + timeStr;

        // Add brief flash animation to indicate refresh happened
        el.style.transition = 'none';
        el.style.color = 'var(--archie-cyan)';
        el.style.fontWeight = '600';
        setTimeout(() => {
            el.style.transition = 'color 1s ease, font-weight 1s ease';
            el.style.color = 'var(--archie-text-muted)';
            el.style.fontWeight = 'normal';
        }, 50);
    }
}

function formatSizeGB(gb) {
    if (gb >= 1024) return (gb / 1024).toFixed(1) + ' TB';
    if (gb >= 1) return gb.toFixed(1) + ' GB';
    return (gb * 1024).toFixed(0) + ' MB';
}

function renderStorageOverview(disks, lvm) {
    // Helper to check if disk is USB/removable
    function isAttachedDrive(disk) {
        const name = (disk.name || disk.device || '').toLowerCase();
        const model = (disk.model || '').toLowerCase();
        const transport = (disk.transport || '').toLowerCase();
        const removable = disk.removable === true || disk.removable === 'true' || disk.removable === 1;
        const smart = disk.smart_health;
        // USB devices: have "usb" in model/transport, or are removable, or sd[c-z] without healthy SMART
        return transport === 'usb' || removable ||
               model.includes('usb') || model.includes('sandisk') || model.includes('flash') ||
               (name.match(/sd[c-z]/) && smart !== 'healthy');
    }

    // Separate local and attached drives
    const localDisks = disks.filter(d => !isAttachedDrive(d));
    const attachedDisks = disks.filter(d => isAttachedDrive(d));

    // Calculate stats for a set of disks
    function calcStats(diskList) {
        let total = 0, used = 0, free = 0;
        diskList.forEach(disk => {
            total += disk.size_gb || 0;
            // Check disk-level usage (for disks without partitions, formatted directly)
            if (disk.usage && disk.mountpoint) {
                used += disk.usage.used_gb || 0;
                free += disk.usage.free_gb || 0;
            }
            (disk.partitions || []).forEach(part => {
                if (part.usage) {
                    used += part.usage.used_gb || 0;
                    free += part.usage.free_gb || 0;
                }
                (part.children || []).forEach(child => {
                    if (child.usage) {
                        used += child.usage.used_gb || 0;
                        free += child.usage.free_gb || 0;
                    }
                });
            });
        });
        return { total, used, free, count: diskList.length };
    }

    const localStats = calcStats(localDisks);
    const attachedStats = calcStats(attachedDisks);

    // Update Local storage stats
    document.getElementById('storage-local-total').textContent = formatSizeGB(localStats.total);
    document.getElementById('storage-local-used').textContent = formatSizeGB(localStats.used);
    document.getElementById('storage-local-free').textContent = formatSizeGB(localStats.free);
    document.getElementById('storage-local-count').textContent = localStats.count;

    // Update Attached storage stats
    document.getElementById('storage-attached-total').textContent = formatSizeGB(attachedStats.total);
    document.getElementById('storage-attached-used').textContent = formatSizeGB(attachedStats.used);
    document.getElementById('storage-attached-count').textContent = attachedStats.count;

    // Track health status (local drives only - USB typically has no SMART)
    let healthyCount = 0, failingCount = 0;
    localDisks.forEach(disk => {
        if (disk.smart_health === 'healthy' || disk.smart_health === 'PASSED') {
            healthyCount++;
        } else if (disk.smart_health === 'failing' || disk.smart_health === 'FAILED') {
            failingCount++;
        }
    });

    const healthEl = document.getElementById('storage-health');
    if (failingCount > 0) {
        healthEl.textContent = failingCount + ' FAILING';
        healthEl.style.color = 'var(--archie-red)';
    } else if (healthyCount > 0) {
        healthEl.textContent = healthyCount + '/' + localDisks.length + ' OK';
        healthEl.style.color = 'var(--archie-green)';
    } else if (localDisks.length === 0) {
        healthEl.textContent = 'No Local';
        healthEl.style.color = 'var(--archie-text-muted)';
    } else {
        healthEl.textContent = 'Mixed';
        healthEl.style.color = 'var(--archie-orange)';
    }
}

function getUsageColor(pct) {
    if (pct >= 80) return 'var(--archie-red)';
    if (pct >= 60) return 'var(--archie-orange)';
    return 'var(--archie-green)';
}

// Get contrasting text color (black or white) based on background
function getContrastColor(hexColor) {
    if (!hexColor || hexColor.startsWith('var(')) return '#fff';
    const hex = hexColor.replace('#', '');
    const r = parseInt(hex.substr(0, 2), 16);
    const g = parseInt(hex.substr(2, 2), 16);
    const b = parseInt(hex.substr(4, 2), 16);
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.5 ? '#000' : '#fff';
}

function renderStorageTreemap(disks, lvm, vgFree, dockerMounts) {
    const container = document.getElementById('storage-treemap');
    console.log('renderStorageTreemap called with dockerMounts:', dockerMounts, 'keys:', Object.keys(dockerMounts || {}));
    if (!disks || !disks.length) {
        container.innerHTML = '<p style="color: var(--archie-text-muted); padding: 20px; text-align: center;">No drives detected</p>';
        return;
    }

    // Helper: get Docker stacks using a mountpoint
    // Uses global dockerMounts (from /api/storage/docker-mounts) which has stack colors
    // Falls back to parameter dockerMounts (from host_monitor) if global not available
    function getDockerStacks(mountpoint) {
        // Prefer global dockerMounts (has colors from database) over parameter
        const mounts = window.dockerMounts || dockerMounts;
        if (!mounts || !mountpoint) return [];
        const stacks = [];
        const seenStacks = new Set();
        // Check if any Docker mount path falls under this mountpoint
        Object.keys(mounts).forEach(hostPath => {
            let matches = false;
            if (mountpoint === '/') {
                // Root filesystem - all absolute paths fall under it
                matches = hostPath.startsWith('/');
            } else {
                matches = hostPath.startsWith(mountpoint + '/') || hostPath === mountpoint;
            }
            if (matches) {
                mounts[hostPath].forEach(item => {
                    // Handle both string (container name) and object (stack info) formats
                    let stackName, displayName, color;
                    if (typeof item === 'string') {
                        // Container name format - derive stack from prefix
                        stackName = item.includes('_') ? item.split('_')[0] : item;
                        // Use friendly names for known stacks
                        if (stackName === 'archie') displayName = 'A.R.C.H.I.E.';
                        else displayName = stackName.charAt(0).toUpperCase() + stackName.slice(1);
                        color = null; // Will use default purple
                    } else {
                        // Stack object format (has color from database)
                        stackName = item.stack_name;
                        displayName = item.display_name || stackName;
                        color = item.color;
                    }
                    if (!seenStacks.has(stackName)) {
                        seenStacks.add(stackName);
                        stacks.push({ stack_name: stackName, display_name: displayName, color: color });
                    }
                });
            }
        });
        return stacks;
    }

    // Helper: get short mountpoint label (last 2 parts of path)
    function getMountLabel(mountpoint) {
        if (!mountpoint || mountpoint === '/') return null;
        const parts = mountpoint.split('/').filter(p => p);
        if (parts.length <= 2) return mountpoint;
        return '/' + parts.slice(-2).join('/');
    }

    // Helper to check if disk is USB/removable
    function isAttachedDrive(disk) {
        const name = (disk.name || disk.device || '').toLowerCase();
        const model = (disk.model || '').toLowerCase();
        const transport = (disk.transport || '').toLowerCase();
        const removable = disk.removable === true || disk.removable === 'true' || disk.removable === 1;
        const smart = disk.smart_health;
        // USB devices: have "usb" in model/transport, or are removable, or sd[c-z] without healthy SMART
        return transport === 'usb' || removable ||
               model.includes('usb') || model.includes('sandisk') || model.includes('flash') ||
               (name.match(/sd[c-z]/) && smart !== 'healthy');
    }

    // Separate local and attached drives
    const localDisks = disks.filter(d => !isAttachedDrive(d));
    const attachedDisks = disks.filter(d => isAttachedDrive(d));

    // Helper to render a single disk
    function renderDisk(disk) {
        let diskHtml = '';
        const healthClass = disk.smart_health || 'unknown';
        const healthLabel = disk.smart_health === 'healthy' ? 'HEALTHY' :
                           disk.smart_health === 'failing' ? 'FAILING' : 'N/A';

        // Check if this is a system/boot drive (contains / or /boot mount)
        const isSystemDrive = (disk.partitions || []).some(part => {
            if (part.mountpoint === '/' || part.mountpoint === '/boot') return true;
            return (part.children || []).some(child => child.mountpoint === '/' || child.mountpoint === '/boot');
        });

        diskHtml += '<div class="treemap-drive">';
        diskHtml += '<div class="treemap-drive-header">';
        diskHtml += '<span class="treemap-drive-name">' + disk.device + '</span>';
        if (isSystemDrive) {
            diskHtml += '<span class="treemap-drive-system" style="background: var(--archie-orange); color: #000; padding: 2px 6px; border-radius: 4px; font-size: 0.65rem; font-weight: 600;">BOOT DRIVE</span>';
        }
        diskHtml += '<span class="treemap-drive-model">' + (disk.model || 'Unknown') + '</span>';
        diskHtml += '<span class="treemap-drive-health ' + healthClass + '">' + healthLabel + '</span>';
        diskHtml += '<span class="treemap-drive-size">' + formatSizeGB(disk.size_gb) + '</span>';
        // Only show Prepare as LVM for non-system drives
        if (!isSystemDrive) {
            // Pass disk object as JSON for mounted partition detection
            const diskJson = JSON.stringify({
                device: disk.device,
                stable_id: disk.stable_id,
                size_gb: disk.size_gb,
                model: disk.model,
                name: disk.name,
                partitions: (disk.partitions || []).map(p => ({
                    device: p.device,
                    mountpoint: p.mountpoint,
                    stable_id: p.stable_id
                }))
            }).replace(/'/g, "\\'").replace(/"/g, '&quot;');
            diskHtml += '<button class="btn btn-sm btn-ghost" style="margin-left: auto; padding: 4px 10px; font-size: 0.7rem;" onclick="event.stopPropagation(); openPrepareLvmModalSafe(\'' + diskJson + '\')"><i data-lucide="layers" style="width:12px;height:12px;margin-right:4px;"></i>Prepare as LVM</button>';
            // Add Wipe button for drives with partitions (data on them)
            if (disk.partitions && disk.partitions.length > 0) {
                const hasLvm = disk.partitions.some(p => p.type === 'lvm' || p.fstype === 'LVM2_member' || p.vg_name);
                if (hasLvm) {
                    const wipeInfo = disk.model || 'Drive with LVM';
                    diskHtml += '<button class="btn btn-sm btn-danger" style="padding: 4px 10px; font-size: 0.7rem;" onclick="event.stopPropagation(); openWipeModal(\'' + (disk.stable_id || disk.device || '').replace(/'/g, "\\'") + '\', ' + (disk.size_gb || 0) + ', \'' + wipeInfo.replace(/'/g, "\\'") + '\')"><i data-lucide="trash-2" style="width:12px;height:12px;margin-right:4px;"></i>Wipe</button>';
                }
            }
        }
        diskHtml += '</div>';

        diskHtml += '<div class="treemap-partitions">';

        const totalBytes = disk.size_bytes || (disk.size_gb * 1073741824);

        // Handle disks mounted directly without partitions (formatted as whole disk)
        if ((!disk.partitions || disk.partitions.length === 0) && disk.mountpoint) {
            const usagePct = disk.usage ? disk.usage.percent : 0;
            const diskDockerStacks = getStacksForPath(disk.mountpoint);
            const diskMountLabel = getMountLabel(disk.mountpoint);

            diskHtml += '<div class="treemap-partition mounted"';
            diskHtml += ' style="flex: 100;"';
            diskHtml += ' onclick="selectPartition(this, \'' + disk.name + '\', \'' + (disk.device || '').replace(/'/g, "\\'") + '\', \'disk\')"';
            diskHtml += ' title="' + disk.device + ' (' + formatSizeGB(disk.size_gb) + ') → ' + disk.mountpoint + '">';
            // Badge container
            diskHtml += '<div class="treemap-partition-badges" style="position:absolute;top:2px;right:4px;display:flex;gap:2px;flex-wrap:wrap;justify-content:flex-end;max-width:80%;">';
            if (diskMountLabel) {
                diskHtml += '<span style="background:var(--archie-cyan);color:#000;padding:1px 4px;border-radius:3px;font-size:0.5rem;font-weight:600;">' + diskMountLabel + '</span>';
            }
            if (diskDockerStacks.length > 0) {
                diskDockerStacks.forEach(stack => {
                    const bgColor = stack.color || 'var(--archie-purple)';
                    const textColor = getContrastColor(bgColor);
                    diskHtml += '<span style="background:' + bgColor + ';color:' + textColor + ';padding:1px 4px;border-radius:3px;font-size:0.5rem;font-weight:600;">🐳 ' + (stack.display_name || stack.stack_name) + '</span>';
                });
            }
            diskHtml += '</div>';
            diskHtml += '<span class="treemap-partition-label">' + (disk.label || disk.name) + '</span>';
            diskHtml += '<span class="treemap-partition-size">' + formatSizeGB(disk.size_gb) + '</span>';
            if (disk.usage) {
                diskHtml += '<div class="treemap-partition-usage">';
                diskHtml += '<div class="treemap-partition-usage-bar" style="width: ' + usagePct + '%; background: ' + getUsageColor(usagePct) + ';"></div>';
                diskHtml += '</div>';
            }
            diskHtml += '</div>';
        }

        (disk.partitions || []).forEach(part => {
            // Check if this partition has LVM children
            const hasChildren = part.children && part.children.length > 0;

            if (hasChildren) {
                // Render children (LVM logical volumes) instead
                part.children.forEach(child => {
                    const childPct = totalBytes > 0 ? (child.size_gb / disk.size_gb * 100) : 10;
                    const isMounted = !!child.mountpoint;
                    const usagePct = child.usage ? child.usage.percent : 0;
                    const stateClass = isMounted ? 'mounted' : 'unmounted';
                    const label = child.name || child.device;
                    const sizeLabel = formatSizeGB(child.size_gb);
                    // Check if this is the root filesystem
                    const isRootVolume = child.mountpoint === '/';

                    // Check for Docker stacks using this mount
                    const childDockerStacks = getStacksForPath(child.mountpoint);
                    const childMountLabel = getMountLabel(child.mountpoint);

                    diskHtml += '<div class="treemap-partition ' + stateClass + '"';
                    diskHtml += ' style="flex: ' + Math.max(childPct, 2) + ';"';
                    diskHtml += ' onclick="selectPartition(this, \'' + disk.name + '\', \'' + (child.device || '').replace(/'/g, "\\'") + '\', \'lvm\')"';
                    diskHtml += ' title="' + label + ' (' + sizeLabel + ')' + (child.mountpoint ? ' → ' + child.mountpoint : '') + (isRootVolume ? ' [ROOT]' : '') + '">';
                    // Badge container for multiple badges
                    diskHtml += '<div class="treemap-partition-badges" style="position:absolute;top:2px;right:4px;display:flex;gap:2px;flex-wrap:wrap;justify-content:flex-end;max-width:80%;">';
                    if (isRootVolume) {
                        diskHtml += '<span style="background:var(--archie-orange);color:#000;padding:1px 4px;border-radius:3px;font-size:0.5rem;font-weight:600;">ROOT</span>';
                    } else if (childMountLabel && child.mountpoint !== '/boot' && child.mountpoint !== '/boot/efi') {
                        // Show mountpoint badge for non-system mounts (blue)
                        diskHtml += '<span style="background:var(--archie-cyan);color:#000;padding:1px 4px;border-radius:3px;font-size:0.5rem;font-weight:600;">' + childMountLabel + '</span>';
                    }
                    if (childDockerStacks.length > 0) {
                        // Show individual Docker badge for each stack with its own color
                        childDockerStacks.forEach(stack => {
                            const bgColor = stack.color || 'var(--archie-purple)';
                            const textColor = getContrastColor(bgColor);
                            diskHtml += '<span style="background:' + bgColor + ';color:' + textColor + ';padding:1px 4px;border-radius:3px;font-size:0.5rem;font-weight:600;">🐳 ' + (stack.display_name || stack.stack_name) + '</span>';
                        });
                    }
                    diskHtml += '</div>';
                    diskHtml += '<span class="treemap-partition-label">' + label + '</span>';
                    diskHtml += '<span class="treemap-partition-size">' + sizeLabel + '</span>';
                    if (isMounted && child.usage) {
                        diskHtml += '<div class="treemap-partition-usage">';
                        diskHtml += '<div class="treemap-partition-usage-bar" style="width: ' + usagePct + '%; background: ' + getUsageColor(usagePct) + ';"></div>';
                        diskHtml += '</div>';
                    }
                    diskHtml += '</div>';
                });

                // Show VG free space if available
                // Try to find VG name from LVM PV data or from child vg_name
                let vgFreeName = null;
                let vgFreeGb = 0;
                if (lvm && lvm.pvs) {
                    const pv = lvm.pvs.find(p => p.pv_name === part.device);
                    if (pv && vgFree[pv.vg_name]) {
                        vgFreeName = pv.vg_name;
                        vgFreeGb = vgFree[pv.vg_name].free_gb || 0;
                    }
                }
                // Fallback: derive VG name from child's vg_name field
                if (!vgFreeName && part.children.length > 0) {
                    const childVg = part.children[0].vg_name;
                    if (childVg && vgFree[childVg]) {
                        vgFreeName = childVg;
                        vgFreeGb = vgFree[childVg].free_gb || 0;
                    }
                }
                if (vgFreeName && vgFreeGb > 0) {
                    const freePct = totalBytes > 0 ? (vgFreeGb / disk.size_gb * 100) : 5;
                    diskHtml += '<div class="treemap-partition lvm-free"';
                    diskHtml += ' style="flex: ' + Math.max(freePct, 2) + ';"';
                    diskHtml += ' onclick="selectPartition(this, \'' + disk.name + '\', \'vg-free-' + vgFreeName + '\', \'lvm-free\')"';
                    diskHtml += ' title="VG Free Space: ' + formatSizeGB(vgFreeGb) + '">';
                    diskHtml += '<span class="treemap-partition-label">Free</span>';
                    diskHtml += '<span class="treemap-partition-size">' + formatSizeGB(vgFreeGb) + '</span>';
                    diskHtml += '</div>';
                }
            } else {
                // Regular partition (no LVM children)
                const partPct = totalBytes > 0 ? (part.size_gb / disk.size_gb * 100) : 10;
                const isMounted = !!part.mountpoint;
                const usagePct = part.usage ? part.usage.percent : 0;
                const stateClass = isMounted ? 'mounted' : 'unmounted';
                const label = part.name || part.device;
                const sizeLabel = formatSizeGB(part.size_gb);
                // Check if this is a system partition (root, boot, EFI, system dirs, BIOS boot, system LVM)
                const systemMountsVisual = ['/', '/boot', '/boot/efi', '/var', '/usr', '/etc', '/home', '/tmp'];
                const isBiosBoot = !part.mountpoint && part.size_gb < 0.01; // BIOS boot is typically 1MB
                const isSystemLvm = part.fstype === 'LVM2_member' && (
                    (part.vg_name && (part.vg_name.includes('ubuntu') || part.vg_name.includes('system'))) ||
                    (disk.name === 'sdd') // Boot drive's LVM partition
                );
                const isSystemPartition = systemMountsVisual.includes(part.mountpoint) || isBiosBoot || isSystemLvm;
                // Check for Docker stacks using this mount
                const partDockerStacks = getStacksForPath(part.mountpoint);
                const partMountLabel = getMountLabel(part.mountpoint);

                diskHtml += '<div class="treemap-partition ' + stateClass + '"';
                diskHtml += ' style="flex: ' + Math.max(partPct, 2) + ';"';
                diskHtml += ' onclick="selectPartition(this, \'' + disk.name + '\', \'' + (part.device || '').replace(/'/g, "\\'") + '\', \'partition\')"';
                diskHtml += ' title="' + label + ' (' + sizeLabel + ')' + (part.mountpoint ? ' → ' + part.mountpoint : '') + (isSystemPartition ? ' [SYSTEM]' : '') + '">';
                // Badge container for multiple badges
                diskHtml += '<div class="treemap-partition-badges" style="position:absolute;top:2px;right:4px;display:flex;gap:2px;flex-wrap:wrap;justify-content:flex-end;max-width:80%;">';
                if (isSystemPartition) {
                    diskHtml += '<span style="background:var(--archie-orange);color:#000;padding:1px 4px;border-radius:3px;font-size:0.5rem;font-weight:600;">SYS</span>';
                } else if (partMountLabel) {
                    // Show mountpoint badge for non-system mounts (blue/cyan)
                    diskHtml += '<span style="background:var(--archie-cyan);color:#000;padding:1px 4px;border-radius:3px;font-size:0.5rem;font-weight:600;">' + partMountLabel + '</span>';
                }
                if (partDockerStacks.length > 0) {
                    // Show individual Docker badge for each stack with its own color
                    partDockerStacks.forEach(stack => {
                        const bgColor = stack.color || 'var(--archie-purple)';
                        const textColor = getContrastColor(bgColor);
                        diskHtml += '<span style="background:' + bgColor + ';color:' + textColor + ';padding:1px 4px;border-radius:3px;font-size:0.5rem;font-weight:600;">🐳 ' + (stack.display_name || stack.stack_name) + '</span>';
                    });
                }
                diskHtml += '</div>';
                diskHtml += '<span class="treemap-partition-label">' + label + '</span>';
                diskHtml += '<span class="treemap-partition-size">' + sizeLabel + '</span>';
                if (isMounted && part.usage) {
                    diskHtml += '<div class="treemap-partition-usage">';
                    diskHtml += '<div class="treemap-partition-usage-bar" style="width: ' + usagePct + '%; background: ' + getUsageColor(usagePct) + ';"></div>';
                    diskHtml += '</div>';
                }
                diskHtml += '</div>';
            }
        });

        // Unallocated space
        if (disk.unallocated_gb > 0.5) {
            const unallocPct = disk.unallocated_gb / disk.size_gb * 100;
            diskHtml += '<div class="treemap-partition unallocated"';
            diskHtml += ' style="flex: ' + Math.max(unallocPct, 2) + ';"';
            diskHtml += ' onclick="selectPartition(this, \'' + disk.name + '\', \'unallocated\', \'unallocated\')"';
            diskHtml += ' title="Unallocated: ' + formatSizeGB(disk.unallocated_gb) + '">';
            diskHtml += '<span class="treemap-partition-label">Free</span>';
            diskHtml += '<span class="treemap-partition-size">' + formatSizeGB(disk.unallocated_gb) + '</span>';
            diskHtml += '</div>';
        }

        diskHtml += '</div>'; // close treemap-partitions
        diskHtml += '</div>'; // close treemap-drive
        return diskHtml;
    }

    let html = '';

    // Render Local Drives section
    if (localDisks.length > 0) {
        // Format as "Last refresh: 10:22 PM" - uses browser's local timezone
        const refreshTime = lastStorageRefresh
            ? 'Last refresh: ' + lastStorageRefresh.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})
            : 'Last refresh: --';
        html += '<div class="local-drives-header" style="margin-bottom: 8px; padding: 6px 12px; background: var(--archie-bg-tertiary); border-radius: 6px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">';
        html += '<div style="display: flex; align-items: center; gap: 8px;">';
        html += '<span style="font-size: 0.75rem; font-weight: 600; color: var(--archie-cyan);">LOCAL DRIVES</span>';
        html += '<span style="font-size: 0.7rem; color: var(--archie-text-muted);">(' + localDisks.length + ' drive' + (localDisks.length > 1 ? 's' : '') + ')</span>';
        html += '</div>';
        html += '<div style="display: flex; align-items: center; gap: 6px; font-size: 0.7rem; color: var(--archie-text-muted);">';
        html += '<i data-lucide="refresh-cw" style="width: 12px; height: 12px;"></i>';
        html += '<span id="storageRefreshTime">' + refreshTime + '</span>';
        html += '</div>';
        html += '</div>';
        localDisks.forEach(disk => { html += renderDisk(disk); });
    }

    // Render Attached/USB Drives section
    if (attachedDisks.length > 0) {
        html += '<div style="margin: 16px 0 8px 0; padding: 6px 12px; background: var(--archie-bg-tertiary); border-radius: 6px; display: flex; align-items: center; gap: 8px; border-top: 2px dashed var(--archie-orange);">';
        html += '<span style="font-size: 0.75rem; font-weight: 600; color: var(--archie-orange);">USB / ATTACHED DRIVES</span>';
        html += '<span style="font-size: 0.7rem; color: var(--archie-text-muted);">(' + attachedDisks.length + ' drive' + (attachedDisks.length > 1 ? 's' : '') + ') - SMART health not available for removable media</span>';
        html += '</div>';
        attachedDisks.forEach(disk => { html += renderDisk(disk); });
    }

    container.innerHTML = html;
}

function renderStorageCharts(disks) {
    if (typeof Chart === 'undefined') return;

    // Disk Usage Breakdown (like Dashboard display)
    const diskBreakdownContainer = document.getElementById('storage-disk-breakdown');
    if (diskBreakdownContainer) {
        // Clear container using DOM methods
        while (diskBreakdownContainer.firstChild) {
            diskBreakdownContainer.removeChild(diskBreakdownContainer.firstChild);
        }

        function formatDiskSizeStorage(gb) {
            if (gb >= 1000) return (gb / 1000).toFixed(1) + ' TB';
            return gb.toFixed(1) + ' GB';
        }

        function getColorClassStorage(pct, warn, crit) {
            if (pct >= crit) return 'red';
            if (pct >= warn) return 'orange';
            return 'green';
        }

        disks.forEach((disk, idx) => {
            const driveDiv = document.createElement('div');
            if (idx > 0) {
                driveDiv.style.cssText = 'margin-top: 14px; border-top: 1px solid var(--archie-border); padding-top: 10px;';
            }

            // Drive header: model + total size
            const header = document.createElement('div');
            header.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;';
            const label = document.createElement('span');
            label.style.cssText = 'font-size: 0.85rem; font-weight: 600; color: var(--archie-text-primary);';
            label.textContent = (disk.model || disk.name || 'Drive ' + (idx + 1)).substring(0, 35);
            const sizeLabel = document.createElement('span');
            sizeLabel.style.cssText = 'font-size: 0.75rem; color: var(--archie-text-muted);';
            sizeLabel.textContent = formatDiskSizeStorage(disk.size_gb || 0);
            header.appendChild(label);
            header.appendChild(sizeLabel);
            driveDiv.appendChild(header);

            // Calculate usage stats
            const totalGb = disk.size_gb || 0;
            const unallocatedGb = disk.unallocated_gb || 0;
            const allocatedGb = totalGb - unallocatedGb;
            let usedGb = 0, freeGb = 0;

            // Check if disk is mounted directly (whole-disk mount, no partitions)
            if (disk.usage && disk.mountpoint && (!disk.partitions || disk.partitions.length === 0)) {
                usedGb = disk.usage.used_gb || 0;
                freeGb = disk.usage.free_gb || 0;
            } else {
                // Iterate through partitions
                (disk.partitions || []).forEach(part => {
                    const items = (part.children && part.children.length) ? part.children : [part];
                    items.forEach(item => {
                        if (item.usage) {
                            usedGb += item.usage.used_gb || 0;
                            freeGb += item.usage.free_gb || 0;
                        }
                    });
                });
            }
            const usePct = totalGb > 0 ? (usedGb / totalGb * 100) : 0;

            // Progress bar
            const barOuter = document.createElement('div');
            barOuter.className = 'progress-bar';
            const barFill = document.createElement('div');
            barFill.className = 'progress-fill ' + getColorClassStorage(usePct, 80, 90);
            barFill.style.width = usePct + '%';
            barOuter.appendChild(barFill);
            driveDiv.appendChild(barOuter);

            // Summary line: Alloc / Unalloc / Free / Used
            const summary = document.createElement('div');
            summary.style.cssText = 'font-size: 0.72rem; color: var(--archie-text-muted); margin-top: 3px; display: flex; gap: 10px; flex-wrap: wrap;';

            function addStat(parent, lbl, val, color) {
                const s = document.createElement('span');
                const l = document.createElement('span');
                l.textContent = lbl + ': ';
                const v = document.createElement('span');
                v.style.cssText = 'color:' + color + '; font-weight: 600;';
                v.textContent = val;
                s.appendChild(l);
                s.appendChild(v);
                parent.appendChild(s);
            }
            addStat(summary, 'Alloc', formatDiskSizeStorage(allocatedGb), 'var(--archie-cyan)');
            addStat(summary, 'Unalloc', formatDiskSizeStorage(unallocatedGb), 'var(--archie-text-muted)');
            addStat(summary, 'Free', formatDiskSizeStorage(freeGb), 'var(--archie-green)');
            addStat(summary, 'Used', formatDiskSizeStorage(usedGb), 'var(--archie-orange)');
            driveDiv.appendChild(summary);

            // Partition breakdown (matching Dashboard style)
            const parts = disk.partitions || [];

            // Helper: get all Docker stacks for a disk (disk + partitions + LVM children)
            function getAllStacksForDisk(d) {
                const stackSet = new Map();
                // Disk-level mount
                if (d.mountpoint) {
                    getStacksForPath(d.mountpoint).forEach(s => stackSet.set(s.stack_name, s));
                }
                // Partition mounts
                (d.partitions || []).forEach(p => {
                    if (p.mountpoint) {
                        getStacksForPath(p.mountpoint).forEach(s => stackSet.set(s.stack_name, s));
                    }
                    // LVM children
                    (p.children || []).forEach(c => {
                        if (c.mountpoint) {
                            getStacksForPath(c.mountpoint).forEach(s => stackSet.set(s.stack_name, s));
                        }
                    });
                });
                return Array.from(stackSet.values());
            }

            // Show aggregated Docker badges at disk level
            const diskStacks = getAllStacksForDisk(disk);
            if (diskStacks.length > 0) {
                const stacksRow = document.createElement('div');
                stacksRow.style.cssText = 'display: flex; gap: 4px; flex-wrap: wrap; margin-top: 6px;';
                diskStacks.forEach(stack => {
                    const badge = document.createElement('span');
                    const color = stack.color || '#6366f1';
                    badge.style.cssText = 'padding: 2px 6px; font-size: 0.68rem; border-radius: 3px; background: ' + color + '22; color: ' + color + '; border: 1px solid ' + color + '33;';
                    badge.textContent = stack.display_name || stack.stack_name;
                    badge.title = 'Docker stack using this drive';
                    stacksRow.appendChild(badge);
                });
                driveDiv.appendChild(stacksRow);
            }

            // Handle whole-disk mount (disk formatted without partitions)
            if (parts.length === 0 && disk.mountpoint && disk.usage) {
                const partList = document.createElement('div');
                partList.style.cssText = 'margin-top: 6px; padding-left: 8px; border-left: 2px solid var(--archie-border); font-size: 0.72rem;';

                const row = document.createElement('div');
                row.style.cssText = 'display: flex; justify-content: space-between; padding: 2px 0; color: var(--archie-text-secondary);';

                const nameSpan = document.createElement('span');
                nameSpan.style.fontWeight = '500';
                nameSpan.textContent = disk.label || disk.name || '(whole disk)';

                const infoSpan = document.createElement('span');
                infoSpan.style.color = 'var(--archie-text-muted)';
                let infoText = formatDiskSizeStorage(disk.size_gb || 0) + ' ' + (disk.fstype || '');
                infoText += ' @ ' + disk.mountpoint;
                infoText += ' (' + (disk.usage.percent || 0).toFixed(0) + '% used)';
                infoSpan.textContent = infoText;

                row.appendChild(nameSpan);
                row.appendChild(infoSpan);
                partList.appendChild(row);

                // Show Docker stacks using this disk's mount
                const diskMountStacks = getStacksForPath(disk.mountpoint);
                if (diskMountStacks.length > 0) {
                    const stackRow = document.createElement('div');
                    stackRow.style.cssText = 'display: flex; gap: 4px; flex-wrap: wrap; padding: 2px 0 2px 12px;';
                    diskMountStacks.forEach(stack => {
                        const badge = document.createElement('span');
                        const color = stack.color || '#6366f1';
                        badge.style.cssText = 'padding: 1px 5px; font-size: 0.65rem; border-radius: 3px; background: ' + color + '33; color: ' + color + '; border: 1px solid ' + color + '44;';
                        badge.textContent = stack.display_name || stack.stack_name;
                        badge.title = 'Docker: ' + (stack.service || '') + ' → ' + (stack.container_path || '');
                        stackRow.appendChild(badge);
                    });
                    partList.appendChild(stackRow);
                }

                driveDiv.appendChild(partList);
            } else if (parts.length > 0) {
                const partList = document.createElement('div');
                partList.style.cssText = 'margin-top: 6px; padding-left: 8px; border-left: 2px solid var(--archie-border); font-size: 0.72rem;';
                parts.forEach(part => {
                    const row = document.createElement('div');
                    row.style.cssText = 'display: flex; justify-content: space-between; padding: 2px 0; color: var(--archie-text-secondary);';

                    const nameSpan = document.createElement('span');
                    nameSpan.style.fontWeight = '500';
                    nameSpan.textContent = part.name || part.device || '?';

                    const infoSpan = document.createElement('span');
                    infoSpan.style.color = 'var(--archie-text-muted)';
                    let infoText = formatDiskSizeStorage(part.size_gb || 0) + ' ' + (part.fstype || '');
                    if (part.mountpoint) infoText += ' @ ' + part.mountpoint;
                    if (part.usage) {
                        infoText += ' (' + (part.usage.percent || 0).toFixed(0) + '% used)';
                    }
                    infoSpan.textContent = infoText;

                    row.appendChild(nameSpan);
                    row.appendChild(infoSpan);
                    partList.appendChild(row);

                    // Show Docker stacks using this partition's mount
                    if (part.mountpoint) {
                        const partStacks = getStacksForPath(part.mountpoint);
                        if (partStacks.length > 0) {
                            const stackRow = document.createElement('div');
                            stackRow.style.cssText = 'display: flex; gap: 4px; flex-wrap: wrap; padding: 2px 0 2px 12px;';
                            partStacks.forEach(stack => {
                                const badge = document.createElement('span');
                                const color = stack.color || '#6366f1';
                                badge.style.cssText = 'padding: 1px 5px; font-size: 0.65rem; border-radius: 3px; background: ' + color + '33; color: ' + color + '; border: 1px solid ' + color + '44;';
                                badge.textContent = stack.display_name || stack.stack_name;
                                badge.title = 'Docker: ' + (stack.service || '') + ' → ' + (stack.container_path || '');
                                stackRow.appendChild(badge);
                            });
                            partList.appendChild(stackRow);
                        }
                    }

                    // Show LVM children under partition
                    let childrenTotalGb = 0;
                    (part.children || []).forEach(child => {
                        const childRow = document.createElement('div');
                        childRow.style.cssText = 'display: flex; justify-content: space-between; padding: 2px 0 2px 12px; color: var(--archie-text-secondary);';

                        const childName = document.createElement('span');
                        childName.style.cssText = 'font-weight: 500; color: var(--archie-cyan);';
                        childName.textContent = '\u2514 ' + (child.name || child.device || '?');

                        const childInfo = document.createElement('span');
                        childInfo.style.color = 'var(--archie-text-muted)';
                        const childSizeGb = child.size_gb || 0;
                        childrenTotalGb += childSizeGb;
                        let childInfoText = formatDiskSizeStorage(childSizeGb) + ' ' + (child.fstype || '');
                        if (child.mountpoint) childInfoText += ' @ ' + child.mountpoint;
                        if (child.usage) childInfoText += ' (' + (child.usage.percent || 0).toFixed(0) + '% used)';
                        childInfo.textContent = childInfoText;

                        childRow.appendChild(childName);
                        childRow.appendChild(childInfo);
                        partList.appendChild(childRow);

                        // Show Docker stacks using this LVM child's mount
                        if (child.mountpoint) {
                            const childStacks = getStacksForPath(child.mountpoint);
                            if (childStacks.length > 0) {
                                const childStackRow = document.createElement('div');
                                childStackRow.style.cssText = 'display: flex; gap: 4px; flex-wrap: wrap; padding: 2px 0 2px 24px;';
                                childStacks.forEach(stack => {
                                    const badge = document.createElement('span');
                                    const color = stack.color || '#6366f1';
                                    badge.style.cssText = 'padding: 1px 5px; font-size: 0.65rem; border-radius: 3px; background: ' + color + '33; color: ' + color + '; border: 1px solid ' + color + '44;';
                                    badge.textContent = stack.display_name || stack.stack_name;
                                    badge.title = 'Docker: ' + (stack.service || '') + ' → ' + (stack.container_path || '');
                                    childStackRow.appendChild(badge);
                                });
                                partList.appendChild(childStackRow);
                            }
                        }
                    });

                    // Show VG free space for LVM parent partitions
                    if (part.fstype === 'LVM2_member' && part.children && part.children.length > 0) {
                        const vgName = part.children[0].vg_name;
                        if (vgName && storageData?.vg_free?.[vgName]) {
                            const vgFreeGb = storageData.vg_free[vgName].free_gb || 0;
                            if (vgFreeGb > 0.1) {
                                const freeRow = document.createElement('div');
                                freeRow.style.cssText = 'display: flex; justify-content: space-between; padding: 2px 0 2px 12px; color: var(--archie-text-muted); font-style: italic;';
                                const freeName = document.createElement('span');
                                freeName.textContent = '\u2514 VG Free';
                                const freeInfo = document.createElement('span');
                                freeInfo.style.color = 'var(--archie-green)';
                                freeInfo.textContent = formatDiskSizeStorage(vgFreeGb) + ' available';
                                freeRow.appendChild(freeName);
                                freeRow.appendChild(freeInfo);
                                partList.appendChild(freeRow);
                            }
                        }
                    }
                });
                driveDiv.appendChild(partList);
            }

            diskBreakdownContainer.appendChild(driveDiv);
        });

        // If no disks, show message
        if (disks.length === 0) {
            const noDataMsg = document.createElement('div');
            noDataMsg.style.cssText = 'color: var(--archie-text-muted); font-size: 0.85rem;';
            noDataMsg.textContent = 'No disk data available';
            diskBreakdownContainer.appendChild(noDataMsg);
        }
    }
}

// ===== Storage Partition Selection =====

function selectPartition(element, diskName, devicePath, partType) {
    // Deselect previous
    if (selectedPartition) {
        selectedPartition.classList.remove('selected');
    }
    selectedPartition = element;
    element.classList.add('selected');

    // Find disk and partition data
    const disk = (storageData?.disks || []).find(d => d.name === diskName);
    if (!disk) return;

    // Open modal based on partition type
    if (partType === 'lvm-free') {
        openPartitionDetailsModal(disk, devicePath, 'lvm-free');
    } else if (partType === 'unallocated') {
        openPartitionDetailsModal(disk, null, 'unallocated');
    } else if (partType === 'disk') {
        // Disk formatted without partitions - treat disk itself as the partition
        const diskAsPart = {
            name: disk.label || disk.name,
            device: disk.device,
            size_gb: disk.size_gb,
            size_bytes: disk.size_bytes,
            fstype: disk.fstype,
            mountpoint: disk.mountpoint,
            uuid: disk.uuid,
            usage: disk.usage,
            is_whole_disk: true  // Flag to indicate this is a whole disk mount
        };
        openPartitionDetailsModal(disk, diskAsPart, 'partition');
    } else {
        // Find the partition or child
        let part = null;
        for (const p of (disk.partitions || [])) {
            if (p.device === devicePath) { part = p; break; }
            for (const c of (p.children || [])) {
                if (c.device === devicePath) { part = c; break; }
            }
            if (part) break;
        }
        if (part) {
            openPartitionDetailsModal(disk, part, 'partition');
        }
    }
}

function closeActionPanel() {
    // Side panel removed - using modals instead
    // Keep function for compatibility
    if (selectedPartition) {
        selectedPartition.classList.remove('selected');
        selectedPartition = null;
    }
}

// Close all modals helper - call before opening a new modal
function closeAllModals() {
    document.querySelectorAll('.modal-overlay.active').forEach(modal => {
        modal.classList.remove('active');
    });
}

// Partition Details Modal state
let currentPartitionDetailsData = null;

function openPartitionDetailsModal(disk, part, contentType) {
    closeAllModals();
    currentPartitionDetailsData = { disk: disk, part: part, contentType: contentType };

    // Close side panel
    closeActionPanel();

    // Set title based on content type
    let title = 'Partition Details';
    if (contentType === 'partition') {
        title = part.name || part.device || 'Partition';
    } else if (contentType === 'lvm-free') {
        title = 'VG Free Space';
    } else if (contentType === 'unallocated') {
        title = 'Unallocated Space';
    }
    document.getElementById('partitionDetailsName').textContent = title;

    // Render content based on type
    const body = document.getElementById('partitionDetailsBody');
    if (contentType === 'partition') {
        body.innerHTML = buildPartitionModalContent(disk, part);
    } else if (contentType === 'lvm-free') {
        body.innerHTML = buildLvmFreeModalContent(disk, part);
    } else if (contentType === 'unallocated') {
        body.innerHTML = buildUnallocatedModalContent(disk);
    }

    document.getElementById('partitionDetailsModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function closePartitionDetailsModal() {
    document.getElementById('partitionDetailsModal').classList.remove('active');
    currentPartitionDetailsData = null;
    if (selectedPartition) {
        selectedPartition.classList.remove('selected');
        selectedPartition = null;
    }
}

function buildPartitionModalContent(disk, part) {
    console.log('buildPartitionModalContent - part:', part);
    console.log('  part.type:', part.type, '  isLvm:', part.type === 'lvm');
    console.log('  part.fstype:', part.fstype, '  part.mountpoint:', part.mountpoint);
    const isMounted = !!part.mountpoint;
    const isLvm = part.type === 'lvm';
    const usagePct = part.usage ? part.usage.percent : 0;

    let html = '<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px;">';
    html += '<div><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Device</span><span class="mono" style="color: var(--archie-cyan);">' + (part.device || '--') + '</span></div>';
    html += '<div><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Filesystem</span><span>' + (part.fstype || '--') + '</span></div>';
    html += '<div><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Size</span><span>' + formatSizeGB(part.size_gb) + '</span></div>';
    html += '<div><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Mount</span><span style="color: ' + (isMounted ? 'var(--archie-green)' : 'var(--archie-text-muted)') + ';">' + (part.mountpoint || 'Not mounted') + '</span></div>';
    if (part.label) {
        html += '<div style="grid-column: span 2;"><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Label</span><span>' + part.label + '</span></div>';
    }
    // Stable ID field - used for reliable device identification across reboots
    if (part.stable_id) {
        html += '<div style="grid-column: span 2;"><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Stable ID</span><span class="mono" style="color: var(--archie-purple); font-size: 0.75rem; word-break: break-all;">' + part.stable_id + '</span></div>';
    }
    // Serial from parent disk
    if (disk.serial) {
        html += '<div style="grid-column: span 2;"><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Serial</span><span class="mono" style="font-size: 0.75rem;">' + disk.serial + '</span></div>';
    }
    html += '</div>';

    // Usage bar
    if (isMounted && part.usage) {
        html += '<div style="margin-bottom: 16px; padding: 12px; background: var(--archie-bg-tertiary); border-radius: 6px;">';
        html += '<div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--archie-text-secondary); margin-bottom: 6px;">';
        html += '<span>' + formatSizeGB(part.usage.used_gb) + ' used</span>';
        html += '<span>' + formatSizeGB(part.usage.free_gb) + ' free</span>';
        html += '</div>';
        html += '<div style="height: 8px; background: var(--archie-bg-primary); border-radius: 4px; overflow: hidden;">';
        html += '<div style="width: ' + usagePct + '%; height: 100%; background: ' + getUsageColor(usagePct) + ';"></div>';
        html += '</div>';
        html += '<div style="text-align: center; font-size: 0.9rem; font-weight: 600; color: ' + getUsageColor(usagePct) + '; margin-top: 6px;">' + usagePct.toFixed(1) + '% used</div>';
        html += '</div>';
    }

    // Type badges
    html += '<div style="margin-bottom: 16px; display: flex; gap: 6px; flex-wrap: wrap;">';
    if (isLvm) html += '<span class="lvm-badge">LVM</span>';
    if (isMounted) html += '<span style="padding: 2px 6px; font-size: 0.7rem; background: var(--archie-green-alpha-20); color: var(--archie-green); border-radius: 3px;">MOUNTED</span>';
    if (disk.smart_health) html += '<span class="treemap-drive-health ' + disk.smart_health + '" style="font-size: 0.7rem;">' + disk.smart_health.toUpperCase() + '</span>';
    html += '</div>';

    // Docker stacks using this partition
    if (isMounted) {
        const stacks = getStacksForPath(part.mountpoint);
        if (stacks.length > 0) {
            html += '<div style="margin-bottom: 16px; padding: 12px; background: var(--archie-bg-tertiary); border-radius: 6px;">';
            html += '<div style="color: var(--archie-text-muted); font-size: 0.75rem; margin-bottom: 8px;">DOCKER STACKS ON THIS VOLUME</div>';
            html += '<div style="display: flex; gap: 6px; flex-wrap: wrap;">';
            stacks.forEach(stack => {
                const bgColor = stack.color || 'var(--archie-purple)';
                const textColor = getContrastColor(bgColor);
                html += '<span style="background:' + bgColor + ';color:' + textColor + ';padding: 4px 10px;border-radius: 4px;font-size: 0.8rem;font-weight: 600;display: inline-flex;align-items: center;gap: 4px;">🐳 ' + (stack.display_name || stack.stack_name) + '</span>';
            });
            html += '</div>';
            html += '</div>';
        }
    }

    // Check if this is a protected system partition
    // Protected: root, boot, efi, system dirs, BIOS boot (small no-mount), LVM containing system VG
    const systemMounts = ['/', '/boot', '/boot/efi', '/var', '/usr', '/etc', '/home', '/tmp'];
    const isBiosBootPartition = !part.mountpoint && part.size_gb < 0.01; // BIOS boot is typically 1MB
    const isSystemLvmMember = part.fstype === 'LVM2_member' && (
        (part.vg_name && (part.vg_name.includes('ubuntu') || part.vg_name.includes('system'))) ||
        (disk.name === 'sdd') // Boot drive's LVM partition
    );
    const isSystemPartition = systemMounts.includes(part.mountpoint) || isBiosBootPartition || isSystemLvmMember;

    // Action buttons
    html += '<div style="display: flex; flex-direction: column; gap: 8px;">';
    if (isMounted) {
        // Browse Files - show different options for system vs data partitions
        if (isSystemPartition) {
            html += '<button class="btn btn-ghost" style="justify-content: flex-start;" onclick="showToast(\'info\', \'System partition mounted at ' + part.mountpoint.replace(/'/g, "\\'") + '\')"><i data-lucide="shield" style="width:16px;height:16px;margin-right:8px;"></i> System Partition</button>';
        } else {
            html += '<button class="btn btn-ghost" style="justify-content: flex-start;" onclick="browsePartition(\'' + part.mountpoint.replace(/'/g, "\\'") + '\')"><i data-lucide="folder-open" style="width:16px;height:16px;margin-right:8px;"></i> Browse Files</button>';
        }
        if (isLvm) {
            const extVgName = part.vg_name || '';
            const extLvName = part.lv_name || part.name || '';
            let extFreeGb = 0;
            if (extVgName && storageData?.vg_free?.[extVgName]) {
                extFreeGb = storageData.vg_free[extVgName].free_gb || 0;
            } else if (extVgName && window.hostLvmData?.vgs) {
                const vgEntry = window.hostLvmData.vgs.find(v => v.vg_name === extVgName);
                if (vgEntry) extFreeGb = vgEntry.vg_free_gb || 0;
            }
            // Extend Volume - safe for system (can grow online), but show warning modal
            if (isSystemPartition) {
                html += '<button class="btn btn-primary" style="justify-content: flex-start;" onclick="confirmSystemExtend(\'' + extLvName.replace(/'/g, "\\'") + '\', \'' + extVgName.replace(/'/g, "\\'") + '\', ' + (part.size_gb || 0) + ', ' + extFreeGb + ', \'' + (part.mountpoint || '').replace(/'/g, "\\'") + '\')"><i data-lucide="shield-alert" style="width:16px;height:16px;margin-right:8px;color:var(--archie-orange);"></i> Extend Volume (System)</button>';
            } else {
                html += '<button class="btn btn-primary" style="justify-content: flex-start;" onclick="openLvmExtendModal(\'' + extLvName.replace(/'/g, "\\'") + '\', \'' + extVgName.replace(/'/g, "\\'") + '\', ' + (part.size_gb || 0) + ', ' + extFreeGb + ', \'' + (part.mountpoint || '').replace(/'/g, "\\'") + '\')"><i data-lucide="expand" style="width:16px;height:16px;margin-right:8px;"></i> Extend Volume</button>';
            }
            // Shrink button - BLOCKED for system partitions (requires unmount)
            const usedGb = part.usage ? (part.usage.used_gb || 0) : 0;
            const canShrinkFs = (part.fstype === 'ext4' || part.fstype === 'ext3');
            if (isSystemPartition) {
                html += '<button class="btn btn-warning" style="justify-content: flex-start; opacity:0.5; cursor:not-allowed;" onclick="showToast(\'error\', \'Cannot shrink system volume - requires unmount which would crash the server\')"><i data-lucide="shield-alert" style="width:16px;height:16px;margin-right:8px;"></i> Shrink Volume (Protected)</button>';
            } else if (canShrinkFs) {
                html += '<button class="btn btn-warning" style="justify-content: flex-start;" onclick="openLvmShrinkModal(\'' + (part.device || '').replace(/'/g, "\\'") + '\', \'' + extLvName.replace(/'/g, "\\'") + '\', \'' + extVgName.replace(/'/g, "\\'") + '\', ' + (part.size_gb || 0) + ', ' + usedGb + ', \'' + (part.fstype || '').replace(/'/g, "\\'") + '\', \'' + (part.mountpoint || '').replace(/'/g, "\\'") + '\')"><i data-lucide="minimize-2" style="width:16px;height:16px;margin-right:8px;"></i> Shrink Volume</button>';
            } else {
                html += '<button class="btn btn-warning" style="justify-content: flex-start; opacity:0.5; cursor:not-allowed;" onclick="showToast(\'error\', \'Only ext4 filesystems can be shrunk\')"><i data-lucide="minimize-2" style="width:16px;height:16px;margin-right:8px;"></i> Shrink Volume</button>';
            }
            // Resize Filesystem button - safe for system (can grow online), but show warning
            const fsSupportedForResize = ['ext4', 'ext3', 'ext2', 'xfs', 'btrfs'].includes(part.fstype);
            const fsSizeMismatch = part.usage && part.size_gb && (part.size_gb - part.usage.total_gb) > 1;
            if (fsSupportedForResize) {
                const resizeDevice = (part.device || '').replace(/'/g, "\\'");
                const resizeMountpoint = (part.mountpoint || '').replace(/'/g, "\\'");
                if (isSystemPartition) {
                    const btnStyle = fsSizeMismatch ? 'btn btn-warning' : 'btn btn-ghost';
                    const hint = fsSizeMismatch ? ' (Size mismatch!)' : '';
                    html += '<button class="' + btnStyle + '" style="justify-content: flex-start;" onclick="confirmSystemResize(\'' + resizeDevice + '\', \'' + (part.fstype || '').replace(/'/g, "\\'") + '\', \'' + resizeMountpoint + '\')"><i data-lucide="shield-alert" style="width:16px;height:16px;margin-right:8px;color:var(--archie-orange);"></i> Resize FS (System)' + hint + '</button>';
                } else {
                    const btnStyle = fsSizeMismatch ? 'btn btn-warning' : 'btn btn-ghost';
                    const hint = fsSizeMismatch ? ' (Size mismatch detected!)' : '';
                    html += '<button class="' + btnStyle + '" style="justify-content: flex-start;" onclick="resizeFilesystem(\'' + resizeDevice + '\', \'' + (part.fstype || '').replace(/'/g, "\\'") + '\', \'' + resizeMountpoint + '\')"><i data-lucide="maximize-2" style="width:16px;height:16px;margin-right:8px;"></i> Resize Filesystem' + hint + '</button>';
                }
            }
            // Rename button (not for system VG/LV)
            if (isSystemPartition) {
                html += '<button class="btn btn-ghost" style="justify-content: flex-start; opacity:0.5; cursor:not-allowed;" onclick="showToast(\'error\', \'Cannot rename system volume\')"><i data-lucide="pencil" style="width:16px;height:16px;margin-right:8px;"></i> Rename</button>';
            } else {
                html += '<button class="btn btn-ghost" style="justify-content: flex-start;" onclick="openLvmRenameModal(\'' + extVgName.replace(/'/g, "\\'") + '\', \'' + extLvName.replace(/'/g, "\\'") + '\')"><i data-lucide="pencil" style="width:16px;height:16px;margin-right:8px;"></i> Rename</button>';
            }
        } else {
            // Non-LVM partition features
            // Change Label button (for ext2/3/4)
            if (part.fstype === 'ext4' || part.fstype === 'ext3' || part.fstype === 'ext2') {
                html += '<button class="btn btn-ghost" style="justify-content: flex-start;" onclick="openChangeLabelModal(\'' + (part.device || '').replace(/'/g, "\\'") + '\', \'' + (part.label || '').replace(/'/g, "\\'") + '\', \'' + (part.fstype || '').replace(/'/g, "\\'") + '\')"><i data-lucide="tag" style="width:16px;height:16px;margin-right:8px;"></i> Change Label</button>';
            }
            // Convert to LVM button - protected for system partitions
            if (isSystemPartition) {
                html += '<button class="btn btn-primary" style="justify-content: flex-start; opacity:0.5; cursor:not-allowed;" onclick="showToast(\'error\', \'Cannot convert system partition to LVM - this would destroy the boot drive\')"><i data-lucide="shield-alert" style="width:16px;height:16px;margin-right:8px;"></i> Convert to LVM (Protected)</button>';
            } else {
                const convertId = (part.stable_id || part.device || '').replace(/'/g, "\\'");
                html += '<button class="btn btn-primary" style="justify-content: flex-start;" onclick="openConvertToLvmModal(\'' + convertId + '\', ' + (part.size_gb || 0) + ', \'' + (part.fstype || '').replace(/'/g, "\\'") + '\', \'' + (part.mountpoint || '').replace(/'/g, "\\'") + '\')"><i data-lucide="layers" style="width:16px;height:16px;margin-right:8px;"></i> Convert to LVM</button>';
            }
        }
        // Only show Unmount for non-system partitions
        if (isSystemPartition) {
            html += '<button class="btn btn-danger" style="justify-content: flex-start; opacity:0.5; cursor:not-allowed;" onclick="showToast(\'error\', \'Cannot unmount system partition - this would crash the server\')"><i data-lucide="shield-alert" style="width:16px;height:16px;margin-right:8px;"></i> Protected (System)</button>';
        } else {
            // Use stable_id if available for reliable device identification
            const unmountId = (part.stable_id || part.device || '').replace(/'/g, "\\'");
            html += '<button class="btn btn-danger" style="justify-content: flex-start;" onclick="promptAndUnmount(\'' + unmountId + '\')"><i data-lucide="hard-drive-download" style="width:16px;height:16px;margin-right:8px;"></i> Unmount</button>';
        }
    } else {
        // Use stable_id if available for reliable device identification
        const mountId = (part.stable_id || part.device || '').replace(/'/g, "\\'");
        const formatId = (part.stable_id || part.device || '').replace(/'/g, "\\'");
        html += '<button class="btn btn-primary" style="justify-content: flex-start;" onclick="openMountModal(\'' + mountId + '\')"><i data-lucide="arrow-down-to-line" style="width:16px;height:16px;margin-right:8px;"></i> Mount</button>';
        html += '<button class="btn btn-danger" style="justify-content: flex-start;" onclick="openFormatModal(\'' + formatId + '\', ' + (part.size_gb || 0) + ', \'' + (part.fstype || '').replace(/'/g, "\\'") + '\', \'' + (part.label || '').replace(/'/g, "\\'") + '\')"><i data-lucide="hard-drive" style="width:16px;height:16px;margin-right:8px;"></i> Format</button>';
    }
    // Add Wipe button for LVM partitions or drives with LVM structures (protected for system partitions)
    if (isLvm || part.vg_name || part.type === 'lvm' || part.fstype === 'LVM2_member') {
        if (isSystemPartition) {
            html += '<button class="btn btn-danger" style="justify-content: flex-start; opacity:0.5; cursor:not-allowed;" onclick="showToast(\'error\', \'Cannot wipe system LVM - this would destroy the operating system\')"><i data-lucide="shield-alert" style="width:16px;height:16px;margin-right:8px;"></i> Wipe LVM (Protected)</button>';
        } else {
            const wipeId = (part.stable_id || part.device || '').replace(/'/g, "\\'");
            const wipeInfo = (part.vg_name ? 'VG: ' + part.vg_name : '') + (part.lv_name ? ', LV: ' + part.lv_name : '') || part.fstype || 'LVM';
            html += '<button class="btn btn-danger" style="justify-content: flex-start;" onclick="openWipeModal(\'' + wipeId + '\', ' + (part.size_gb || 0) + ', \'' + wipeInfo.replace(/'/g, "\\'") + '\')"><i data-lucide="trash-2" style="width:16px;height:16px;margin-right:8px;"></i> Wipe LVM</button>';
        }
    }
    html += '<button class="btn btn-ghost" style="justify-content: flex-start;" onclick="registerManagedMount(\'' + (part.device || '').replace(/'/g, "\\'") + '\', \'' + (part.mountpoint || '').replace(/'/g, "\\'") + '\', \'' + (part.fstype || '').replace(/'/g, "\\'") + '\', ' + (part.size_gb || 0) + ', \'' + (disk.model || '').replace(/'/g, "\\'") + '\')"><i data-lucide="bookmark" style="width:16px;height:16px;margin-right:8px;"></i> Register as Managed</button>';
    html += '</div>';

    return html;
}

function buildLvmFreeModalContent(disk, vgRef) {
    const vgName = vgRef.replace('vg-free-', '');
    const vgFreeData = storageData?.vg_free?.[vgName];
    const freeGb = vgFreeData ? vgFreeData.free_gb : 0;

    let html = '<div style="background: var(--archie-bg-tertiary); padding: 12px; border-radius: 6px; margin-bottom: 16px;">';
    html += '<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">';
    html += '<div><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Volume Group</span><span class="mono" style="color: var(--archie-cyan);">' + vgName + '</span></div>';
    html += '<div><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Free Space</span><span style="color: var(--archie-green); font-weight: 600;">' + formatSizeGB(freeGb) + '</span></div>';
    html += '</div></div>';

    html += '<p style="color: var(--archie-text-secondary); font-size: 0.85rem; margin-bottom: 16px;">This free space can be used to create new logical volumes or extend existing ones.</p>';

    html += '<div style="display: flex; flex-direction: column; gap: 8px;">';
    html += '<button class="btn btn-primary" style="justify-content: flex-start;" onclick="openCreateLvModal(\'' + vgName.replace(/'/g, "\\'") + '\', ' + freeGb + ')"><i data-lucide="plus" style="width:16px;height:16px;margin-right:8px;"></i> Create New Logical Volume</button>';
    html += '<button class="btn btn-ghost" style="justify-content: flex-start;" onclick="closePartitionDetailsModal(); showToast(\'info\', \'Select an existing LV from the treemap, then use Extend Volume\')"><i data-lucide="expand" style="width:16px;height:16px;margin-right:8px;"></i> Extend Existing LV</button>';
    html += '</div>';

    return html;
}

function buildUnallocatedModalContent(disk) {
    let html = '<div style="background: var(--archie-bg-tertiary); padding: 12px; border-radius: 6px; margin-bottom: 16px;">';
    html += '<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">';
    html += '<div><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Drive</span><span class="mono" style="color: var(--archie-cyan);">' + disk.device + '</span></div>';
    html += '<div><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Unallocated</span><span style="color: var(--archie-purple); font-weight: 600;">' + formatSizeGB(disk.unallocated_gb) + '</span></div>';
    html += '<div style="grid-column: span 2;"><span style="color: var(--archie-text-muted); font-size: 0.8rem; display: block;">Model</span><span>' + (disk.model || 'Unknown') + '</span></div>';
    html += '</div></div>';

    html += '<p style="color: var(--archie-text-secondary); font-size: 0.85rem; margin-bottom: 16px;">This space is not assigned to any partition. You can create a new partition or extend an existing LVM volume group.</p>';

    html += '<div style="display: flex; flex-direction: column; gap: 8px;">';
    html += '<button class="btn btn-primary" style="justify-content: flex-start;" onclick="openCreatePartitionModal(\'' + (disk.device || '').replace(/'/g, "\\'") + '\', ' + (disk.unallocated_gb || 0) + ')"><i data-lucide="plus-circle" style="width:16px;height:16px;margin-right:8px;"></i> Create New Partition</button>';
    html += '</div>';

    return html;
}

// Old render functions removed - using modal-based functions now

async function browsePartition(mountpoint) {
    // Close any open modal
    closePartitionDetailsModal();

    // Show loading toast
    showToast('info', 'Opening file browser for ' + mountpoint + '...');

    try {
        const res = await fetch('/dashboard/api/storage/browse?path=' + encodeURIComponent(mountpoint));
        const json = await res.json();
        if (json.success) {
            // For now, show a summary toast. A full file browser modal could be added later.
            const entries = json.data.entries || [];
            const dirs = entries.filter(e => e.is_dir).length;
            const files = entries.length - dirs;
            showToast('success', 'Path: ' + json.data.path + ' - ' + dirs + ' directories, ' + files + ' files');
        } else {
            showToast('error', 'Failed to browse: ' + (json.error || 'Unknown error'));
        }
    } catch (err) {
        showToast('error', 'Browse failed: ' + err.message);
    }
}

async function registerManagedMount(device, mountPoint, filesystem, capacityGb, driveModel) {
    try {
        const res = await fetch('/dashboard/api/storage/mounts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                device: device,
                mount_point: mountPoint,
                filesystem: filesystem,
                capacity_gb: capacityGb,
                drive_model: driveModel,
                is_managed: true
            })
        });
        const json = await res.json();
        if (json.success) {
            showToast('success', 'Registered ' + device + ' as managed mount');
        } else {
            showToast('error', json.error || 'Failed to register');
        }
    } catch (e) {
        showToast('error', 'Error: ' + e.message);
    }
}

// ===== Re-Authentication Modal =====

function requestReauth(operationName, callback) {
    reauthCallback = callback;
    document.getElementById('reauthMessage').textContent = 'Operation: ' + operationName + '. Enter your password to continue.';
    document.getElementById('reauthPassword').value = '';
    document.getElementById('reauthError').style.display = 'none';
    document.getElementById('reauthModal').classList.add('active');
    setTimeout(() => document.getElementById('reauthPassword').focus(), 100);
}

function closeReauthModal() {
    document.getElementById('reauthModal').classList.remove('active');
    document.getElementById('reauthPassword').value = '';
    document.getElementById('reauthError').style.display = 'none';
    reauthCallback = null;
}

async function submitReauth() {
    const password = document.getElementById('reauthPassword').value;
    if (!password) {
        document.getElementById('reauthError').textContent = 'Password is required';
        document.getElementById('reauthError').style.display = 'block';
        return;
    }

    try {
        const res = await fetch('/dashboard/api/auth/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: password })
        });
        const json = await res.json();
        if (json.success) {
            // Save callback before closing modal (closeReauthModal clears reauthCallback)
            const callbackToRun = reauthCallback;
            closeReauthModal();
            showToast('success', 'Re-authenticated successfully');
            if (callbackToRun) {
                callbackToRun();
            }
        } else {
            document.getElementById('reauthError').textContent = json.error || 'Authentication failed';
            document.getElementById('reauthError').style.display = 'block';
            document.getElementById('reauthPassword').value = '';
            document.getElementById('reauthPassword').focus();
        }
    } catch (e) {
        document.getElementById('reauthError').textContent = 'Error: ' + e.message;
        document.getElementById('reauthError').style.display = 'block';
    }
}

// Render Functions
function renderPartitions(partitions) {
    const tbody = document.getElementById('partitions-table');
    if (!partitions.length) {
        tbody.innerHTML = '<tr><td colspan="9" style="color: var(--archie-text-muted)">No partitions found</td></tr>';
        return;
    }
    tbody.innerHTML = partitions.map(p => {
        const lvmInfo = p.lvm_info;
        const canExtend = lvmInfo && lvmInfo.can_extend;
        const vgFree = lvmInfo ? lvmInfo.vg_free_gb : 0;

        return `
        <tr>
            <td class="mono">
                ${p.device || '--'}
                ${p.is_lvm ? '<span class="lvm-badge">LVM</span>' : ''}
            </td>
            <td class="mono">${p.mountpoint || '--'}</td>
            <td>${p.fstype || '--'}</td>
            <td class="mono" style="font-size: 0.8rem;">
                ${p.parent_disk || '--'}
                ${lvmInfo && lvmInfo.vg_name ? `<br><span style="color: var(--archie-purple);">${lvmInfo.vg_name}/${lvmInfo.lv_name}</span>` : ''}
            </td>
            <td>${p.total_gb || 0} GB</td>
            <td>${p.used_gb || 0} GB</td>
            <td>${p.free_gb || 0} GB</td>
            <td>
                <div class="progress-bar" style="width: 80px; display: inline-block; vertical-align: middle;">
                    <div class="progress-fill ${getColorClass(p.usage_percent || 0, 80, 90)}" style="width: ${p.usage_percent || 0}%"></div>
                </div>
                <span style="margin-left: 8px;">${(p.usage_percent || 0).toFixed(1)}%</span>
            </td>
            <td>
                ${canExtend ?
                    `<button class="extend-btn" onclick="openLvmExtendModal('${lvmInfo.lv_name}', '${lvmInfo.vg_name}', ${p.total_gb || 0}, ${vgFree}, '${(p.mountpoint || '').replace(/'/g, "\\'")}')">
                        Extend <span class="vg-free-badge">+${vgFree} GB</span>
                    </button>` :
                    (p.is_lvm ? '<span style="color: var(--archie-text-muted); font-size: 0.75rem;">No free space</span>' : '-')
                }
            </td>
        </tr>`;
    }).join('');
}

function renderLVM(lvm) {
    const vgsTbody = document.getElementById('lvm-vgs');
    const lvsTbody = document.getElementById('lvm-lvs');
    const pvsTbody = document.getElementById('lvm-pvs');
    const noneNotice = document.getElementById('lvm-none');
    const lvmPanel = document.querySelector('.lvm-grid');

    if (!lvm || (!lvm.vgs?.length && !lvm.lvs?.length && !lvm.pvs?.length)) {
        if (lvmPanel) lvmPanel.style.display = 'none';
        if (noneNotice) noneNotice.style.display = 'block';
        return;
    }

    if (lvmPanel) lvmPanel.style.display = 'grid';
    if (noneNotice) noneNotice.style.display = 'none';

    // Volume Groups
    if (lvm.vgs?.length) {
        vgsTbody.innerHTML = lvm.vgs.map(vg => `
            <tr>
                <td class="mono">${vg.vg_name}</td>
                <td>${vg.vg_size_gb} GB</td>
                <td style="color: ${vg.vg_free_gb > 0 ? 'var(--archie-green)' : 'var(--archie-text-muted)'}">
                    ${vg.vg_free_gb > 0 ? vg.vg_free_gb + ' GB' : 'Fully allocated'}
                    ${vg.vg_free_gb === 0 ? '<span style="font-size:0.7rem; margin-left:5px;" title="All VG space is used by logical volumes. This is normal.">(OK)</span>' : ''}
                </td>
            </tr>
        `).join('');
    } else {
        vgsTbody.innerHTML = '<tr><td colspan="3" style="color: var(--archie-text-muted)">None</td></tr>';
    }

    // Logical Volumes
    if (lvm.lvs?.length) {
        lvsTbody.innerHTML = lvm.lvs.map(lv => `
            <tr>
                <td class="mono">${lv.lv_name}</td>
                <td>${lv.vg_name}</td>
                <td>${lv.lv_size_gb} GB</td>
            </tr>
        `).join('');
    } else {
        lvsTbody.innerHTML = '<tr><td colspan="3" style="color: var(--archie-text-muted)">None</td></tr>';
    }

    // Physical Volumes
    if (lvm.pvs?.length) {
        pvsTbody.innerHTML = lvm.pvs.map(pv => `
            <tr>
                <td class="mono">${pv.pv_name}</td>
                <td>${pv.vg_name}</td>
                <td>${pv.pv_size}</td>
            </tr>
        `).join('');
    } else {
        pvsTbody.innerHTML = '<tr><td colspan="3" style="color: var(--archie-text-muted)">None</td></tr>';
    }
}

function renderRaid(raid) {
    const container = document.getElementById('raid-status');
    if (!raid || !raid.arrays || !raid.arrays.length) {
        container.innerHTML = '<p style="color: var(--archie-text-muted)">No RAID arrays detected</p>';
        return;
    }
    container.innerHTML = raid.arrays.map(a => `
        <div style="margin-bottom: 12px; padding: 10px; background: var(--archie-bg-tertiary); border-radius: 6px;">
            <strong style="color: var(--archie-accent)">${a.name}</strong>
            <span class="badge ${a.status}" style="margin-left: 10px;">${a.status}</span>
            <span style="color: var(--archie-text-muted); margin-left: 10px;">${a.level}</span>
        </div>
    `).join('');
}

function renderCapacityAlerts(alerts) {
    const banner = document.getElementById('capacity-alerts-banner');
    if (!banner) return;

    if (!alerts || alerts.length === 0) {
        banner.style.display = 'none';
        return;
    }

    // Filter to show only warnings and critical alerts
    const significantAlerts = alerts.filter(a => a.severity === 'critical' || a.severity === 'warning');

    if (significantAlerts.length === 0) {
        banner.style.display = 'none';
        return;
    }

    banner.style.display = 'block';

    const severityColors = {
        'critical': { bg: 'var(--archie-red)', icon: 'alert-circle' },
        'warning': { bg: 'var(--archie-orange)', icon: 'alert-triangle' },
        'notice': { bg: 'var(--archie-yellow)', icon: 'info' }
    };

    // Build alerts using DOM methods for safety
    while (banner.firstChild) banner.removeChild(banner.firstChild);

    significantAlerts.forEach(alert => {
        const colors = severityColors[alert.severity] || severityColors.notice;
        const alertDiv = document.createElement('div');
        alertDiv.style.cssText = 'display: flex; align-items: center; gap: 10px; padding: 10px 14px; background: ' + colors.bg + '22; border: 1px solid ' + colors.bg + '44; border-radius: 6px; margin-bottom: 8px;';

        const icon = document.createElement('i');
        icon.setAttribute('data-lucide', colors.icon);
        icon.style.cssText = 'width: 18px; height: 18px; color: ' + colors.bg + ';';
        alertDiv.appendChild(icon);

        const textDiv = document.createElement('div');
        textDiv.style.flex = '1';

        const sevSpan = document.createElement('span');
        sevSpan.style.cssText = 'color: ' + colors.bg + '; font-weight: 600;';
        sevSpan.textContent = alert.severity.toUpperCase();
        textDiv.appendChild(sevSpan);

        const msgSpan = document.createElement('span');
        msgSpan.style.cssText = 'color: var(--archie-text-primary); margin-left: 8px;';
        msgSpan.textContent = (alert.mount || alert.device) + ' is ' + alert.percent.toFixed(0) + '% full';
        textDiv.appendChild(msgSpan);

        alertDiv.appendChild(textDiv);
        banner.appendChild(alertDiv);
    });

    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function renderSmartHealth(smartDetails) {
    const container = document.getElementById('smart-health-content');
    if (!container) return;

    // Clear container
    while (container.firstChild) container.removeChild(container.firstChild);

    if (!smartDetails || Object.keys(smartDetails).length === 0) {
        const msg = document.createElement('p');
        msg.style.color = 'var(--archie-text-muted)';
        msg.textContent = 'SMART data not available. Ensure smartctl is installed and host_monitor.py is running with sudo access.';
        container.appendChild(msg);
        return;
    }

    // Sort disks by name
    const diskNames = Object.keys(smartDetails).sort();

    diskNames.forEach(name => {
        const d = smartDetails[name];

        // Health status colors
        const healthColors = {
            'healthy': { color: 'var(--archie-green)', label: 'HEALTHY' },
            'failing': { color: 'var(--archie-red)', label: 'FAILING' },
            'unknown': { color: 'var(--archie-orange)', label: 'UNKNOWN' },
            'unavailable': { color: 'var(--archie-text-muted)', label: 'N/A' }
        };
        const health = healthColors[d.health_status] || healthColors.unavailable;

        // Create card
        const card = document.createElement('div');
        card.className = 'smart-card';
        card.style.cssText = 'padding: 12px; background: var(--archie-bg-tertiary); border-radius: 8px; border: 1px solid var(--archie-border);';

        // Header row
        const headerRow = document.createElement('div');
        headerRow.style.cssText = 'display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;';

        const nameDiv = document.createElement('div');
        const deviceName = document.createElement('div');
        deviceName.style.cssText = 'font-weight: 600; color: var(--archie-cyan); font-size: 0.95rem;';
        deviceName.textContent = '/dev/' + name;
        nameDiv.appendChild(deviceName);

        const modelDiv = document.createElement('div');
        modelDiv.style.cssText = 'font-size: 0.75rem; color: var(--archie-text-muted); margin-top: 2px;';
        modelDiv.textContent = d.model || 'Unknown Model';
        nameDiv.appendChild(modelDiv);
        headerRow.appendChild(nameDiv);

        const healthBadge = document.createElement('span');
        healthBadge.style.cssText = 'padding: 3px 8px; font-size: 0.7rem; border-radius: 4px; background: ' + health.color + '22; color: ' + health.color + '; font-weight: 600;';
        healthBadge.textContent = health.label;
        headerRow.appendChild(healthBadge);
        card.appendChild(headerRow);

        // Stats grid
        const statsGrid = document.createElement('div');
        statsGrid.style.cssText = 'display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; font-size: 0.8rem;';

        // Temperature
        const tempDiv = document.createElement('div');
        const tempLabel = document.createElement('div');
        tempLabel.style.cssText = 'color: var(--archie-text-muted); font-size: 0.7rem;';
        tempLabel.textContent = 'TEMP';
        tempDiv.appendChild(tempLabel);
        const tempValue = document.createElement('div');
        if (d.temperature_celsius !== null && d.temperature_celsius !== undefined) {
            const temp = d.temperature_celsius;
            let tempColor = 'var(--archie-green)';
            if (temp >= 60) tempColor = 'var(--archie-red)';
            else if (temp >= 50) tempColor = 'var(--archie-orange)';
            else if (temp >= 45) tempColor = 'var(--archie-yellow)';
            tempValue.style.cssText = 'color: ' + tempColor + '; font-weight: 600;';
            tempValue.textContent = temp + '°C';
        } else {
            tempValue.textContent = '--';
        }
        tempDiv.appendChild(tempValue);
        statsGrid.appendChild(tempDiv);

        // Power-on hours
        const hoursDiv = document.createElement('div');
        const hoursLabel = document.createElement('div');
        hoursLabel.style.cssText = 'color: var(--archie-text-muted); font-size: 0.7rem;';
        hoursLabel.textContent = 'POWER ON';
        hoursDiv.appendChild(hoursLabel);
        const hoursValue = document.createElement('div');
        if (d.power_on_hours !== null && d.power_on_hours !== undefined) {
            const hours = d.power_on_hours;
            const days = Math.floor(hours / 24);
            const years = (hours / 8760).toFixed(1);
            hoursValue.title = hours.toLocaleString() + ' hours';
            hoursValue.textContent = hours >= 8760 ? years + 'y' : days + 'd';
        } else {
            hoursValue.textContent = '--';
        }
        hoursDiv.appendChild(hoursValue);
        statsGrid.appendChild(hoursDiv);

        // Sector health
        const sectorDiv = document.createElement('div');
        const sectorInfo = [];
        if (d.reallocated_sector_count !== null && d.reallocated_sector_count !== undefined) {
            sectorInfo.push('Reallocated: ' + d.reallocated_sector_count);
        }
        if (d.current_pending_sector !== null && d.current_pending_sector !== undefined) {
            sectorInfo.push('Pending: ' + d.current_pending_sector);
        }
        if (d.offline_uncorrectable !== null && d.offline_uncorrectable !== undefined) {
            sectorInfo.push('Uncorrectable: ' + d.offline_uncorrectable);
        }
        sectorDiv.title = sectorInfo.join(' | ') || 'No sector issues';
        const sectorLabel = document.createElement('div');
        sectorLabel.style.cssText = 'color: var(--archie-text-muted); font-size: 0.7rem;';
        sectorLabel.textContent = 'SECTORS';
        sectorDiv.appendChild(sectorLabel);
        const sectorValue = document.createElement('div');
        const totalIssues = (d.reallocated_sector_count || 0) + (d.current_pending_sector || 0) + (d.offline_uncorrectable || 0);
        sectorValue.style.color = totalIssues > 0 ? 'var(--archie-orange)' : 'var(--archie-green)';
        sectorValue.textContent = totalIssues > 0 ? 'Issues' : 'OK';
        sectorDiv.appendChild(sectorValue);
        statsGrid.appendChild(sectorDiv);

        card.appendChild(statsGrid);

        // Warnings
        if (d.warnings && d.warnings.length > 0) {
            const warningsDiv = document.createElement('div');
            warningsDiv.style.cssText = 'margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--archie-border);';
            d.warnings.forEach(w => {
                const warnLine = document.createElement('div');
                warnLine.style.cssText = 'color: var(--archie-orange); font-size: 0.75rem; display: flex; align-items: center; gap: 4px;';
                const warnIcon = document.createElement('i');
                warnIcon.setAttribute('data-lucide', 'alert-triangle');
                warnIcon.style.cssText = 'width: 12px; height: 12px;';
                warnLine.appendChild(warnIcon);
                const warnText = document.createElement('span');
                warnText.textContent = w;
                warnLine.appendChild(warnText);
                warningsDiv.appendChild(warnLine);
            });
            card.appendChild(warningsDiv);
        }

        container.appendChild(card);
    });

    if (typeof lucide !== 'undefined') lucide.createIcons();
}

// Current category filter
let processCategoryFilter = 'all';

function filterProcessesByCategory(category) {
    processCategoryFilter = category;
    filterProcesses(true);
}

function filterProcesses(resetPage = true) {
    const filter = document.getElementById('process-filter').value.toLowerCase();

    filteredProcesses = allProcesses.filter(p => {
        // Text filter
        const matchesText = (p.name || '').toLowerCase().includes(filter) ||
            (p.user || '').toLowerCase().includes(filter) ||
            (p.command || '').toLowerCase().includes(filter) ||
            (p.container || '').toLowerCase().includes(filter) ||
            String(p.pid || '').includes(filter);

        // Category filter
        const matchesCategory = processCategoryFilter === 'all' ||
            (p.category || 'user') === processCategoryFilter;

        return matchesText && matchesCategory;
    });

    // Reset to page 1 when filter changes
    if (resetPage) processCurrentPage = 1;

    renderProcessPage();
}

function getCategoryBadge(category) {
    const badges = {
        'docker': { class: 'docker-badge', label: 'DOCKER', color: 'var(--archie-purple)' },
        'system': { class: 'system-badge', label: 'SYSTEM', color: 'var(--archie-orange)' },
        'kernel': { class: 'kernel-badge', label: 'KERNEL', color: 'var(--archie-text-muted)' },
        'user': { class: 'user-badge', label: 'USER', color: 'var(--archie-cyan)' }
    };
    const badge = badges[category] || badges['user'];
    return `<span style="background: ${badge.color}22; color: ${badge.color}; padding: 2px 6px; border-radius: 3px; font-size: 0.65rem; font-weight: 600;">${badge.label}</span>`;
}

function renderProcessPage() {
    const tbody = document.getElementById('processes-table');
    const total = filteredProcesses.length;

    if (!total) {
        tbody.innerHTML = '<tr><td colspan="8" style="color: var(--archie-text-muted)">No processes found</td></tr>';
        updateProcessPagination(0, 0, 0);
        return;
    }

    // Update process count in section title
    const countEl = document.getElementById('process-count-badge');
    if (countEl) countEl.textContent = total;

    // Calculate pagination
    const totalPages = Math.ceil(total / processPageSize);
    if (processCurrentPage > totalPages) processCurrentPage = totalPages;
    const start = (processCurrentPage - 1) * processPageSize;
    const end = Math.min(start + processPageSize, total);
    const pageData = filteredProcesses.slice(start, end);

    tbody.innerHTML = pageData.map(p => {
        const isHost = p.from_host;
        const rssLabel = p.rss_kb ? ` (${(p.rss_kb / 1024).toFixed(0)} MB)` : '';
        const escapedCmd = (p.command || p.name || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
        const escapedName = (p.name || '').replace(/'/g, "\\'");
        const category = p.category || 'user';
        const container = p.container || null;
        const containerDisplay = p.container_display || container;
        const escapedContainer = container ? container.replace(/'/g, "\\'") : '';

        // Container badge for Docker processes
        let containerBadge = '';
        if (container) {
            const isArchie = containerDisplay === 'A.R.C.H.I.E.';
            const badgeColor = isArchie ? 'var(--archie-green)' : 'var(--archie-purple)';
            containerBadge = `<span style="background: ${badgeColor}22; color: ${badgeColor}; padding: 2px 6px; border-radius: 3px; font-size: 0.65rem; margin-left: 6px; font-weight: 500;">${containerDisplay}</span>`;
        }

        // Actions based on category
        let actions = '';
        if (isHost) {
            if (category === 'docker' && container) {
                // Docker container processes - can restart/stop the container
                actions = `
                    <button class="action-btn restart" onclick="restartDockerContainer('${escapedContainer}')" title="Restart container">Restart</button>
                    <button class="action-btn stop" onclick="stopDockerContainer('${escapedContainer}')" title="Stop container">Stop</button>
                `;
            } else if (category === 'kernel') {
                // Kernel threads - no actions
                actions = '<span style="color: var(--archie-text-muted); font-size: 0.7rem;">Kernel thread</span>';
            } else if (category === 'system') {
                // System processes - read only for safety
                actions = '<span style="color: var(--archie-text-muted); font-size: 0.7rem;">System</span>';
            } else {
                // User processes - can kill
                actions = `<button class="action-btn kill" onclick="killHostProcess(${p.pid}, '${escapedName}')" title="Kill process">Kill</button>`;
            }
        } else {
            actions = `<button class="action-btn kill" onclick="killProcess(${p.pid}, '${escapedName}')">Kill</button>`;
        }

        return `
        <tr>
            <td class="mono">${p.pid || '--'}</td>
            <td title="${escapedCmd}">
                ${p.name || '--'}
                ${isHost ? '<span class="host-badge">HOST</span>' : ''}
                ${containerBadge}
            </td>
            <td>${getCategoryBadge(category)}</td>
            <td>${p.user || '--'}</td>
            <td>${(p.cpu_percent || 0).toFixed(1)}%</td>
            <td>${(p.memory_percent || 0).toFixed(1)}%${rssLabel}</td>
            <td><span class="badge ${(p.status || '').replace(/[^a-z-]/gi, '').toLowerCase()}">${p.status || '--'}</span></td>
            <td>${actions}</td>
        </tr>`;
    }).join('');

    updateProcessPagination(start + 1, end, total);
}

function updateProcessPagination(start, end, total) {
    const pageInfo = document.getElementById('process-page-info');
    const prevBtn = document.getElementById('process-prev-btn');
    const nextBtn = document.getElementById('process-next-btn');

    if (pageInfo) {
        pageInfo.textContent = total > 0 ? `Showing ${start}-${end} of ${total}` : 'No processes';
    }
    if (prevBtn) prevBtn.disabled = processCurrentPage <= 1;
    if (nextBtn) nextBtn.disabled = end >= total;
}

function changeProcessPageSize(newSize) {
    processPageSize = parseInt(newSize);
    processCurrentPage = 1;
    renderProcessPage();
}

function processPagePrev() {
    if (processCurrentPage > 1) {
        processCurrentPage--;
        renderProcessPage();
    }
}

function processPageNext() {
    const totalPages = Math.ceil(filteredProcesses.length / processPageSize);
    if (processCurrentPage < totalPages) {
        processCurrentPage++;
        renderProcessPage();
    }
}

function renderServices(services) {
    const tbody = document.getElementById('services-table');
    const dockerNotice = document.getElementById('services-docker-notice');

    // Check if we have host services available
    const hostServices = window.hostServicesData || [];
    const allServices = [...hostServices, ...services];

    if (!allServices.length) {
        // Show Docker limitation notice
        if (dockerNotice) dockerNotice.style.display = 'flex';
        tbody.innerHTML = '<tr><td colspan="4" style="color: var(--archie-text-muted)">Waiting for host monitor data...</td></tr>';
        return;
    }

    if (dockerNotice) dockerNotice.style.display = hostServices.length ? 'none' : 'flex';
    tbody.innerHTML = allServices.slice(0, 50).map(s => `
        <tr>
            <td>
                ${s.name || '--'}
                ${s.from_host ? '<span class="host-badge">HOST</span>' : ''}
            </td>
            <td><span class="badge ${(s.status || '').toLowerCase()}">${s.status || '--'}</span></td>
            <td>${s.enabled ? 'Yes' : 'No'}</td>
            <td>
                ${s.from_host ? '<span style="color: var(--archie-text-muted); font-size: 0.75rem;">Read-only</span>' :
                    (s.status === 'active'
                        ? `<button class="action-btn stop" onclick="serviceAction('${s.name}', 'stop')">Stop</button>
                           <button class="action-btn restart" onclick="serviceAction('${s.name}', 'restart')">Restart</button>`
                        : `<button class="action-btn start" onclick="serviceAction('${s.name}', 'start')">Start</button>`)
                }
            </td>
        </tr>
    `).join('');
}

// Host data render functions
function renderHostServices(services) {
    if (!services || !services.length) return;
    window.hostServicesData = services.map(s => ({ ...s, from_host: true }));
    // Trigger re-render of services table
    renderServices([]);
}

function renderHostLVM(lvm) {
    if (!lvm) return;

    // Store LVM data globally for extend modal
    window.hostLvmData = lvm;

    const vgsTbody = document.getElementById('lvm-vgs');
    const lvsTbody = document.getElementById('lvm-lvs');
    const pvsTbody = document.getElementById('lvm-pvs');
    const noneNotice = document.getElementById('lvm-none');
    const lvmPanel = document.querySelector('.lvm-grid');

    if (!lvm.vgs?.length && !lvm.lvs?.length && !lvm.pvs?.length) {
        return; // Keep container detection if no host data
    }

    if (lvmPanel) lvmPanel.style.display = 'grid';
    if (noneNotice) noneNotice.style.display = 'none';

    // Build VG lookup for free space
    const vgFreeSpace = {};
    if (lvm.vgs?.length) {
        lvm.vgs.forEach(vg => {
            vgFreeSpace[vg.vg_name] = vg.vg_free_gb || 0;
        });
    }

    // Volume Groups from host (with Create LV button when free space available)
    if (lvm.vgs?.length) {
        vgsTbody.innerHTML = lvm.vgs.map(vg => {
            const hasSpace = vg.vg_free_gb > 0;
            return `
            <tr>
                <td class="mono">${vg.vg_name} <span class="host-badge">HOST</span></td>
                <td>${vg.vg_size_gb} GB</td>
                <td style="color: ${hasSpace ? 'var(--archie-green)' : 'var(--archie-text-muted)'}">
                    ${hasSpace ? vg.vg_free_gb + ' GB' : 'Fully allocated'}
                    ${!hasSpace ? '<span style="font-size:0.7rem; margin-left:5px;" title="All VG space is used by logical volumes. This is normal.">(OK)</span>' : ''}
                    ${hasSpace ? `<button class="action-btn start" onclick="openCreateLvModal('${vg.vg_name}', ${vg.vg_free_gb})" title="Create new logical volume in this VG" style="padding:2px 8px; font-size:0.7rem; margin-left:8px;"><i data-lucide="plus" style="width:10px;height:10px;display:inline-block;vertical-align:middle;"></i> Create LV</button>` : ''}
                </td>
            </tr>
        `}).join('');
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    // Logical Volumes from host (with extend and snapshot buttons)
    if (lvm.lvs?.length) {
        lvsTbody.innerHTML = lvm.lvs.map(lv => {
            const freeSpace = vgFreeSpace[lv.vg_name] || 0;
            const hasSpace = freeSpace > 0;
            return `
            <tr>
                <td class="mono">${lv.lv_name}</td>
                <td>${lv.vg_name}</td>
                <td>${lv.lv_size_gb} GB</td>
                <td style="display: flex; gap: 4px; flex-wrap: wrap;">
                    <button class="action-btn ${hasSpace ? 'start' : ''}"
                            onclick="openLvmExtendModal('${lv.lv_name}', '${lv.vg_name}', ${lv.lv_size_gb}, ${freeSpace})"
                            title="${hasSpace ? 'Extend this logical volume' : 'No free space in VG - click for options'}">
                        <i data-lucide="${hasSpace ? 'plus' : 'info'}" style="width:12px; height:12px; display:inline-block; vertical-align:middle;"></i>
                        ${hasSpace ? 'Extend' : 'Manage'}
                    </button>
                    ${hasSpace ? `<button class="action-btn" onclick="openSnapshotModal('${lv.vg_name}', '${lv.lv_name}', ${freeSpace})" title="Create snapshot of this LV"><i data-lucide="copy" style="width:12px; height:12px; display:inline-block; vertical-align:middle;"></i></button>` : ''}
                </td>
            </tr>
        `}).join('');
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    // Physical Volumes from host
    if (lvm.pvs?.length) {
        pvsTbody.innerHTML = lvm.pvs.map(pv => `
            <tr>
                <td class="mono">${pv.pv_name}</td>
                <td>${pv.vg_name}</td>
                <td>${pv.pv_size}</td>
            </tr>
        `).join('');
    }
}

function renderHostDisks(disks) {
    const container = document.getElementById('host-disks-content');
    if (!container) return;

    if (!disks || !disks.length) {
        container.innerHTML = `
            <p style="color: var(--archie-text-muted)">
                <i data-lucide="info" style="width:14px; height:14px; display:inline-block; vertical-align: middle;"></i>
                Disk layout not available. Restart host monitor service to collect data.
            </p>
        `;
        if (typeof lucide !== 'undefined') lucide.createIcons();
        return;
    }

    let html = '';

    disks.forEach(disk => {
        const usedPct = disk.size_gb > 0
            ? ((disk.size_gb - (disk.unallocated_gb || 0)) / disk.size_gb * 100).toFixed(0)
            : 100;

        html += `
        <div class="host-disk-card" style="margin-bottom: 16px; padding: 16px; background: var(--archie-bg-tertiary); border-radius: 8px; border: 1px solid var(--archie-border-default);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <div>
                    <strong style="color: var(--archie-accent); font-size: 1.1rem;">${disk.device}</strong>
                    <span style="color: var(--archie-text-muted); margin-left: 12px; font-size: 0.85rem;">
                        ${disk.model || 'Unknown Model'}
                    </span>
                </div>
                <div style="text-align: right;">
                    <span style="font-size: 1.2rem; color: var(--archie-text-primary);">${disk.size_gb} GB</span>
                </div>
            </div>

            <div class="progress-container" style="margin-bottom: 12px;">
                <div class="progress-bar" style="height: 8px; background: var(--archie-bg-secondary); border-radius: 4px; overflow: hidden;">
                    <div style="width: ${usedPct}%; height: 100%; background: var(--archie-accent); transition: width 0.3s;"></div>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.75rem; color: var(--archie-text-muted); margin-top: 4px;">
                    <span>Allocated: ${(disk.size_gb - (disk.unallocated_gb || 0)).toFixed(1)} GB</span>
                    ${disk.unallocated_gb > 0
                        ? `<span style="color: var(--archie-green);">Free: ${disk.unallocated_gb} GB available</span>`
                        : '<span>Fully partitioned</span>'}
                </div>
            </div>

            <div class="partitions-section">
                <div style="font-size: 0.8rem; color: var(--archie-cyan); margin-bottom: 8px;">
                    <i data-lucide="layers" style="width:12px; height:12px; display:inline-block; vertical-align: middle;"></i>
                    Partitions (${disk.partitions?.length || 0})
                </div>
                <table class="data-table" style="font-size: 0.85rem;">
                    <thead>
                        <tr>
                            <th>Device</th>
                            <th>Type</th>
                            <th>Size</th>
                            <th>Mount Point</th>
                            <th>Children (LVM/RAID)</th>
                        </tr>
                    </thead>
                    <tbody>
        `;

        if (disk.partitions?.length) {
            disk.partitions.forEach(part => {
                const childrenStr = part.children?.length
                    ? part.children.map(c => `${c.name} (${c.type})`).join(', ')
                    : '-';
                const mountStr = part.mountpoint || (part.children?.find(c => c.mountpoint)?.mountpoint) || '-';

                html += `
                        <tr>
                            <td class="mono">${part.device}</td>
                            <td><span class="badge">${part.fstype || part.type || 'unknown'}</span></td>
                            <td>${part.size_gb} GB</td>
                            <td class="mono" style="color: ${mountStr !== '-' ? 'var(--archie-cyan)' : 'var(--archie-text-muted)'};">${mountStr}</td>
                            <td style="font-size: 0.75rem; color: var(--archie-text-muted);">${childrenStr}</td>
                        </tr>
                `;
            });
        } else {
            html += `
                        <tr><td colspan="5" style="color: var(--archie-text-muted); text-align: center;">No partitions found</td></tr>
            `;
        }

        html += `
                    </tbody>
                </table>
            </div>

            ${disk.unallocated_gb > 0.5 ? `
            <div style="margin-top: 12px; padding: 10px; background: rgba(0,255,136,0.1); border: 1px solid var(--archie-green); border-radius: 6px;">
                <i data-lucide="plus-circle" style="width:14px; height:14px; display:inline-block; vertical-align: middle; color: var(--archie-green);"></i>
                <span style="color: var(--archie-green); margin-left: 6px; font-size: 0.85rem;">
                    ${disk.unallocated_gb} GB unallocated space available for new partitions or LVM extension
                </span>
            </div>
            ` : ''}
        </div>
        `;
    });

    container.innerHTML = html;
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function renderHostRaid(raid) {
    if (!raid || !raid.arrays?.length) return;

    const container = document.getElementById('raid-status');
    container.innerHTML = raid.arrays.map(a => `
        <div style="margin-bottom: 12px; padding: 10px; background: var(--archie-bg-tertiary); border-radius: 6px;">
            <strong style="color: var(--archie-accent)">${a.name}</strong>
            <span class="badge ${a.status}" style="margin-left: 10px;">${a.status}</span>
            <span style="color: var(--archie-text-muted); margin-left: 10px;">${a.level}</span>
            <span class="host-badge" style="margin-left: 10px;">HOST</span>
        </div>
    `).join('');
}

// renderStackOverview: renders stack cards into the overview grid
// NOTE: All data comes from authenticated API endpoints (admin-only), not user input.
// This follows the same trusted-source template literal pattern used throughout
// the existing codebase (renderNetwork, renderServices, etc.)
function renderStackOverview(stacks) {
    // Sum running/stopped across all stacks
    let totalRunning = 0, totalStopped = 0, totalContainers = 0;
    stacks.forEach(s => {
        totalRunning += s.containers_running || 0;
        totalStopped += (s.containers_total || 0) - (s.containers_running || 0);
        totalContainers += s.containers_total || 0;
    });
    document.getElementById('docker-running-count').textContent = totalRunning;
    document.getElementById('docker-stopped-count').textContent = totalStopped;
    document.getElementById('docker-total-count').textContent = totalContainers;
    document.getElementById('docker-stacks-count').textContent = stacks.length;

    const grid = document.getElementById('stack-overview-grid');
    if (!stacks.length) {
        grid.innerHTML = '<div class="loading-placeholder"><i data-lucide="inbox" style="width: 32px; height: 32px;"></i>No stacks found</div>';
    } else {
        grid.innerHTML = stacks.map(s => {
            const statusClass = s.status === 'running' ? 'running' : (s.status === 'partial' ? 'paused' : 'stopped');
            const statusText = s.status === 'running' ? 'Running' : (s.status === 'partial' ? 'Partial' : (s.status === 'stopped' ? 'Stopped' : 'Unknown'));

            // Health badge
            let healthBadge = '';
            if (s.health_summary) {
                const healthColors = {
                    'healthy': { bg: 'rgba(0,255,65,0.15)', color: 'var(--archie-green)', icon: '♥', text: 'Healthy' },
                    'unhealthy': { bg: 'rgba(255,107,107,0.15)', color: 'var(--archie-red)', icon: '!', text: 'Unhealthy' },
                    'starting': { bg: 'rgba(255,170,0,0.15)', color: 'var(--archie-orange)', icon: '◐', text: 'Starting' },
                    'degraded': { bg: 'rgba(255,170,0,0.15)', color: 'var(--archie-orange)', icon: '◑', text: 'Degraded' },
                    'none': { bg: 'rgba(110,118,129,0.15)', color: 'var(--archie-text-muted)', icon: '○', text: 'No healthcheck' }
                };
                const h = healthColors[s.health_summary] || healthColors['none'];
                healthBadge = `<span class="badge" style="background: ${h.bg}; color: ${h.color};" title="${h.text}">${h.icon}</span>`;
            }

            // Resource usage
            let resourceStats = '';
            if (s.total_cpu !== undefined || s.total_mem !== undefined) {
                const cpu = (s.total_cpu || 0).toFixed(1);
                const mem = (s.total_mem || 0).toFixed(1);
                resourceStats = `<span style="color: var(--archie-cyan);"><i data-lucide="cpu" style="width: 12px; height: 12px;"></i> ${cpu}%</span>
                    <span style="color: var(--archie-purple);"><i data-lucide="memory-stick" style="width: 12px; height: 12px;"></i> ${mem}%</span>`;
            }

            // Restart warning
            let restartWarning = '';
            if (s.restart_total && s.restart_total > 0) {
                restartWarning = `<span style="color: var(--archie-orange);" title="${s.restart_total} restart(s)"><i data-lucide="rotate-ccw" style="width: 12px; height: 12px;"></i> ${s.restart_total}</span>`;
            }

            // Container list
            let containerList = '';
            if (s.containers && s.containers.length > 0) {
                containerList = `<div class="stack-container-list">
                    ${s.containers.map(c => {
                        const stateClass = c.state === 'running' ? 'running' : 'stopped';
                        const stateIcon = c.state === 'running' ? '●' : '○';
                        return `<span class="stack-container-item ${stateClass}" title="${c.name}: ${c.status || c.state}">${stateIcon} ${c.name}</span>`;
                    }).join('')}
                </div>`;
            }

            return `
            <div class="stack-card" style="--stack-color: ${s.color || '#00D4AA'}" onclick="openStackDetail('${s.name}')">
                <div class="stack-card-header">
                    <div class="stack-card-name">${s.display_name}</div>
                    <div class="stack-card-badges">
                        ${s.is_system ? '<span class="badge system">SYSTEM</span>' : ''}
                        ${healthBadge}
                        <span class="badge ${statusClass}">${statusText}</span>
                    </div>
                </div>
                ${s.description ? `<div class="stack-card-desc">${s.description}</div>` : ''}
                <div class="stack-card-stats">
                    <span><i data-lucide="box" style="width: 14px; height: 14px;"></i> ${s.containers_running}/${s.containers_total}</span>
                    ${resourceStats}
                    ${restartWarning}
                </div>
                ${containerList}
                <div class="stack-card-footer">
                    <span style="font-size: 0.75rem; color: var(--archie-text-muted);">${s.name}</span>
                    <div style="display: flex; gap: 6px;">
                        <button class="docker-card-btn start" onclick="event.stopPropagation(); quickStackAction('${s.name}', 'up', ${!!s.is_system})" title="Start">
                            <i data-lucide="play" style="width: 12px; height: 12px;"></i>
                        </button>
                        <button class="docker-card-btn" onclick="event.stopPropagation(); quickStackAction('${s.name}', 'restart', ${!!s.is_system})" title="Restart">
                            <i data-lucide="refresh-cw" style="width: 12px; height: 12px;"></i>
                        </button>
                        <button class="docker-card-btn stop" onclick="event.stopPropagation(); quickStackAction('${s.name}', 'down', ${!!s.is_system})" title="Stop">
                            <i data-lucide="square" style="width: 12px; height: 12px;"></i>
                        </button>
                    </div>
                </div>
            </div>`;
        }).join('');
    }

    if (typeof lucide !== 'undefined') lucide.createIcons();
}

// Docker Compose actions (multi-stack aware)
async function dockerComposeAction(action) {
    // If in stack detail view, delegate to stack action
    if (currentStackName) {
        stackAction(action);
        return;
    }

    const actionMessages = {
        'up': 'Start all containers?',
        'down': 'Stop all containers? This will stop all running services.',
        'restart': 'Restart all containers?'
    };

    showConfirmModal('Docker Compose', actionMessages[action] || `Execute ${action}?`, async () => {
        showToast('success', `Executing docker-compose ${action}...`);
        try {
            const res = await fetch('/dashboard/api/docker/compose', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, confirm: true })
            });
            const json = await res.json();
            if (json.success) {
                showToast('success', `Docker-compose ${action} completed`);
                setTimeout(() => loadDocker(), 2000);
            } else {
                showToast('error', json.error || `Failed to ${action}`);
            }
        } catch (e) {
            showToast('error', 'Docker compose error: ' + e.message);
        }
    });
}

// View container logs
function viewDockerLogs(containerId, containerName) {
    window.open(`/dashboard/api/docker/${containerId}/logs?tail=100`, '_blank');
}

function renderNetwork(data) {
    // Cache host IP for use by other sub-tabs
    if (data.host_ip) cachedHostIp = data.host_ip;

    // Network Summary - prefer host data
    const hostname = data.host_hostname || data.hostname || '--';
    document.getElementById('net-hostname').textContent = hostname;
    document.getElementById('net-container-ip').textContent = data.primary_ip || '--';
    document.getElementById('net-gateway').textContent = data.gateway || '--';

    // DNS - prefer host DNS
    const dnsServers = data.host_dns_servers || data.dns_servers || [];
    document.getElementById('net-dns').textContent = dnsServers.join(', ') || '--';

    // Host IP
    const hostIpElem = document.getElementById('net-host-ip');
    if (data.host_ip) {
        hostIpElem.textContent = data.host_ip;
        hostIpElem.style.color = 'var(--archie-green)';
    } else if (data.host_interfaces?.length) {
        const firstHost = data.host_interfaces[0];
        hostIpElem.textContent = firstHost.ip || 'Unknown';
        hostIpElem.style.color = 'var(--archie-cyan)';
    } else {
        hostIpElem.textContent = data.is_containerized ? 'N/A (Docker)' : (data.primary_ip || '--');
        hostIpElem.style.color = 'var(--archie-orange)';
    }

    // Host Interfaces (from host monitor)
    const hostSection = document.getElementById('host-interfaces-section');
    const hostTbody = document.getElementById('host-interfaces-table');
    if (data.host_interfaces?.length) {
        hostSection.style.display = 'block';
        hostTbody.innerHTML = data.host_interfaces.map(h => {
            const isPhysical = !h.name?.startsWith('br-') && !h.name?.startsWith('docker') && !h.name?.startsWith('veth');
            return `
            <tr>
                <td>${h.name || '--'} ${isPhysical ? '<span class="host-badge">PRIMARY</span>' : ''}</td>
                <td class="mono" style="color: var(--archie-green);">${h.ip || '-'}</td>
                <td class="mono" style="font-size: 0.8rem;">${h.cidr || '-'}</td>
                <td style="color: var(--archie-text-muted); font-size: 0.8rem;">${h.note || (isPhysical ? 'Physical interface' : 'Virtual bridge')}</td>
            </tr>`;
        }).join('');
    } else {
        hostSection.style.display = 'none';
    }

    // Container Interfaces
    const netTbody = document.getElementById('network-table');
    const interfaces = data.interfaces || [];
    if (!interfaces.length) {
        netTbody.innerHTML = '<tr><td colspan="9" style="color: var(--archie-text-muted)">No interfaces found</td></tr>';
    } else {
        netTbody.innerHTML = interfaces.map(i => `
            <tr>
                <td>${i.name || '--'}</td>
                <td class="mono">${i.ip || '-'}</td>
                <td class="mono" style="font-size: 0.8rem;">${i.netmask || '-'}</td>
                <td class="mono" style="font-size: 0.8rem;">${i.mac || '-'}</td>
                <td><span class="badge ${(i.status || '').toLowerCase()}">${i.status || '--'}</span></td>
                <td>${i.speed_mbps ? i.speed_mbps + ' Mbps' : '-'}</td>
                <td>${i.mtu || '-'}</td>
                <td>${formatBytes(i.bytes_sent)}</td>
                <td>${formatBytes(i.bytes_recv)}</td>
            </tr>
        `).join('');
    }

    // Connections - prefer host data, group by stack
    const connTbody = document.getElementById('connections-table');
    const hostConns = data.host_connections || [];
    const containerConns = data.connections || [];
    const allConns = hostConns.length > 0 ? hostConns : containerConns;
    const connSource = hostConns.length > 0 ? 'host' : 'container';
    const portToStack = data.port_to_stack || {};
    const stacksSummary = data.stacks_summary || [];
    const stackColorMap = {};
    stacksSummary.forEach(s => { stackColorMap[s.name] = s.color || '#888'; });

    connTbody.textContent = '';
    if (!allConns.length) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 5;
        td.style.color = 'var(--archie-text-muted)';
        td.textContent = 'No active connections';
        tr.appendChild(td);
        connTbody.appendChild(tr);
    } else {
        // Group connections by stack
        const connsByStack = {};
        const unassignedConns = [];
        allConns.slice(0, 50).forEach(c => {
            const localPort = (c.local_addr || '').split(':').pop();
            const stackName = portToStack[localPort];
            if (stackName) {
                if (!connsByStack[stackName]) connsByStack[stackName] = [];
                connsByStack[stackName].push(c);
            } else {
                unassignedConns.push(c);
            }
        });

        function appendConnRow(tbody, c) {
            const tr = document.createElement('tr');
            [c.local_addr || '-', c.remote_addr || '-'].forEach(val => {
                const td = document.createElement('td');
                td.className = 'mono';
                td.textContent = val;
                tr.appendChild(td);
            });
            const tdStatus = document.createElement('td');
            const badge = document.createElement('span');
            badge.className = 'badge active';
            badge.textContent = c.status || '--';
            tdStatus.appendChild(badge);
            tr.appendChild(tdStatus);
            [c.process || '-', c.pid || '-'].forEach(val => {
                const td = document.createElement('td');
                td.textContent = val;
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        }

        if (Object.keys(connsByStack).length > 0) {
            // Render grouped connections
            for (const [stackName, conns] of Object.entries(connsByStack)) {
                const color = stackColorMap[stackName] || '#888';
                const headerTr = document.createElement('tr');
                const headerTd = document.createElement('td');
                headerTd.colSpan = 5;
                headerTd.style.cssText = `padding: 6px 8px; background: ${color}15;`;
                const dot = document.createElement('span');
                dot.style.cssText = `display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${color}; margin-right: 8px;`;
                headerTd.appendChild(dot);
                const label = document.createElement('span');
                label.style.cssText = `color: ${color}; font-weight: 600; font-size: 0.85rem;`;
                label.textContent = `${stackName} — ${conns.length} connection${conns.length !== 1 ? 's' : ''}`;
                headerTd.appendChild(label);
                headerTr.appendChild(headerTd);
                connTbody.appendChild(headerTr);
                conns.forEach(c => appendConnRow(connTbody, c));
            }
            if (unassignedConns.length) {
                const headerTr = document.createElement('tr');
                const headerTd = document.createElement('td');
                headerTd.colSpan = 5;
                headerTd.style.cssText = 'padding: 6px 8px; background: var(--archie-bg-tertiary);';
                const label = document.createElement('span');
                label.style.cssText = 'color: var(--archie-text-muted); font-weight: 600; font-size: 0.85rem;';
                label.textContent = `Unassigned — ${unassignedConns.length} connection${unassignedConns.length !== 1 ? 's' : ''}`;
                headerTd.appendChild(label);
                headerTr.appendChild(headerTd);
                connTbody.appendChild(headerTr);
                unassignedConns.forEach(c => appendConnRow(connTbody, c));
            }
        } else {
            // Fallback: flat list (no stack data)
            if (connSource === 'host') {
                const headerTr = document.createElement('tr');
                const headerTd = document.createElement('td');
                headerTd.colSpan = 5;
                headerTd.style.padding = '4px 8px';
                const badge = document.createElement('span');
                badge.className = 'host-badge';
                badge.textContent = 'HOST';
                headerTd.appendChild(badge);
                headerTr.appendChild(headerTd);
                connTbody.appendChild(headerTr);
            }
            allConns.slice(0, 30).forEach(c => appendConnRow(connTbody, c));
        }
    }

    // Listening Ports - prefer host data, group by stack
    const portsTbody = document.getElementById('listening-ports-table');
    const hostPorts = data.host_listening_ports || [];
    const containerPorts = data.listening_ports || [];
    const allPorts = hostPorts.length > 0 ? hostPorts : containerPorts;
    const portSource = hostPorts.length > 0 ? 'host' : 'container';

    portsTbody.textContent = '';
    if (!allPorts.length) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 5;
        td.style.color = 'var(--archie-text-muted)';
        td.textContent = 'No listening ports found';
        tr.appendChild(td);
        portsTbody.appendChild(tr);
    } else {
        // Deduplicate by port number
        const seenPorts = new Set();
        const uniquePorts = allPorts.filter(p => {
            const key = `${p.port}:${p.ip}`;
            if (seenPorts.has(key)) return false;
            seenPorts.add(key);
            return true;
        });

        function appendPortRow(tbody, p) {
            const isCommonPort = [22, 80, 443, 3000, 5432, 5678, 5679, 8080, 11434].includes(p.port);
            const tr = document.createElement('tr');
            const tdPort = document.createElement('td');
            tdPort.className = 'mono';
            tdPort.style.color = isCommonPort ? 'var(--archie-green)' : 'var(--archie-text-primary)';
            tdPort.style.fontWeight = isCommonPort ? 'bold' : 'normal';
            tdPort.textContent = p.port;
            tr.appendChild(tdPort);

            const tdProto = document.createElement('td');
            tdProto.textContent = (p.protocol || 'tcp').toUpperCase();
            tr.appendChild(tdProto);

            const tdBind = document.createElement('td');
            tdBind.className = 'mono';
            tdBind.style.fontSize = '0.85rem';
            tdBind.textContent = p.ip || '0.0.0.0';
            tr.appendChild(tdBind);

            const tdProc = document.createElement('td');
            tdProc.textContent = p.process || '-';
            tr.appendChild(tdProc);

            const tdPid = document.createElement('td');
            tdPid.textContent = p.pid || '-';
            tr.appendChild(tdPid);

            tbody.appendChild(tr);
        }

        // Group ports by stack
        const portsByStack = {};
        const unassignedPorts = [];
        uniquePorts.forEach(p => {
            const stackName = portToStack[String(p.port)];
            if (stackName) {
                if (!portsByStack[stackName]) portsByStack[stackName] = [];
                portsByStack[stackName].push(p);
            } else {
                unassignedPorts.push(p);
            }
        });

        if (Object.keys(portsByStack).length > 0) {
            for (const [stackName, ports] of Object.entries(portsByStack)) {
                const color = stackColorMap[stackName] || '#888';
                const headerTr = document.createElement('tr');
                const headerTd = document.createElement('td');
                headerTd.colSpan = 5;
                headerTd.style.cssText = `padding: 6px 8px; background: ${color}15;`;
                const dot = document.createElement('span');
                dot.style.cssText = `display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${color}; margin-right: 8px;`;
                headerTd.appendChild(dot);
                const label = document.createElement('span');
                label.style.cssText = `color: ${color}; font-weight: 600; font-size: 0.85rem;`;
                label.textContent = `${stackName} — ${ports.length} port${ports.length !== 1 ? 's' : ''}`;
                headerTd.appendChild(label);
                headerTr.appendChild(headerTd);
                portsTbody.appendChild(headerTr);
                ports.forEach(p => appendPortRow(portsTbody, p));
            }
            if (unassignedPorts.length) {
                const headerTr = document.createElement('tr');
                const headerTd = document.createElement('td');
                headerTd.colSpan = 5;
                headerTd.style.cssText = 'padding: 6px 8px; background: var(--archie-bg-tertiary);';
                const label = document.createElement('span');
                label.style.cssText = 'color: var(--archie-text-muted); font-weight: 600; font-size: 0.85rem;';
                label.textContent = `Unassigned — ${unassignedPorts.length} port${unassignedPorts.length !== 1 ? 's' : ''}`;
                headerTd.appendChild(label);
                headerTr.appendChild(headerTd);
                portsTbody.appendChild(headerTr);
                unassignedPorts.forEach(p => appendPortRow(portsTbody, p));
            }
        } else {
            // Fallback: flat list
            if (portSource === 'host') {
                const headerTr = document.createElement('tr');
                const headerTd = document.createElement('td');
                headerTd.colSpan = 5;
                headerTd.style.padding = '4px 8px';
                const badge = document.createElement('span');
                badge.className = 'host-badge';
                badge.textContent = 'HOST';
                headerTd.appendChild(badge);
                headerTr.appendChild(headerTd);
                portsTbody.appendChild(headerTr);
            }
            uniquePorts.forEach(p => appendPortRow(portsTbody, p));
        }
    }

    // Bandwidth Summary
    document.getElementById('bandwidth-sent').textContent = formatBytes(data.total_bytes_sent || 0);
    document.getElementById('bandwidth-recv').textContent = formatBytes(data.total_bytes_recv || 0);
}

// ===== Network Sub-tab Functions =====
let portMapLoaded = false;
let topologyLoaded = false;
let bandwidthLoaded = false;
let cachedHostIp = '192.168.1.200'; // Updated by renderNetwork from API data

function showNetSubtab(tabName) {
    // Toggle button active state
    document.querySelectorAll('.net-subtab').forEach(btn => btn.classList.remove('active'));
    const btn = document.getElementById('net-subtab-btn-' + tabName);
    if (btn) btn.classList.add('active');

    // Toggle content
    document.querySelectorAll('.net-subtab-content').forEach(c => c.classList.remove('active'));
    const content = document.getElementById('net-subtab-' + tabName);
    if (content) content.classList.add('active');

    // Lazy-load sub-tab data on first visit
    if (tabName === 'portmap') {
        if (!portMapLoaded) loadPortMap();
        loadPortUsage(); // Always refresh port usage when viewing
    }
    if (tabName === 'topology' && !topologyLoaded) loadTopology();
    if (tabName === 'bandwidth' && !bandwidthLoaded) loadBandwidthData();

    lucide.createIcons();
}

// Common web UI ports for auto-detection
const COMMON_WEB_PORTS = {
    80: 'HTTP', 443: 'HTTPS', 3000: 'Web App', 8080: 'Web App', 8443: 'HTTPS',
    8123: 'Home Assistant', 9000: 'Portainer', 32400: 'Plex',
    8096: 'Jellyfin', 8989: 'Sonarr', 7878: 'Radarr', 9696: 'Prowlarr',
    5678: 'n8n', 8081: 'Web App', 3001: 'Web App'
};

async function loadPortMap() {
    // Show cached data immediately if available
    if (window.cachedPortMapData) {
        renderPortMap(window.cachedPortMapData);
    }

    // Fetch fresh data
    try {
        const res = await fetch('/dashboard/api/network/port-map');
        const json = await res.json();
        if (json.success) {
            window.cachedPortMapData = json;
            saveToCache(CACHE_KEYS.portMap, json);
            renderPortMap(json);
            portMapLoaded = true;
        } else {
            if (!window.cachedPortMapData) {
                document.getElementById('port-map-container').textContent = 'Error: ' + (json.error || 'Unknown error');
            }
        }
    } catch (e) {
        console.error('Port map error:', e);
        if (!window.cachedPortMapData) {
            document.getElementById('port-map-container').textContent = 'Failed to load port map';
        }
    }
}

function renderPortMap(data) {
    // NOTE: All data comes from admin-only internal API endpoints (trusted server data).
    // This follows the same DOM rendering pattern used throughout this template.
    const container = document.getElementById('port-map-container');
    const stacks = data.stacks || [];
    const unassigned = data.unassigned_ports || [];
    const hostIp = cachedHostIp;

    // Build DOM elements instead of innerHTML for port map
    container.textContent = '';

    // Render each stack group
    stacks.forEach(stack => {
        if (!stack.ports.length) return;
        const color = stack.color || '#888888';
        const webUiPorts = stack.web_ui_ports || [];
        const webUiMap = {};
        webUiPorts.forEach(w => { webUiMap[w.port] = w; });

        const group = document.createElement('div');
        group.className = 'port-stack-group';

        const header = document.createElement('div');
        header.className = 'port-stack-header';
        header.style.cssText = `background: ${color}22; border-bottom: 1px solid ${color}44;`;

        const dot = document.createElement('span');
        dot.className = 'color-dot';
        dot.style.background = color;
        header.appendChild(dot);

        const nameSpan = document.createElement('span');
        nameSpan.style.color = color;
        nameSpan.textContent = stack.name;
        header.appendChild(nameSpan);

        if (stack.is_system) {
            const badge = document.createElement('span');
            badge.className = 'host-badge';
            badge.textContent = 'SYSTEM';
            header.appendChild(badge);
        }

        const countSpan = document.createElement('span');
        countSpan.className = 'port-count';
        countSpan.textContent = `${stack.ports.length} port${stack.ports.length !== 1 ? 's' : ''}`;
        header.appendChild(countSpan);

        group.appendChild(header);

        const table = document.createElement('table');
        table.className = 'data-table data-table-compact';
        table.style.margin = '0';
        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        ['Port', 'Service', 'Status', 'Web UI'].forEach(t => {
            const th = document.createElement('th');
            th.textContent = t;
            headRow.appendChild(th);
        });
        thead.appendChild(headRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        stack.ports.forEach(p => {
            const tr = document.createElement('tr');

            const tdPort = document.createElement('td');
            tdPort.className = 'mono';
            tdPort.style.cssText = 'color: var(--archie-green); font-weight: bold;';
            // Make port clickable if it's open
            if (p.open) {
                const portLink = document.createElement('a');
                portLink.href = `http://${hostIp}:${p.port}/`;
                portLink.target = '_blank';
                portLink.style.cssText = 'color: var(--archie-green); text-decoration: none; display: inline-flex; align-items: center; gap: 4px;';
                portLink.textContent = p.port;
                const icon = document.createElement('i');
                icon.setAttribute('data-lucide', 'external-link');
                icon.style.cssText = 'width: 12px; height: 12px; opacity: 0.6;';
                portLink.appendChild(icon);
                portLink.title = `Open http://${hostIp}:${p.port}/`;
                tdPort.appendChild(portLink);
            } else {
                tdPort.textContent = p.port;
            }
            tr.appendChild(tdPort);

            const tdSvc = document.createElement('td');
            tdSvc.textContent = p.service || '-';
            tr.appendChild(tdSvc);

            const tdStatus = document.createElement('td');
            const badge = document.createElement('span');
            badge.className = 'badge' + (p.open ? ' active' : '');
            badge.style.fontSize = '0.75rem';
            if (!p.open) badge.style.opacity = '0.5';
            badge.textContent = p.open ? 'OPEN' : 'CLOSED';
            tdStatus.appendChild(badge);
            tr.appendChild(tdStatus);

            const tdWeb = document.createElement('td');
            const webEntry = webUiMap[p.port];
            const autoLabel = COMMON_WEB_PORTS[p.port];
            if (webEntry || autoLabel) {
                const a = document.createElement('a');
                const path = webEntry ? (webEntry.path || '/') : '/';
                const label = webEntry ? (webEntry.label || 'Open') : autoLabel;
                a.href = `http://${hostIp}:${p.port}${path}`;
                a.target = '_blank';
                a.className = 'web-ui-link';
                a.title = a.href;
                a.textContent = label;
                tdWeb.appendChild(a);
            } else {
                tdWeb.style.color = 'var(--archie-text-muted)';
                tdWeb.textContent = '-';
            }
            tr.appendChild(tdWeb);

            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        group.appendChild(table);
        container.appendChild(group);
    });

    // Unassigned ports
    if (unassigned.length) {
        const group = document.createElement('div');
        group.className = 'port-stack-group';

        const header = document.createElement('div');
        header.className = 'port-stack-header';
        header.style.cssText = 'background: var(--archie-bg-tertiary); border-bottom: 1px solid var(--archie-border-default);';

        const dot = document.createElement('span');
        dot.className = 'color-dot';
        dot.style.background = '#888';
        header.appendChild(dot);

        const nameSpan = document.createElement('span');
        nameSpan.style.color = 'var(--archie-text-muted)';
        nameSpan.textContent = 'Unassigned (Host Services)';
        header.appendChild(nameSpan);

        const countSpan = document.createElement('span');
        countSpan.className = 'port-count';
        countSpan.textContent = `${unassigned.length} port${unassigned.length !== 1 ? 's' : ''}`;
        header.appendChild(countSpan);

        group.appendChild(header);

        const table = document.createElement('table');
        table.className = 'data-table data-table-compact';
        table.style.margin = '0';
        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        ['Port', 'Process', 'Bind Address', 'Web UI'].forEach(t => {
            const th = document.createElement('th');
            th.textContent = t;
            headRow.appendChild(th);
        });
        thead.appendChild(headRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        unassigned.forEach(p => {
            const tr = document.createElement('tr');

            const tdPort = document.createElement('td');
            tdPort.className = 'mono';
            tdPort.style.fontWeight = 'bold';
            // Make port clickable
            const portLink = document.createElement('a');
            portLink.href = `http://${hostIp}:${p.port}/`;
            portLink.target = '_blank';
            portLink.style.cssText = 'color: var(--archie-green); text-decoration: none; display: inline-flex; align-items: center; gap: 4px;';
            portLink.textContent = p.port;
            const icon = document.createElement('i');
            icon.setAttribute('data-lucide', 'external-link');
            icon.style.cssText = 'width: 12px; height: 12px; opacity: 0.6;';
            portLink.appendChild(icon);
            portLink.title = `Open http://${hostIp}:${p.port}/`;
            tdPort.appendChild(portLink);
            tr.appendChild(tdPort);

            const tdProc = document.createElement('td');
            tdProc.textContent = p.process || '-';
            tr.appendChild(tdProc);

            const tdBind = document.createElement('td');
            tdBind.className = 'mono';
            tdBind.style.fontSize = '0.85rem';
            tdBind.textContent = p.ip || '0.0.0.0';
            tr.appendChild(tdBind);

            const tdWeb = document.createElement('td');
            if (COMMON_WEB_PORTS[p.port]) {
                const a = document.createElement('a');
                a.href = `http://${hostIp}:${p.port}/`;
                a.target = '_blank';
                a.className = 'web-ui-link';
                a.title = a.href;
                a.textContent = COMMON_WEB_PORTS[p.port];
                tdWeb.appendChild(a);
            } else {
                tdWeb.style.color = 'var(--archie-text-muted)';
                tdWeb.textContent = '-';
            }
            tr.appendChild(tdWeb);

            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        group.appendChild(table);
        container.appendChild(group);
    }

    if (!container.children.length) {
        container.textContent = 'No port data available';
        container.style.cssText = 'color: var(--archie-text-muted); padding: 20px; text-align: center;';
    }

    lucide.createIcons();
}

// ===== Topology Map =====
async function loadTopology() {
    try {
        const [netRes, portRes] = await Promise.all([
            fetch('/dashboard/api/network'),
            fetch('/dashboard/api/network/port-map')
        ]);
        const netJson = await netRes.json();
        const portJson = await portRes.json();
        if (netJson.success && portJson.success) {
            renderTopology(netJson.data || {}, portJson);
            topologyLoaded = true;
        } else {
            document.getElementById('topology-svg-container').textContent = 'Failed to load topology data';
        }
    } catch (e) {
        console.error('Topology error:', e);
        document.getElementById('topology-svg-container').textContent = 'Failed to load topology';
    }
}

function renderTopology(netData, portData) {
    const container = document.getElementById('topology-svg-container');
    const stacks = (portData.stacks || []).filter(s => s.ports.length > 0);
    const unassigned = portData.unassigned_ports || [];

    const gateway = netData.gateway || '192.168.1.1';
    const hostIp = netData.host_ip || '192.168.1.200';
    const hostname = netData.host_hostname || netData.hostname || 'archie';

    // Layout dimensions
    const nodeW = 140, nodeH = 60;
    const colGap = 60;
    const rowGap = 16;
    const totalNodes = stacks.length + (unassigned.length ? 1 : 0);
    const rightColHeight = Math.max(1, totalNodes) * (nodeH + rowGap) - rowGap;
    const svgH = Math.max(280, rightColHeight + 80);
    const svgW = 5 * (nodeW + colGap) + 20;

    // Node positions (left-to-right: Internet, Router, Host, Bridge, Stacks)
    const cols = [20, nodeW + colGap + 20, 2*(nodeW + colGap) + 20, 3*(nodeW + colGap) + 20, 4*(nodeW + colGap) + 20];
    const midY = svgH / 2 - nodeH / 2;

    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + svgW + ' ' + svgH);
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', svgH);
    svg.style.maxWidth = svgW + 'px';

    // Helper to draw rounded rect node
    function drawNode(x, y, label, sublabel, color, textColor) {
        const g = document.createElementNS(ns, 'g');
        const rect = document.createElementNS(ns, 'rect');
        rect.setAttribute('x', x);
        rect.setAttribute('y', y);
        rect.setAttribute('width', nodeW);
        rect.setAttribute('height', nodeH);
        rect.setAttribute('rx', 8);
        rect.setAttribute('fill', color + '22');
        rect.setAttribute('stroke', color);
        rect.setAttribute('stroke-width', '1.5');
        g.appendChild(rect);

        const text = document.createElementNS(ns, 'text');
        text.setAttribute('x', x + nodeW / 2);
        text.setAttribute('y', y + 24);
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('fill', textColor || color);
        text.setAttribute('font-size', '12');
        text.setAttribute('font-weight', '600');
        text.textContent = label;
        g.appendChild(text);

        if (sublabel) {
            const sub = document.createElementNS(ns, 'text');
            sub.setAttribute('x', x + nodeW / 2);
            sub.setAttribute('y', y + 42);
            sub.setAttribute('text-anchor', 'middle');
            sub.setAttribute('fill', textColor || color);
            sub.setAttribute('font-size', '10');
            sub.setAttribute('opacity', '0.7');
            sub.textContent = sublabel;
            g.appendChild(sub);
        }
        svg.appendChild(g);
        return { cx: x + nodeW, cy: y + nodeH / 2, lx: x, ly: y + nodeH / 2 };
    }

    // Helper to draw line
    function drawLine(x1, y1, x2, y2, color, dashed) {
        const line = document.createElementNS(ns, 'line');
        line.setAttribute('x1', x1);
        line.setAttribute('y1', y1);
        line.setAttribute('x2', x2);
        line.setAttribute('y2', y2);
        line.setAttribute('stroke', color);
        line.setAttribute('stroke-width', '1.5');
        if (dashed) line.setAttribute('stroke-dasharray', '4,4');
        svg.appendChild(line);
    }

    // Draw fixed nodes
    const inet = drawNode(cols[0], midY, 'Internet', 'WAN', '#e74c3c', '#e74c3c');
    const router = drawNode(cols[1], midY, 'Router', gateway, '#f39c12', '#f39c12');
    const host = drawNode(cols[2], midY, hostname, hostIp, '#00D4AA', '#00D4AA');
    const bridge = drawNode(cols[3], midY, 'Docker Bridge', '172.17.0.1', '#3498db', '#3498db');

    // Lines between fixed nodes
    drawLine(inet.cx, inet.cy, router.lx, router.ly, '#e74c3c88');
    drawLine(router.cx, router.cy, host.lx, host.ly, '#f39c1288');
    drawLine(host.cx, host.cy, bridge.lx, bridge.ly, '#3498db88');

    // Right column: stack nodes
    const rightStartY = Math.max(20, midY - (rightColHeight / 2) + nodeH / 2);

    stacks.forEach(function(stack, i) {
        const y = rightStartY + i * (nodeH + rowGap);
        const color = stack.color || '#888';
        const portList = stack.ports.map(function(p) { return ':' + p.port; }).join(', ');
        const label = stack.name.length > 14 ? stack.name.substring(0, 12) + '...' : stack.name;
        const sublabel = portList.length > 20 ? portList.substring(0, 18) + '...' : portList;
        const sNode = drawNode(cols[4], y, label, sublabel, color, color);
        drawLine(bridge.cx, bridge.cy, sNode.lx, sNode.ly, color + '66');
    });

    // Unassigned host services node
    if (unassigned.length) {
        const y = rightStartY + stacks.length * (nodeH + rowGap);
        const portList = unassigned.slice(0, 5).map(function(p) { return ':' + p.port; }).join(', ');
        const sublabel = portList + (unassigned.length > 5 ? '...' : '');
        const uNode = drawNode(cols[4], y, 'Host Services', sublabel, '#888', '#888');
        drawLine(host.cx, host.cy, uNode.lx, uNode.ly, '#88888866', true);
    }

    container.textContent = '';
    container.appendChild(svg);
}

// ===== Bandwidth Monitor =====
async function loadBandwidthData() {
    // Show cached data immediately if available
    if (window.cachedBandwidthData) {
        renderBandwidthMonitor(window.cachedBandwidthData);
    }

    // Fetch fresh data
    try {
        const res = await fetch('/dashboard/api/network/bandwidth');
        const json = await res.json();
        if (json.success) {
            window.cachedBandwidthData = json;
            saveToCache(CACHE_KEYS.bandwidth, json);
            renderBandwidthMonitor(json);
            bandwidthLoaded = true;
        } else {
            if (!window.cachedBandwidthData) {
                document.getElementById('bandwidth-monitor-container').textContent = 'Error: ' + (json.error || 'Unknown');
            }
        }
    } catch (e) {
        console.error('Bandwidth error:', e);
        if (!window.cachedBandwidthData) {
            document.getElementById('bandwidth-monitor-container').textContent = 'Failed to load bandwidth data';
        }
    }
}

function formatRate(bps) {
    if (bps == null || bps === 0) return '0 B/s';
    const units = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
    let val = bps;
    let idx = 0;
    while (val >= 1024 && idx < units.length - 1) { val /= 1024; idx++; }
    return val.toFixed(idx > 0 ? 1 : 0) + ' ' + units[idx];
}

// Alias for chart tooltip callback
function formatBandwidthRate(bps) {
    return formatRate(bps);
}

// Format packet count with K/M/B suffixes
function formatPacketCount(count) {
    if (count == null || count === 0) return '0';
    if (count < 1000) return count.toString();
    if (count < 1000000) return (count / 1000).toFixed(1) + 'K';
    if (count < 1000000000) return (count / 1000000).toFixed(2) + 'M';
    return (count / 1000000000).toFixed(2) + 'B';
}

function renderBandwidthMonitor(data) {
    const container = document.getElementById('bandwidth-monitor-container');
    const interfaces = (data.current && data.current.interfaces) || [];
    const containers = (data.current && data.current.containers) || [];
    const samples = data.samples || [];
    const dataAge = data.current ? data.current.data_age : null;

    container.textContent = '';

    if (!interfaces.length && !containers.length) {
        container.textContent = 'No bandwidth data available yet. Data will appear after host monitor collects samples.';
        container.style.cssText = 'color: var(--archie-text-muted); padding: 20px; text-align: center;';
        return;
    }

    // Get latest rates from samples
    const latestRates = samples.length ? (samples[samples.length - 1].rates || {}) : {};

    // Find the primary physical interface (eno1 typically)
    const physicalIfaces = interfaces.filter(function(i) {
        return !i.name.startsWith('br-') && !i.name.startsWith('docker') &&
               !i.name.startsWith('veth') && i.name !== 'lo';
    });
    const primaryIface = physicalIfaces.length > 0 ? physicalIfaces[0].name : 'eno1';

    // Update total traffic stats from cumulative counters
    if (samples.length > 0) {
        const latestSample = samples[samples.length - 1];
        const counters = latestSample.counters || {};
        const ifaceCounters = counters[primaryIface] || {};

        // Total bytes
        const totalRx = ifaceCounters.rx_bytes || 0;
        const totalTx = ifaceCounters.tx_bytes || 0;
        const totalRxPkts = ifaceCounters.rx_packets || 0;
        const totalTxPkts = ifaceCounters.tx_packets || 0;

        document.getElementById('total-rx-bytes').textContent = formatBytes(totalRx);
        document.getElementById('total-rx-packets').textContent = formatPacketCount(totalRxPkts) + ' packets';
        document.getElementById('total-tx-bytes').textContent = formatBytes(totalTx);
        document.getElementById('total-tx-packets').textContent = formatPacketCount(totalTxPkts) + ' packets';
        document.getElementById('total-traffic').textContent = formatBytes(totalRx + totalTx);

        // Current combined rate
        const sampleRates = latestSample.rates || {};
        const currentRates = sampleRates[primaryIface] || {};
        const combinedRate = (currentRates.rx_bps || 0) + (currentRates.tx_bps || 0);
        document.getElementById('current-rate').textContent = formatRate(combinedRate);
    }

    // Update bandwidth history chart
    if (bandwidthChart && samples.length > 1) {
        // Extract RX and TX rates from samples for primary interface
        const labels = [];
        const rxData = [];
        const txData = [];

        samples.forEach(function(sample, idx) {
            // Convert timestamp to time string
            const ts = sample.timestamp;
            const d = new Date(ts * 1000);
            const timeStr = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
            labels.push(timeStr);

            // Get rates for primary interface
            const rates = sample.rates || {};
            const ifaceRates = rates[primaryIface] || {};
            rxData.push(ifaceRates.rx_bps || 0);
            txData.push(ifaceRates.tx_bps || 0);
        });

        // Update chart
        bandwidthChart.data.labels = labels;
        bandwidthChart.data.datasets[0].data = rxData;
        bandwidthChart.data.datasets[1].data = txData;
        bandwidthChart.update('none');

        // Update chart info
        const chartInfo = document.getElementById('bandwidth-chart-info');
        if (chartInfo) {
            chartInfo.textContent = primaryIface + ' \u00B7 ' + samples.length + ' samples';
        }
    }

    // Age indicator
    if (dataAge != null) {
        const ageDiv = document.createElement('div');
        ageDiv.style.cssText = 'font-size: 0.75rem; color: var(--archie-text-muted); margin-bottom: 12px;';
        ageDiv.textContent = 'Data age: ' + dataAge + 's \u00B7 ' + samples.length + ' sample' + (samples.length !== 1 ? 's' : '') + ' in history';
        container.appendChild(ageDiv);
    }

    // Per-interface table
    if (interfaces.length) {
        const title = document.createElement('h4');
        title.style.cssText = 'color: var(--archie-cyan); margin-bottom: 10px; font-size: 0.9rem;';
        title.textContent = 'Interface Bandwidth';
        container.appendChild(title);

        const table = document.createElement('table');
        table.className = 'data-table data-table-compact';
        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        ['Interface', 'RX Total', 'TX Total', 'RX Rate', 'TX Rate', 'Trend'].forEach(function(t) {
            const th = document.createElement('th');
            th.textContent = t;
            headRow.appendChild(th);
        });
        thead.appendChild(headRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        // Filter out loopback for cleaner display
        const displayIfaces = interfaces.filter(function(i) { return i.name !== 'lo'; });
        displayIfaces.forEach(function(iface) {
            const tr = document.createElement('tr');
            const isPhysical = !iface.name.startsWith('br-') && !iface.name.startsWith('docker') && !iface.name.startsWith('veth');

            const tdName = document.createElement('td');
            tdName.textContent = iface.name;
            if (isPhysical) {
                tdName.style.fontWeight = 'bold';
                const badge = document.createElement('span');
                badge.className = 'host-badge';
                badge.textContent = 'PHY';
                badge.style.marginLeft = '6px';
                tdName.appendChild(badge);
            }
            tr.appendChild(tdName);

            const tdRx = document.createElement('td');
            tdRx.className = 'mono bandwidth-rate';
            tdRx.textContent = formatBytes(iface.rx_bytes);
            tr.appendChild(tdRx);

            const tdTx = document.createElement('td');
            tdTx.className = 'mono bandwidth-rate';
            tdTx.textContent = formatBytes(iface.tx_bytes);
            tr.appendChild(tdTx);

            const rate = latestRates[iface.name];
            const tdRxRate = document.createElement('td');
            tdRxRate.className = 'mono bandwidth-rate';
            tdRxRate.style.color = 'var(--archie-green)';
            tdRxRate.textContent = rate ? formatRate(rate.rx_bps) : '-';
            tr.appendChild(tdRxRate);

            const tdTxRate = document.createElement('td');
            tdTxRate.className = 'mono bandwidth-rate';
            tdTxRate.style.color = 'var(--archie-cyan)';
            tdTxRate.textContent = rate ? formatRate(rate.tx_bps) : '-';
            tr.appendChild(tdTxRate);

            // Sparkline cell
            const tdSparkline = document.createElement('td');
            const sparkDiv = document.createElement('div');
            sparkDiv.className = 'sparkline-cell';
            const canvas = document.createElement('canvas');
            canvas.width = 80;
            canvas.height = 24;
            sparkDiv.appendChild(canvas);
            tdSparkline.appendChild(sparkDiv);
            tr.appendChild(tdSparkline);

            // Draw sparkline from historical data
            if (samples.length > 1) {
                const rxData = samples.map(function(s) {
                    return (s.rates && s.rates[iface.name]) ? s.rates[iface.name].rx_bps : 0;
                });
                drawSparkline(canvas.getContext('2d'), rxData, 80, 24, '#00D4AA');
            }

            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        container.appendChild(table);
    }

    // Per-container table
    if (containers.length) {
        const spacer = document.createElement('div');
        spacer.style.marginTop = '16px';
        container.appendChild(spacer);

        const title = document.createElement('h4');
        title.style.cssText = 'color: var(--archie-orange); margin-bottom: 10px; font-size: 0.9rem;';
        title.textContent = 'Container Network I/O';
        container.appendChild(title);

        const table = document.createElement('table');
        table.className = 'data-table data-table-compact';
        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        ['Container', 'RX', 'TX', 'Raw'].forEach(function(t) {
            const th = document.createElement('th');
            th.textContent = t;
            headRow.appendChild(th);
        });
        thead.appendChild(headRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        containers.forEach(function(c) {
            const tr = document.createElement('tr');

            const tdName = document.createElement('td');
            tdName.style.fontWeight = '600';
            tdName.textContent = c.name;
            tr.appendChild(tdName);

            const tdRx = document.createElement('td');
            tdRx.className = 'mono bandwidth-rate';
            tdRx.style.color = 'var(--archie-green)';
            tdRx.textContent = formatBytes(c.rx_bytes);
            tr.appendChild(tdRx);

            const tdTx = document.createElement('td');
            tdTx.className = 'mono bandwidth-rate';
            tdTx.style.color = 'var(--archie-cyan)';
            tdTx.textContent = formatBytes(c.tx_bytes);
            tr.appendChild(tdTx);

            const tdRaw = document.createElement('td');
            tdRaw.style.cssText = 'font-size: 0.8rem; color: var(--archie-text-muted);';
            tdRaw.textContent = c.net_io_raw || '-';
            tr.appendChild(tdRaw);

            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        container.appendChild(table);
    }
}

function drawSparkline(ctx, data, width, height, color) {
    if (!data || data.length < 2) return;
    const max = Math.max.apply(null, data.concat([1]));
    const step = width / (data.length - 1);

    ctx.clearRect(0, 0, width, height);
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;

    data.forEach(function(val, i) {
        const x = i * step;
        const y = height - (val / max) * (height - 4) - 2;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Fill under line
    ctx.lineTo(width, height);
    ctx.lineTo(0, height);
    ctx.closePath();
    ctx.fillStyle = color + '15';
    ctx.fill();
}

// Action Functions
function killProcess(pid, name) {
    showConfirmModal('Kill Process', `Kill process "${name}" (PID: ${pid})?`, async () => {
        const res = await fetch(`/dashboard/api/process/${pid}/kill`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm: true, signal: 'SIGTERM' })
        });
        const json = await res.json();
        showToast(json.success ? 'success' : 'error', json.message || json.error || 'Action completed');
        if (json.success) loadProcesses();
    });
}

// Kill a host process via command queue (requires sudo)
function killHostProcess(pid, name) {
    showConfirmModal('Kill Host Process', `Kill host process "${name}" (PID: ${pid})?\n\nThis requires sudo privileges.`, async () => {
        const command = `kill -TERM ${pid}`;
        const description = `Kill process ${name} (PID: ${pid})`;
        await submitHostCommand(command, description, true);
        // Refresh after a short delay for host monitor to update
        setTimeout(() => loadProcesses(), 3000);
    });
}

// Stop a Docker container by name
function stopDockerContainer(containerName) {
    const displayName = containerName.startsWith('archie_') ? 'A.R.C.H.I.E.' : containerName;
    showConfirmModal('Stop Docker Container', `Stop container "${displayName}" (${containerName})?`, async () => {
        const command = `docker stop ${containerName}`;
        const description = `Stop container ${containerName}`;
        await submitHostCommand(command, description, true);
        // Refresh processes and docker info
        setTimeout(() => {
            loadProcesses();
            loadDocker();
        }, 3000);
    });
}

// Restart a Docker container by name
function restartDockerContainer(containerName) {
    const displayName = containerName.startsWith('archie_') ? 'A.R.C.H.I.E.' : containerName;
    showConfirmModal('Restart Docker Container', `Restart container "${displayName}" (${containerName})?`, async () => {
        const command = `docker restart ${containerName}`;
        const description = `Restart container ${containerName}`;
        await submitHostCommand(command, description, true);
        // Refresh processes and docker info
        setTimeout(() => {
            loadProcesses();
            loadDocker();
        }, 5000);
    });
}

function serviceAction(name, action) {
    showConfirmModal(`${action.charAt(0).toUpperCase() + action.slice(1)} Service`, `${action} service "${name}"?`, async () => {
        const res = await fetch(`/dashboard/api/service/${name}/action`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm: true, action })
        });
        const json = await res.json();
        showToast(json.success ? 'success' : 'error', json.message || json.error || 'Action completed');
        if (json.success) loadServices();
    });
}

function dockerAction(id, action) {
    showConfirmModal(`${action.charAt(0).toUpperCase() + action.slice(1)} Container`, `${action} container "${id.substring(0, 12)}"?`, async () => {
        const res = await fetch(`/dashboard/api/docker/${id}/action`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm: true, action })
        });
        const json = await res.json();
        showToast(json.success ? 'success' : 'error', json.message || json.error || 'Action completed');
        if (json.success) loadDocker();
    });
}

function mountDisk(device) {
    // Use the themed mount modal instead of browser prompt
    openMountModal(device);
}

// Modal & Toast
function showConfirmModal(title, message, onConfirm) {
    document.getElementById('confirmTitle').textContent = title;
    const msgEl = document.getElementById('confirmMessage');
    msgEl.textContent = message;
    msgEl.style.whiteSpace = 'pre-line';  // Preserve newlines in text content
    document.getElementById('confirmModal').classList.add('active');
    pendingAction = onConfirm;
    document.getElementById('confirmBtn').onclick = async () => {
        // Save action before closing modal (closeConfirmModal clears pendingAction)
        var actionToExecute = pendingAction;
        closeConfirmModal();
        if (actionToExecute) await actionToExecute();
    };
}

function closeConfirmModal() {
    document.getElementById('confirmModal').classList.remove('active');
    pendingAction = null;
}

// Resize Filesystem Function
function resizeFilesystem(device, fstype, mountpoint) {
    if (!device) {
        showToast('error', 'No device specified');
        return;
    }

    const fstypeDisplay = fstype || 'filesystem';
    const displayDevice = device.length > 40 ? device.substring(0, 40) + '...' : device;

    // Run pre-flight check first
    runPreflightCheck(device, mountpoint || '', 'resize', function() {
        showConfirmModal('Resize Filesystem',
            'Resize the ' + fstypeDisplay + ' on ' + displayDevice + ' to use all available space? This is useful when the underlying device was extended but the filesystem resize failed.',
            function() {
                // Use submitHostCommand for proper tracking
                submitHostCommand('filesystem_resize', {
                    device: device
                }, {
                    label: 'Resize ' + displayDevice,
                    successCallback: function() { loadStorageData(); loadOverview(); }
                });
            }
        );
    });
}

// LVM Extend Modal Functions
let currentExtendLv = null;

// Helper to find mountpoint for an LV by searching partition data
function findMountpointForLv(vgName, lvName) {
    // Search partitionDataMap for matching LV
    if (window.partitionDataMap) {
        for (const key in window.partitionDataMap) {
            const part = window.partitionDataMap[key];
            if (part && part.vg_name === vgName && part.lv_name === lvName && part.mountpoint) {
                return part.mountpoint;
            }
        }
    }
    // Also check cached storage data
    if (storageData && storageData.disks) {
        for (const disk of storageData.disks) {
            if (disk.partitions) {
                for (const part of disk.partitions) {
                    if (part.vg_name === vgName && part.lv_name === lvName && part.mountpoint) {
                        return part.mountpoint;
                    }
                }
            }
        }
    }
    return '';
}

// System partition confirmation helpers - use themed modal instead of native confirm()
function confirmSystemExtend(lvName, vgName, lvSize, vgFree, mountpoint) {
    showConfirmModal(
        '⚠️ System Volume Warning',
        'This is a SYSTEM volume mounted at "' + (mountpoint || '/') + '".\n\n' +
        'Extending is generally safe (can grow online), but proceed with caution.\n\n' +
        'Do you want to continue?',
        function() {
            openLvmExtendModal(lvName, vgName, lvSize, vgFree, mountpoint);
        }
    );
}

function confirmSystemResize(device, fstype, mountpoint) {
    showConfirmModal(
        '⚠️ System Filesystem Warning',
        'This is a SYSTEM filesystem mounted at "' + (mountpoint || '/') + '".\n\n' +
        'Growing the filesystem is generally safe, but proceed with caution.\n\n' +
        'Do you want to continue?',
        function() {
            resizeFilesystem(device, fstype, mountpoint);
        }
    );
}

function openLvmExtendModal(lvName, vgName, lvSize, vgFree, mountpoint) {
    closeAllModals();
    // If mountpoint not provided, try to look it up
    const resolvedMountpoint = mountpoint || findMountpointForLv(vgName, lvName);
    currentExtendLv = { lvName, vgName, lvSize, vgFree, mountpoint: resolvedMountpoint };

    // Update modal content
    document.getElementById('extendLvName').textContent = lvName;
    document.getElementById('extendLvSize').textContent = lvSize + ' GB';
    document.getElementById('extendVgName').textContent = vgName;
    document.getElementById('extendVgFree').textContent = vgFree > 0 ? vgFree + ' GB' : '0 GB (none available)';
    document.getElementById('extendVgFree').style.color = vgFree > 0 ? 'var(--archie-green)' : 'var(--archie-red)';
    document.getElementById('addPvVgName').textContent = vgName;

    // Show/hide appropriate sections based on free space
    const hasSpace = vgFree > 0;
    document.getElementById('lvmNoSpaceWarning').style.display = hasSpace ? 'none' : 'block';
    document.getElementById('lvmExtendOptions').style.display = hasSpace ? 'block' : 'none';

    if (hasSpace) {
        // Set default extend size (min of 10GB or available space)
        const defaultSize = Math.min(10, Math.floor(vgFree));
        document.getElementById('extendSizeInput').value = defaultSize;
        document.getElementById('extendSizeInput').max = Math.floor(vgFree);
        document.getElementById('extendMaxLabel').textContent = Math.floor(vgFree);

        // Update commands
        updateExtendCommands();

        // Listen for input changes
        document.getElementById('extendSizeInput').oninput = updateExtendCommands;
    } else {
        // Show instructions for no-space scenario
        document.getElementById('extendCommands').textContent =
            '# No free space in VG. To add space:\n' +
            '# 1. Add a new disk and partition it\n' +
            '# 2. Initialize as PV:\n' +
            'sudo pvcreate /dev/sdX1\n\n' +
            '# 3. Add to volume group:\n' +
            'sudo vgextend ' + vgName + ' /dev/sdX1\n\n' +
            '# 4. Then return here to extend the LV';
    }

    // Show modal
    document.getElementById('lvmExtendModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function closeLvmExtendModal() {
    document.getElementById('lvmExtendModal').classList.remove('active');
    currentExtendLv = null;
}

// LVM Shrink Modal Functions
let currentShrinkLv = null;

function openLvmShrinkModal(lvDevice, lvName, vgName, currentSizeGb, usedGb, fstype, mountpoint) {
    closeAllModals();
    currentShrinkLv = { lvDevice, lvName, vgName, currentSizeGb, usedGb, fstype, mountpoint };

    document.getElementById('shrinkLvName').textContent = lvName;
    document.getElementById('shrinkLvSize').textContent = formatSizeGB(currentSizeGb);
    document.getElementById('shrinkLvUsed').textContent = formatSizeGB(usedGb);

    const minSafeGb = usedGb * 1.10;  // 10% buffer
    document.getElementById('shrinkMinSize').textContent = formatSizeGB(minSafeGb);

    // Reset warnings
    document.getElementById('shrinkXfsWarning').style.display = 'none';
    document.getElementById('shrinkRootWarning').style.display = 'none';
    document.getElementById('shrinkSizeInputArea').style.display = 'block';
    document.getElementById('executeLvmShrinkBtn').style.display = 'inline-flex';

    // Check for XFS (cannot shrink)
    if (fstype === 'xfs') {
        document.getElementById('shrinkXfsWarning').style.display = 'block';
        document.getElementById('shrinkSizeInputArea').style.display = 'none';
        document.getElementById('executeLvmShrinkBtn').style.display = 'none';
    }

    // Check for root filesystem
    if (mountpoint === '/') {
        document.getElementById('shrinkRootWarning').style.display = 'block';
        document.getElementById('shrinkSizeInputArea').style.display = 'none';
        document.getElementById('executeLvmShrinkBtn').style.display = 'none';
    }

    // Set default value and hint
    const suggestedSize = Math.ceil(minSafeGb + 5);  // Minimum + 5GB headroom
    document.getElementById('shrinkTargetSize').value = suggestedSize < currentSizeGb ? suggestedSize : Math.ceil(minSafeGb);
    document.getElementById('shrinkTargetSize').min = Math.ceil(minSafeGb);
    document.getElementById('shrinkTargetSize').max = currentSizeGb - 1;
    document.getElementById('shrinkSizeHint').textContent =
        `Valid range: ${minSafeGb.toFixed(1)} GB (minimum) to ${(currentSizeGb - 1).toFixed(1)} GB`;

    document.getElementById('lvmShrinkModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function closeLvmShrinkModal() {
    document.getElementById('lvmShrinkModal').classList.remove('active');
    currentShrinkLv = null;
}

function executeLvmShrink() {
    if (!currentShrinkLv) return;

    const targetSize = parseFloat(document.getElementById('shrinkTargetSize').value);
    const minSafe = currentShrinkLv.usedGb * 1.10;

    if (isNaN(targetSize) || targetSize < minSafe) {
        showToast('error', `Target size must be at least ${minSafe.toFixed(1)} GB (used + 10% buffer)`);
        return;
    }

    if (targetSize >= currentShrinkLv.currentSizeGb) {
        showToast('error', 'Target size must be smaller than current size');
        return;
    }

    // Save info before closing modal
    const shrinkInfo = { ...currentShrinkLv };
    closeLvmShrinkModal();

    // Construct device path for pre-flight
    const device = shrinkInfo.lvDevice || '/dev/' + shrinkInfo.vgName + '/' + shrinkInfo.lvName;
    const mountpoint = shrinkInfo.mountpoint || '';

    // Run pre-flight check before proceeding
    runPreflightCheck(device, mountpoint, 'shrink', function() {
        requestReauth('Shrink ' + shrinkInfo.lvName, function() {
            showConfirmModal('SHRINK VOLUME',
                'WARNING: This will shrink ' + shrinkInfo.lvName + ' to ' + targetSize + ' GB.\n\n' +
                'The volume will be temporarily unmounted during this operation. ' +
                'Ensure no applications are using the volume.',
                function() {
                    submitHostCommand('lvm_shrink', {
                        vg_name: shrinkInfo.vgName,
                        lv_name: shrinkInfo.lvName,
                        target_size_gb: targetSize
                    }, {
                        label: 'Shrink ' + shrinkInfo.lvName,
                        successCallback: function() {
                            setTimeout(function() { loadStorageData(); }, 2000);
                        }
                    });
                }
            );
        });
    });
}

// Convert to LVM Modal Functions
let currentConvertDevice = null;

function openConvertToLvmModal(deviceOrStableId, sizeGb, fstype, mountpoint) {
    closeAllModals();
    currentConvertDevice = { device: deviceOrStableId, sizeGb, fstype, mountpoint };

    // Show a friendly display - if it's a stable_id (doesn't start with /dev/), truncate for display
    const displayText = deviceOrStableId.startsWith('/dev/') ? deviceOrStableId : deviceOrStableId.substring(0, 40) + (deviceOrStableId.length > 40 ? '...' : '');
    document.getElementById('convertDevice').textContent = displayText;
    document.getElementById('convertDevice').title = deviceOrStableId;  // Full ID on hover
    document.getElementById('convertSize').textContent = formatSizeGB(sizeGb);

    // Suggest names based on device (e.g., sdb1 -> data-vg, data-lv)
    // For stable_id, extract a simple base name from the model/serial
    let baseName;
    if (deviceOrStableId.startsWith('/dev/')) {
        baseName = deviceOrStableId.replace('/dev/', '').replace(/[0-9]+$/, '');
    } else {
        // For stable_id like "ST3000DM008 2DM166_Z504K0DJ-part1", use "data" as a default
        baseName = 'data';
    }
    document.getElementById('convertVgName').value = baseName + '-vg';
    document.getElementById('convertLvName').value = baseName + '-lv';
    document.getElementById('convertFstype').value = 'ext4';

    document.getElementById('convertToLvmModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function closeConvertToLvmModal() {
    document.getElementById('convertToLvmModal').classList.remove('active');
    currentConvertDevice = null;
}

function executeConvertToLvm() {
    if (!currentConvertDevice) return;

    const vgName = document.getElementById('convertVgName').value.trim();
    const lvName = document.getElementById('convertLvName').value.trim();
    const fstype = document.getElementById('convertFstype').value;

    // Validate names
    if (!vgName || !lvName) {
        showToast('error', 'Please enter both VG and LV names');
        return;
    }

    if (!/^[a-zA-Z0-9_-]+$/.test(vgName) || !/^[a-zA-Z0-9_-]+$/.test(lvName)) {
        showToast('error', 'Names can only contain letters, numbers, hyphens, and underscores');
        return;
    }

    // Save device info before closing modal (modal close sets currentConvertDevice to null)
    const deviceInfo = { ...currentConvertDevice };
    closeConvertToLvmModal();

    requestReauth('Convert ' + deviceInfo.device + ' to LVM', function() {
        showConfirmModal('CONVERT TO LVM',
            'WARNING: This will ERASE ALL DATA on ' + deviceInfo.device + '!\n\n' +
            'The partition will be converted to:\n' +
            '• Volume Group: ' + vgName + '\n' +
            '• Logical Volume: ' + lvName + '\n' +
            '• Filesystem: ' + fstype + '\n\n' +
            'This cannot be undone!',
            function() {
                submitHostCommand('convert_to_lvm', {
                    device: deviceInfo.device,
                    vg_name: vgName,
                    lv_name: lvName,
                    fstype: fstype
                }, {
                    label: 'Convert to LVM',
                    timeout: 300000,  // 5 minutes for large drives
                    successCallback: function() {
                        setTimeout(function() { loadStorageData(); }, 3000);
                    }
                });
            }
        );
    });
}

// Change Label Modal Functions (for regular partitions)
let currentLabelDevice = null;

function openChangeLabelModal(device, currentLabel, fstype) {
    closeAllModals();
    currentLabelDevice = { device, currentLabel, fstype };

    document.getElementById('labelDevice').textContent = device;
    document.getElementById('labelCurrent').textContent = currentLabel || '(none)';
    document.getElementById('labelNewName').value = currentLabel || '';

    document.getElementById('changeLabelModal').classList.add('active');
    setTimeout(() => document.getElementById('labelNewName').focus(), 100);
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function closeChangeLabelModal() {
    document.getElementById('changeLabelModal').classList.remove('active');
    currentLabelDevice = null;
}

function executeChangeLabel() {
    if (!currentLabelDevice) return;

    const newLabel = document.getElementById('labelNewName').value.trim();

    if (newLabel.length > 16) {
        showToast('error', 'Label must be 16 characters or less');
        return;
    }

    // Save info before closing modal
    const labelInfo = { ...currentLabelDevice };
    closeChangeLabelModal();

    requestReauth('Change label on ' + labelInfo.device, function() {
        submitHostCommand('disk_label', {
            device: labelInfo.device,
            label: newLabel,
            fstype: labelInfo.fstype
        }, {
            label: 'Change Label',
            successCallback: function() {
                setTimeout(function() { loadStorageData(); }, 1000);
            }
        });
    });
}

// LVM Rename Modal Functions
let currentRenameLv = null;

function openLvmRenameModal(vgName, lvName) {
    closeAllModals();
    currentRenameLv = { vgName, lvName };

    document.getElementById('renameVgName').textContent = vgName;
    document.getElementById('renameCurrentLvName').textContent = lvName;
    document.getElementById('renameNewLvName').value = '';
    document.getElementById('renameNewLvName').placeholder = lvName;

    document.getElementById('lvmRenameModal').classList.add('active');
    setTimeout(() => document.getElementById('renameNewLvName').focus(), 100);
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function closeLvmRenameModal() {
    document.getElementById('lvmRenameModal').classList.remove('active');
    currentRenameLv = null;
}

function executeLvmRename() {
    if (!currentRenameLv) return;

    const newName = document.getElementById('renameNewLvName').value.trim();

    // Validate name
    if (!newName) {
        showToast('error', 'Please enter a new name');
        return;
    }

    if (!/^[a-zA-Z0-9_-]+$/.test(newName)) {
        showToast('error', 'Name can only contain letters, numbers, hyphens, and underscores');
        return;
    }

    if (newName === currentRenameLv.lvName) {
        showToast('error', 'New name is the same as current name');
        return;
    }

    // Save info before closing modal
    const renameInfo = { ...currentRenameLv };
    closeLvmRenameModal();

    requestReauth('Rename LV ' + renameInfo.lvName + ' to ' + newName, function() {
        showConfirmModal('RENAME LOGICAL VOLUME',
            'This will rename ' + renameInfo.lvName + ' to ' + newName + '.\n\n' +
            '/etc/fstab will be updated automatically if needed.',
            function() {
                submitHostCommand('lvm_rename', {
                    vg_name: renameInfo.vgName,
                    old_lv_name: renameInfo.lvName,
                    new_lv_name: newName
                }, {
                    label: 'Rename ' + renameInfo.lvName,
                    successCallback: function() {
                        setTimeout(function() { loadStorageData(); }, 2000);
                    }
                });
            }
        );
    });
}

// Escape key closes any active modal
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        closeConfirmModal();
        closeLvmExtendModal();
        closeReauthModal();
        closeActionPanel();
        closeCreateStackWizard();
        closePartitionDetailsModal();
        closeMountModal();
        closeFormatModal();
        closeCreateLvModal();
        closeCreatePartitionModal();
        closePreflightModal();
    }
});

// Overlay click closes modals
document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', e => {
        if (e.target === overlay) overlay.classList.remove('active');
    });
});

function setExtendMax() {
    if (!currentExtendLv) return;
    const maxGb = Math.floor(currentExtendLv.vgFree);
    document.getElementById('extendSizeInput').value = maxGb;
    updateExtendCommands();
}

function updateExtendCommands() {
    if (!currentExtendLv) return;

    const extendSize = parseInt(document.getElementById('extendSizeInput').value) || 0;
    const { lvName, vgName, lvSize, vgFree } = currentExtendLv;

    if (extendSize <= 0 || extendSize > vgFree) {
        document.getElementById('extendCommands').textContent =
            '# Invalid size. Enter a value between 1 and ' + Math.floor(vgFree) + ' GB';
        return;
    }

    const newSize = lvSize + extendSize;

    // Generate commands
    const commands = [
        '# Extend logical volume ' + lvName + ' by ' + extendSize + 'GB',
        '# New size will be: ' + newSize + ' GB',
        '',
        '# Step 1: Extend the logical volume',
        'sudo lvextend -L +' + extendSize + 'G /dev/' + vgName + '/' + lvName,
        '',
        '# Step 2: Resize the filesystem (for ext4)',
        'sudo resize2fs /dev/' + vgName + '/' + lvName,
        '',
        '# For XFS filesystems, use instead:',
        '# sudo xfs_growfs /dev/' + vgName + '/' + lvName,
        '',
        '# Step 3: Verify the new size',
        'df -h | grep ' + lvName
    ].join('\n');

    document.getElementById('extendCommands').textContent = commands;
}

function copyExtendCommands() {
    const commands = document.getElementById('extendCommands').textContent;
    navigator.clipboard.writeText(commands).then(() => {
        showToast('success', 'Commands copied to clipboard!');
    }).catch(err => {
        showToast('error', 'Failed to copy: ' + err);
    });
}

async function refreshAfterLvmExtend() {
    showToast('success', 'Refreshing LVM status...');
    closeLvmExtendModal();

    // Reload host data to get updated LVM info
    await loadHostData();
    await loadDisks();

    showToast('success', 'LVM status refreshed. Check the updated values.');
}

// ===== Host Command Queue Functions =====

async function submitHostCommand(commandType, params, options) {
    options = options || {};
    var successCallback = options.successCallback || null;
    var label = options.label || commandType;
    var timeout = options.timeout || 120000;
    var showOverlay = options.showOverlay !== false; // Default true

    if (showOverlay) {
        showLoadingOverlay(label, 'Submitting command to host...');
    }

    var commandId = null;
    var startTime = Date.now();

    try {
        var submitRes = await fetch('/dashboard/api/host-command/submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command_type: commandType, params: params, confirm: true })
        });
        var submitJson = await submitRes.json();

        if (submitJson.requires_reauth) {
            hideLoadingOverlay();
            requestReauth(label, function() {
                submitHostCommand(commandType, params, options);
            });
            return;
        }

        if (!submitJson.success) {
            if (showOverlay) {
                showCompletionMessage(false, 'Command Failed', submitJson.error || 'Failed to submit command');
            } else {
                showToast('error', submitJson.error || 'Failed to submit command');
            }
            return;
        }

        commandId = submitJson.command_id;

        // Track operation in activity status bar
        trackOperation(commandId, label);
        updateOperationStage(commandId, 'Executing...');

        if (showOverlay) {
            updateLoadingProgress('Waiting for host execution...');
        }

        var deadline = Date.now() + timeout;
        var pollInterval = 2000;
        var pollCount = 0;

        var poll = async function() {
            pollCount++;
            if (Date.now() > deadline) {
                completeOperation(commandId, false, label + ' timed out');
                if (showOverlay) {
                    showCompletionMessage(false, 'Operation Timed Out', 'The host may still be processing. Check the Storage tab for updates.');
                } else {
                    showToast('error', label + ' timed out. The host may still be processing.');
                }
                return;
            }

            // Update stage with elapsed time
            updateOperationStage(commandId, 'Executing...');

            if (showOverlay) {
                updateLoadingProgress('Executing on host... (' + (pollCount * 2) + 's)');
            }

            try {
                var resultRes = await fetch('/dashboard/api/host-command/' + commandId + '/result');
                if (resultRes.status === 200) {
                    var resultJson = await resultRes.json();
                    var cmdResult = (resultJson.data && resultJson.data.result) ? resultJson.data.result : {};

                    // Detect stage from result content
                    if (cmdResult.containers_stopped && cmdResult.containers_stopped.length > 0) {
                        updateOperationStage(commandId, 'Stopped ' + cmdResult.containers_stopped.length + ' containers');
                    }
                    if (cmdResult.output && cmdResult.output.includes('resize2fs')) {
                        updateOperationStage(commandId, 'Running resize2fs...');
                    }

                    // Update loading message while we verify and refresh
                    updateOperationStage(commandId, 'Verifying...');
                    if (showOverlay) {
                        updateLoadingProgress('Verifying changes...');
                    }

                    // Wait for host_monitor to pick up changes (runs every 5s)
                    await new Promise(resolve => setTimeout(resolve, 3000));

                    // Refresh storage data and overview
                    updateOperationStage(commandId, 'Refreshing...');
                    if (showOverlay) {
                        updateLoadingProgress('Refreshing data...');
                    }
                    await loadStorageData();
                    await loadOverview();

                    // Calculate duration
                    var durationSec = Math.round((Date.now() - startTime) / 1000);
                    var durationStr = durationSec < 120 ? durationSec + 's' : Math.floor(durationSec / 60) + 'm ' + (durationSec % 60) + 's';

                    // Build metadata for completion message
                    var meta = {
                        duration: durationStr,
                        containers_stopped: cmdResult.containers_stopped || [],
                        containers_restarted: cmdResult.containers_restarted || [],
                        fstab_updated: cmdResult.fstab_updated || false
                    };

                    // Complete tracking with success/failure info
                    var successMsg = cmdResult.message || (label + ' completed in ' + durationStr);
                    var failMsg = cmdResult.error || (label + ' failed');
                    completeOperation(commandId, cmdResult.success, cmdResult.success ? successMsg : failMsg);

                    // Now show completion message
                    if (cmdResult.success) {
                        if (showOverlay) {
                            showCompletionMessage(true, label + ' Complete', cmdResult.message || 'Operation completed successfully', meta);
                        } else {
                            showToast('success', cmdResult.message || (label + ' completed successfully'));
                        }
                    } else {
                        if (showOverlay) {
                            showCompletionMessage(false, label + ' Failed', cmdResult.error || 'Operation failed', meta);
                        } else {
                            showToast('error', cmdResult.error || (label + ' failed'));
                        }
                    }
                    if (successCallback) successCallback(cmdResult);
                    return;
                } else if (resultRes.status === 202) {
                    setTimeout(poll, pollInterval);
                } else {
                    completeOperation(commandId, false, label + ': unexpected status ' + resultRes.status);
                    if (showOverlay) {
                        showCompletionMessage(false, 'Unexpected Error', 'Status ' + resultRes.status);
                    } else {
                        showToast('error', label + ': unexpected status ' + resultRes.status);
                    }
                }
            } catch (pollErr) {
                completeOperation(commandId, false, label + ': ' + pollErr.message);
                if (showOverlay) {
                    showCompletionMessage(false, 'Connection Error', pollErr.message);
                } else {
                    showToast('error', label + ': polling error - ' + pollErr.message);
                }
            }
        };
        // Initial delay before first poll to let host_monitor pick up the command
        setTimeout(poll, 1500);
    } catch (err) {
        if (commandId) completeOperation(commandId, false, label + ': ' + err.message);
        if (showOverlay) {
            showCompletionMessage(false, 'Error', err.message);
        } else {
            showToast('error', label + ': ' + err.message);
        }
    }
}

function executeLvmExtend() {
    if (!currentExtendLv) return;
    var lvName = currentExtendLv.lvName;
    var vgName = currentExtendLv.vgName;
    var vgFree = currentExtendLv.vgFree;
    var mountpoint = currentExtendLv.mountpoint || '';
    var extendSize = parseInt(document.getElementById('extendSizeInput').value) || 0;

    if (extendSize <= 0 || extendSize > vgFree) {
        showToast('error', 'Invalid size. Enter between 1 and ' + Math.floor(vgFree) + ' GB.');
        return;
    }

    // Close extend modal first
    closeLvmExtendModal();

    // Construct device path for pre-flight
    var device = '/dev/' + vgName + '/' + lvName;

    // Run pre-flight check before confirming
    runPreflightCheck(device, mountpoint, 'extend', function() {
        showConfirmModal('Execute LVM Extend',
            'Extend ' + vgName + '/' + lvName + ' by ' + extendSize + ' GB on the host? This will resize the filesystem automatically.',
            function() {
                submitHostCommand('lvm_extend', {
                    vg_name: vgName,
                    lv_name: lvName,
                    size_gb: extendSize
                }, {
                    label: 'Extend ' + lvName,
                    successCallback: function() { loadStorageData(); loadOverview(); }
                });
            }
        );
    });
}

function executeLvmExtendAll() {
    if (!currentExtendLv) return;
    var lvName = currentExtendLv.lvName;
    var vgName = currentExtendLv.vgName;
    var vgFree = currentExtendLv.vgFree;
    var mountpoint = currentExtendLv.mountpoint || '';

    if (vgFree <= 0) {
        showToast('error', 'No free space available in volume group');
        return;
    }

    // Close extend modal first
    closeLvmExtendModal();

    // Construct device path for pre-flight
    var device = '/dev/' + vgName + '/' + lvName;

    // Run pre-flight check before confirming
    runPreflightCheck(device, mountpoint, 'extend', function() {
        showConfirmModal('Use All Free Space',
            'Extend ' + vgName + '/' + lvName + ' using ALL ' + vgFree + ' GB of free space? This will resize the filesystem automatically.',
            function() {
                submitHostCommand('lvm_extend', {
                    vg_name: vgName,
                    lv_name: lvName,
                    extend_all: true
                }, {
                    label: 'Extend ' + lvName + ' (all free space)',
                    successCallback: function() { loadStorageData(); loadOverview(); }
                });
            }
        );
    });
}

// Global partition data map for stable ID lookups
// Maps partition key (stable_id or device path) to partition data with parent disk
window.partitionDataMap = {};

// Mount Modal state
let currentMountDevice = null;
let currentMountData = null;  // Stores partition data including stable_id

function openMountModal(deviceOrStableId) {
    closeAllModals();
    currentMountDevice = deviceOrStableId;

    // Show a friendly display - if it's a stable_id (doesn't start with /dev/), show it nicely
    const displayText = deviceOrStableId.startsWith('/dev/') ? deviceOrStableId : deviceOrStableId.substring(0, 40) + (deviceOrStableId.length > 40 ? '...' : '');
    document.getElementById('mountDevice').textContent = displayText;
    document.getElementById('mountDevice').title = deviceOrStableId;  // Full ID on hover
    document.getElementById('mountMountpoint').value = '';

    document.getElementById('mountModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();

    // Focus the input
    setTimeout(function() {
        document.getElementById('mountMountpoint').focus();
    }, 100);
}

function closeMountModal() {
    document.getElementById('mountModal').classList.remove('active');
    currentMountDevice = null;
    currentMountData = null;
}

function executeMount() {
    if (!currentMountDevice) return;

    var device = currentMountDevice;
    var mountpoint = document.getElementById('mountMountpoint').value.trim();

    if (!mountpoint) {
        showToast('error', 'Please enter a mount point.');
        return;
    }

    if (!/^\/[a-zA-Z0-9_\/.\-]+$/.test(mountpoint)) {
        showToast('error', 'Invalid mountpoint. Must be an absolute path.');
        return;
    }

    closeMountModal();

    showConfirmModal('Mount Device',
        'Mount ' + device + ' at ' + mountpoint + '?',
        function() {
            submitHostCommand('disk_mount', {
                device: device,
                mountpoint: mountpoint
            }, {
                label: 'Mount ' + device,
                successCallback: function() { loadStorageData(); }
            });
        }
    );
}

function promptAndUnmount(deviceOrStableId) {
    // Close partition details modal if open
    closePartitionDetailsModal();

    // Show a friendly display - if it's a stable_id (doesn't start with /dev/), truncate for display
    const displayText = deviceOrStableId.startsWith('/dev/') ? deviceOrStableId : deviceOrStableId.substring(0, 40) + (deviceOrStableId.length > 40 ? '...' : '');

    showConfirmModal('Unmount Device',
        'Unmount ' + displayText + '? Make sure no processes are using this device.',
        function() {
            submitHostCommand('disk_unmount', {
                device: deviceOrStableId  // Backend will resolve stable_id to device path
            }, {
                label: 'Unmount ' + displayText,
                successCallback: function() { loadStorageData(); }
            });
        }
    );
}

// Format Modal state
let currentFormatDevice = null;

function openFormatModal(deviceOrStableId, sizeGb, currentFs, currentLabel) {
    closeAllModals();
    closeActionPanel();
    currentFormatDevice = deviceOrStableId;

    // Show a friendly display - if it's a stable_id (doesn't start with /dev/), truncate for display
    const displayText = deviceOrStableId.startsWith('/dev/') ? deviceOrStableId : deviceOrStableId.substring(0, 40) + (deviceOrStableId.length > 40 ? '...' : '');
    document.getElementById('formatDevice').textContent = displayText;
    document.getElementById('formatDevice').title = deviceOrStableId;  // Full ID on hover
    document.getElementById('formatSize').textContent = formatSizeGB(sizeGb || 0);
    document.getElementById('formatCurrentFs').textContent = currentFs || 'Unknown';
    document.getElementById('formatCurrentLabel').textContent = currentLabel || 'None';
    document.getElementById('formatLabel').value = '';
    document.getElementById('formatFsType').value = 'ext4';

    updateFormatCommand();

    document.getElementById('formatModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function closeFormatModal() {
    document.getElementById('formatModal').classList.remove('active');
    currentFormatDevice = null;
}

function updateFormatCommand() {
    var device = currentFormatDevice || '/dev/xxx';
    var fstype = document.getElementById('formatFsType').value;
    var label = document.getElementById('formatLabel').value.trim();

    var cmd = 'sudo mkfs.' + fstype + ' ';
    if (label) {
        if (fstype === 'vfat') {
            cmd += '-n "' + label + '" ';
        } else {
            cmd += '-L "' + label + '" ';
        }
    }
    cmd += device;

    document.getElementById('formatCommand').textContent = cmd;
}

function executeFormat() {
    if (!currentFormatDevice) return;

    var device = currentFormatDevice;
    var fstype = document.getElementById('formatFsType').value;
    var label = document.getElementById('formatLabel').value.trim();

    // Close format modal first
    closeFormatModal();

    // Request re-authentication then confirm
    requestReauth('Format ' + device, function() {
        showConfirmModal('FORMAT DEVICE',
            'WARNING: This will ERASE ALL DATA on ' + device + '! Format as ' + fstype + (label ? ' with label "' + label + '"' : '') + '?',
            function() {
                var params = { device: device, fstype: fstype };
                if (label) params.label = label;

                submitHostCommand('disk_format', params, {
                    label: 'Format ' + device,
                    successCallback: function() { loadStorageData(); }
                });
            }
        );
    });
}

// Wipe Modal state
let currentWipeDevice = null;
let currentWipeInfo = {};

function openWipeModal(device, sizeGb, info) {
    closeAllModals();
    closeActionPanel();
    currentWipeDevice = device;
    currentWipeInfo = { device, sizeGb, info };

    // Show a friendly display
    const displayText = device.startsWith('/dev/') ? device : device.substring(0, 40) + (device.length > 40 ? '...' : '');
    document.getElementById('wipeDevice').textContent = displayText;
    document.getElementById('wipeDevice').title = device;
    document.getElementById('wipeSize').textContent = formatSizeGB(sizeGb || 0);
    document.getElementById('wipeInfo').textContent = info || 'Unknown';

    // Reset checkboxes
    document.getElementById('wipeLvm').checked = true;
    document.getElementById('wipePartitionTable').checked = false;

    document.getElementById('wipeModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function closeWipeModal() {
    document.getElementById('wipeModal').classList.remove('active');
    currentWipeDevice = null;
    currentWipeInfo = {};
}

function executeWipe() {
    if (!currentWipeDevice) return;

    var device = currentWipeDevice;
    var wipeLvm = document.getElementById('wipeLvm').checked;
    var wipePartitionTable = document.getElementById('wipePartitionTable').checked;

    // Close wipe modal first
    closeWipeModal();

    // Request re-authentication then confirm
    requestReauth('Wipe ' + device, function() {
        var warningMsg = 'WARNING: This will PERMANENTLY DELETE ALL DATA on ' + device + '!\n\n';
        if (wipeLvm) warningMsg += '• Remove all LVM structures\n';
        if (wipePartitionTable) warningMsg += '• Wipe partition table\n';
        warningMsg += '\nThis action CANNOT be undone!';

        showConfirmModal('WIPE DEVICE', warningMsg, function() {
            var params = { device: device };
            if (wipeLvm) params.wipe_lvm = true;
            if (wipePartitionTable) params.wipe_partition_table = true;

            submitHostCommand('disk_wipe', params, {
                label: 'Wipe ' + device,
                successCallback: function() { loadStorageData(); }
            });
        });
    });
}

// Prepare LVM Modal state
let currentPrepareLvmDisk = null;

// Safe wrapper that checks for mounted partitions before showing prepare LVM modal
function openPrepareLvmModalSafe(diskJsonStr) {
    try {
        const disk = JSON.parse(diskJsonStr.replace(/&quot;/g, '"'));

        // Check if any partitions are mounted
        const mountedParts = (disk.partitions || []).filter(p => p.mountpoint);

        if (mountedParts.length > 0) {
            // Show warning modal with unmount option
            const mountList = mountedParts.map(p => `• ${p.device} at ${p.mountpoint}`).join('\n');
            closeAllModals();

            showConfirmModal('Drive Has Mounted Partitions',
                `Cannot prepare ${disk.device} because these partitions are mounted:\n\n${mountList}\n\nClick Confirm to unmount them and proceed.`,
                function() {
                    // Unmount all mounted partitions sequentially then open modal
                    unmountPartitionsAndPrepareLvm(disk, mountedParts);
                }
            );
        } else {
            // No mounted partitions, proceed normally
            openPrepareLvmModal(disk);
        }
    } catch (e) {
        console.error('Failed to parse disk data:', e);
        showToast('error', 'Failed to open prepare LVM modal');
    }
}

// Unmount partitions then open prepare LVM modal
async function unmountPartitionsAndPrepareLvm(disk, mountedParts) {
    showLoadingOverlay('Unmounting partitions', 'Please wait...');

    for (const part of mountedParts) {
        const deviceToUnmount = part.device;
        updateLoadingProgress(`Unmounting ${deviceToUnmount}...`);

        try {
            const res = await fetch('/dashboard/api/host-command/submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    command_type: 'disk_unmount',
                    params: { device: deviceToUnmount },
                    confirm: true
                })
            });

            const json = await res.json();

            if (json.requires_reauth) {
                hideLoadingOverlay();
                showToast('error', 'Re-authentication required. Please try again.');
                return;
            }

            if (!json.success) {
                hideLoadingOverlay();
                showToast('error', `Failed to unmount ${deviceToUnmount}: ${json.error || 'Unknown error'}`);
                return;
            }

            // Wait for command to complete
            const cmdId = json.command_id;
            let attempts = 0;
            let completed = false;

            while (attempts < 30 && !completed) {
                await new Promise(r => setTimeout(r, 1000));
                const resultRes = await fetch(`/dashboard/api/host-command/${cmdId}/result`);

                if (resultRes.status === 200) {
                    const resultJson = await resultRes.json();
                    const cmdResult = resultJson.data?.result || {};

                    if (!cmdResult.success) {
                        hideLoadingOverlay();
                        showToast('error', `Unmount failed: ${cmdResult.error || 'Unknown error'}`);
                        return;
                    }
                    completed = true;
                } else if (resultRes.status !== 202) {
                    hideLoadingOverlay();
                    showToast('error', `Unexpected error checking unmount result`);
                    return;
                }
                attempts++;
            }

            if (!completed) {
                hideLoadingOverlay();
                showToast('error', 'Unmount timed out');
                return;
            }
        } catch (e) {
            hideLoadingOverlay();
            showToast('error', `Error unmounting ${part.device}: ${e.message}`);
            return;
        }
    }

    hideLoadingOverlay();
    showToast('success', 'All partitions unmounted');

    // Now open the prepare LVM modal
    setTimeout(() => {
        openPrepareLvmModal(disk);
    }, 500);
}

function openPrepareLvmModal(disk) {
    closeAllModals();
    currentPrepareLvmDisk = disk;

    // Populate modal
    document.getElementById('prepareLvmDevice').textContent = disk.device;
    document.getElementById('prepareLvmSize').textContent = formatSizeGB(disk.size_gb || 0);
    document.getElementById('prepareLvmModel').textContent = disk.model || 'Unknown';

    // Suggest default names based on disk name (e.g., sdb -> sdb-vg, sdb-lv)
    const baseName = (disk.name || 'data').replace(/[^a-zA-Z0-9]/g, '');
    document.getElementById('prepareLvmVgName').value = baseName + '-vg';
    document.getElementById('prepareLvmLvName').value = baseName + '-lv';
    document.getElementById('prepareLvmFs').value = 'ext4';
    document.getElementById('prepareLvmMountpoint').value = '/mnt/' + baseName;

    document.getElementById('prepareLvmModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function closePrepareLvmModal() {
    document.getElementById('prepareLvmModal').classList.remove('active');
    currentPrepareLvmDisk = null;
}

function executePrepareLvm() {
    if (!currentPrepareLvmDisk) {
        showToast('error', 'No drive selected');
        return;
    }

    const disk = currentPrepareLvmDisk;
    const vgName = document.getElementById('prepareLvmVgName').value.trim();
    const lvName = document.getElementById('prepareLvmLvName').value.trim();
    const fstype = document.getElementById('prepareLvmFs').value;
    const mountpoint = document.getElementById('prepareLvmMountpoint').value.trim();

    // Validate
    if (!vgName || !lvName) {
        showToast('error', 'Please enter VG and LV names');
        return;
    }

    if (!/^[a-zA-Z0-9_-]+$/.test(vgName) || !/^[a-zA-Z0-9_-]+$/.test(lvName)) {
        showToast('error', 'Names can only contain letters, numbers, hyphens, and underscores');
        return;
    }

    if (mountpoint && !/^\/[a-zA-Z0-9_\/.\-]+$/.test(mountpoint)) {
        showToast('error', 'Invalid mount point. Must be an absolute path.');
        return;
    }

    closePrepareLvmModal();

    // Request re-authentication then confirm
    requestReauth('Prepare ' + disk.device + ' as LVM', function() {
        showConfirmModal('PREPARE AS LVM',
            `WARNING: This will ERASE ALL DATA on ${disk.device}!\n\nThe drive will be set up as:\n• Volume Group: ${vgName}\n• Logical Volume: ${lvName}\n• Filesystem: ${fstype}` + (mountpoint ? `\n• Mount: ${mountpoint}` : ''),
            function() {
                const params = {
                    device: disk.device,
                    vg_name: vgName,
                    lv_name: lvName,
                    fstype: fstype
                };
                if (mountpoint) params.mountpoint = mountpoint;

                submitHostCommand('disk_prepare_lvm', params, {
                    label: 'Prepare ' + disk.device + ' as LVM',
                    timeout: 300000,  // 5 minutes for large drives
                    successCallback: function() {
                        setTimeout(function() { loadStorageData(); }, 3000);
                    }
                });
            }
        );
    });
}

// Create LV Modal state
let currentCreateLvVg = null;
let currentCreateLvFreeGb = 0;

function openCreateLvModal(vgName, freeGb) {
    closeAllModals();
    currentCreateLvVg = vgName;
    currentCreateLvFreeGb = freeGb;

    document.getElementById('createLvVgName').textContent = vgName;
    document.getElementById('createLvFreeSpace').textContent = formatSizeGB(freeGb);
    document.getElementById('createLvName').value = '';
    document.getElementById('createLvSize').value = Math.min(10, Math.floor(freeGb));
    document.getElementById('createLvSize').max = Math.floor(freeGb);
    document.getElementById('createLvFsType').value = 'ext4';
    document.getElementById('createLvMountpoint').value = '';

    document.getElementById('createLvModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();

    setTimeout(function() {
        document.getElementById('createLvName').focus();
    }, 100);
}

function closeCreateLvModal() {
    document.getElementById('createLvModal').classList.remove('active');
    currentCreateLvVg = null;
    currentCreateLvFreeGb = 0;
}

function executeCreateLv() {
    if (!currentCreateLvVg) return;

    var vgName = currentCreateLvVg;
    var freeGb = currentCreateLvFreeGb;
    var lvName = document.getElementById('createLvName').value.trim();
    var sizeGb = parseFloat(document.getElementById('createLvSize').value);
    var fstype = document.getElementById('createLvFsType').value;
    var mountpoint = document.getElementById('createLvMountpoint').value.trim();

    if (!lvName) {
        showToast('error', 'Please enter a logical volume name.');
        return;
    }

    if (!/^[a-zA-Z0-9_\-]+$/.test(lvName)) {
        showToast('error', 'Invalid LV name. Use only letters, numbers, hyphens, and underscores.');
        return;
    }

    if (isNaN(sizeGb) || sizeGb <= 0 || sizeGb > freeGb) {
        showToast('error', 'Invalid size. Must be between 1 and ' + Math.floor(freeGb) + ' GB.');
        return;
    }

    closeCreateLvModal();

    showConfirmModal('Create Logical Volume',
        'Create LV ' + lvName + ' in VG ' + vgName + '? Size: ' + sizeGb + ' GB, FS: ' + fstype + (mountpoint ? ', Mount: ' + mountpoint : ''),
        function() {
            var params = { vg_name: vgName, lv_name: lvName, size_gb: sizeGb, fstype: fstype };
            if (mountpoint && mountpoint.startsWith('/')) params.mountpoint = mountpoint;

            submitHostCommand('lvm_create', params, {
                label: 'Create LV ' + lvName,
                successCallback: function() { loadStorageData(); }
            });
        }
    );
}

// Create VG Modal state
let currentCreateVgDevice = null;

function openCreateVgModal(device) {
    closeAllModals();
    currentCreateVgDevice = device;

    document.getElementById('createVgPvDevice').textContent = device;
    document.getElementById('createVgName').value = '';

    document.getElementById('createVgModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();

    setTimeout(function() {
        document.getElementById('createVgName').focus();
    }, 100);
}

function closeCreateVgModal() {
    document.getElementById('createVgModal').classList.remove('active');
    currentCreateVgDevice = null;
}

function executeCreateVg() {
    if (!currentCreateVgDevice) return;

    var device = currentCreateVgDevice;
    var vgName = document.getElementById('createVgName').value.trim();

    if (!vgName) {
        showToast('error', 'Please enter a volume group name.');
        return;
    }

    if (!/^[a-zA-Z0-9_\-]+$/.test(vgName)) {
        showToast('error', 'Invalid VG name. Use only letters, numbers, hyphens, and underscores.');
        return;
    }

    closeCreateVgModal();

    // Request re-authentication then confirm
    requestReauth('Create VG ' + vgName, function() {
        showConfirmModal('CREATE VOLUME GROUP',
            'WARNING: This will ERASE ALL DATA on ' + device + '!\n\nCreate volume group "' + vgName + '" using ' + device + ' as physical volume?',
            function() {
                submitHostCommand('vg_create', { vg_name: vgName, pv_device: device }, {
                    label: 'Create VG ' + vgName,
                    successCallback: function() { loadStorageData(); }
                });
            }
        );
    });
}

// Snapshot Modal state
let currentSnapshotVg = null;
let currentSnapshotLv = null;
let currentSnapshotFreeGb = 0;

function openSnapshotModal(vgName, lvName, freeGb) {
    closeAllModals();
    currentSnapshotVg = vgName;
    currentSnapshotLv = lvName;
    currentSnapshotFreeGb = freeGb;

    document.getElementById('snapshotSourceLv').textContent = vgName + '/' + lvName;
    document.getElementById('snapshotVgFree').textContent = formatSizeGB(freeGb);
    document.getElementById('snapshotName').value = lvName + '-snap-' + new Date().toISOString().slice(0,10).replace(/-/g, '');
    document.getElementById('snapshotSize').value = Math.min(10, Math.floor(freeGb));

    document.getElementById('snapshotModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();

    setTimeout(function() {
        document.getElementById('snapshotName').focus();
    }, 100);
}

function closeSnapshotModal() {
    document.getElementById('snapshotModal').classList.remove('active');
    currentSnapshotVg = null;
    currentSnapshotLv = null;
    currentSnapshotFreeGb = 0;
}

function executeSnapshot() {
    if (!currentSnapshotVg || !currentSnapshotLv) return;

    var vgName = currentSnapshotVg;
    var lvName = currentSnapshotLv;
    var freeGb = currentSnapshotFreeGb;
    var snapName = document.getElementById('snapshotName').value.trim();
    var sizeGb = parseFloat(document.getElementById('snapshotSize').value);

    if (!snapName) {
        showToast('error', 'Please enter a snapshot name.');
        return;
    }

    if (!/^[a-zA-Z0-9_\-]+$/.test(snapName)) {
        showToast('error', 'Invalid snapshot name. Use only letters, numbers, hyphens, and underscores.');
        return;
    }

    if (isNaN(sizeGb) || sizeGb <= 0 || sizeGb > freeGb) {
        showToast('error', 'Invalid size. Must be between 1 and ' + Math.floor(freeGb) + ' GB.');
        return;
    }

    closeSnapshotModal();

    showConfirmModal('Create Snapshot',
        'Create snapshot "' + snapName + '" of LV ' + lvName + ' in VG ' + vgName + '? Size: ' + sizeGb + ' GB',
        function() {
            submitHostCommand('lvm_snapshot', {
                vg_name: vgName,
                lv_name: lvName,
                snapshot_name: snapName,
                size_gb: sizeGb
            }, {
                label: 'Snapshot ' + lvName,
                successCallback: function() { loadStorageData(); }
            });
        }
    );
}

// Create Partition Modal state
let currentCreatePartitionDevice = null;
let currentCreatePartitionFreeGb = 0;

function openCreatePartitionModal(device, unallocatedGb) {
    closeAllModals();
    currentCreatePartitionDevice = device;
    currentCreatePartitionFreeGb = unallocatedGb;

    document.getElementById('createPartitionDevice').textContent = device;
    document.getElementById('createPartitionFreeSpace').textContent = formatSizeGB(unallocatedGb);
    document.getElementById('createPartitionSize').value = Math.floor(unallocatedGb);
    document.getElementById('createPartitionSize').max = Math.floor(unallocatedGb);

    document.getElementById('createPartitionModal').classList.add('active');
    if (typeof lucide !== 'undefined') lucide.createIcons();

    setTimeout(function() {
        document.getElementById('createPartitionSize').focus();
    }, 100);
}

function closeCreatePartitionModal() {
    document.getElementById('createPartitionModal').classList.remove('active');
    currentCreatePartitionDevice = null;
    currentCreatePartitionFreeGb = 0;
}

function executeCreatePartition() {
    if (!currentCreatePartitionDevice) return;

    var device = currentCreatePartitionDevice;
    var unallocatedGb = currentCreatePartitionFreeGb;
    var sizeGb = parseFloat(document.getElementById('createPartitionSize').value);

    if (isNaN(sizeGb) || sizeGb <= 0 || sizeGb > unallocatedGb) {
        showToast('error', 'Invalid size. Must be between 1 and ' + Math.floor(unallocatedGb) + ' GB.');
        return;
    }

    closeCreatePartitionModal();

    requestReauth('Create partition on ' + device, function() {
        showConfirmModal('Create Partition',
            'Create a ' + sizeGb + ' GB partition on ' + device + '? This modifies the disk partition table.',
            function() {
                submitHostCommand('partition_create', {
                    device: device,
                    size_gb: sizeGb
                }, {
                    label: 'Create partition on ' + device,
                    successCallback: function() { loadStorageData(); }
                });
            }
        );
    });
}

async function checkHostCommandQueue() {
    try {
        var res = await fetch('/dashboard/api/host-command/status');
        var json = await res.json();
        var el = document.getElementById('host-queue-status');
        if (!el) return;

        if (json.available) {
            var statusText = 'Host Queue Ready';
            el.textContent = '';
            var dot = document.createElement('span');
            dot.style.cssText = 'display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--archie-green);';
            el.appendChild(dot);
            var txt = document.createElement('span');
            txt.style.color = 'var(--archie-green)';
            txt.textContent = ' Host Queue Ready';
            el.appendChild(txt);
            if (json.pending_count > 0) {
                var pending = document.createElement('span');
                pending.style.color = 'var(--archie-orange)';
                pending.textContent = ' (' + json.pending_count + ' pending)';
                el.appendChild(pending);
            }
        } else {
            el.textContent = '';
            var dot = document.createElement('span');
            dot.style.cssText = 'display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--archie-text-muted);';
            el.appendChild(dot);
            var txt = document.createElement('span');
            txt.style.color = 'var(--archie-text-muted)';
            txt.textContent = ' Host Queue Offline';
            el.appendChild(txt);
        }
    } catch (e) {
        var el = document.getElementById('host-queue-status');
        if (el) {
            el.textContent = 'Queue N/A';
            el.style.color = 'var(--archie-text-muted)';
        }
    }
}

function showToast(type, message) {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<i data-lucide="${type === 'success' ? 'check-circle' : 'alert-circle'}"></i><span>${message}</span>`;
    container.appendChild(toast);
    lucide.createIcons();
    setTimeout(() => toast.remove(), 4000);
}

// Loading Overlay Functions
let loadingCancelTimeout = null;

function showLoadingOverlay(title, message) {
    const overlay = document.getElementById('loadingOverlay');
    const titleEl = document.getElementById('loadingTitle');
    const messageEl = document.getElementById('loadingMessage');
    const progressEl = document.getElementById('loadingProgress');
    const cancelBtn = document.getElementById('loadingCancelBtn');

    titleEl.textContent = title || 'Processing...';
    messageEl.textContent = message || 'Please wait while the operation completes';
    progressEl.textContent = '';

    // Hide cancel button initially
    if (cancelBtn) cancelBtn.style.display = 'none';

    overlay.classList.add('active');

    // Show cancel button after 15 seconds in case operation gets stuck
    if (loadingCancelTimeout) clearTimeout(loadingCancelTimeout);
    loadingCancelTimeout = setTimeout(() => {
        if (cancelBtn && overlay.classList.contains('active')) {
            cancelBtn.style.display = 'inline-flex';
            lucide.createIcons();
        }
    }, 15000);
}

function updateLoadingProgress(text) {
    const progressEl = document.getElementById('loadingProgress');
    if (progressEl) progressEl.textContent = text;
}

function hideLoadingOverlay() {
    const overlay = document.getElementById('loadingOverlay');
    const cancelBtn = document.getElementById('loadingCancelBtn');

    overlay.classList.remove('active');
    if (cancelBtn) cancelBtn.style.display = 'none';
    if (loadingCancelTimeout) {
        clearTimeout(loadingCancelTimeout);
        loadingCancelTimeout = null;
    }
}

function cancelLoadingOverlay() {
    hideLoadingOverlay();
    showToast('warning', 'Operation cancelled. Check Storage tab for current status.');
    // Refresh data to show current state
    loadStorageData();
    loadOverview();
}

function showCompletionMessage(success, title, message, meta) {
    hideLoadingOverlay();

    // Show "Verifying changes..." in activity bar while data refreshes
    const activityBar = document.getElementById('activityStatusBar');
    const activityText = document.getElementById('activityText');
    const activityTimer = document.getElementById('activityTimer');
    const activityStage = document.getElementById('activityStage');

    if (success && activityBar) {
        activityBar.classList.add('visible');
        if (activityText) activityText.textContent = 'Verifying changes...';
        if (activityTimer) activityTimer.textContent = '';
        if (activityStage) activityStage.textContent = '(Refreshing storage data)';
    }

    // Update global notification to show verifying state
    if (success && typeof broadcastAdminNotification === 'function') {
        broadcastAdminNotification({
            id: 'verify-' + Date.now(),
            type: 'info',
            title: 'Verifying Changes',
            text: 'Refreshing storage data...',
            showTimer: false
        });
    }

    // Skip cache for post-operation refreshes
    skipStorageCache = true;
    console.log('showCompletionMessage: starting refresh sequence, skipStorageCache=true');

    // host_monitor already refreshes data after commands complete
    // First refresh immediately (data should already be available)
    setTimeout(() => {
        console.log('showCompletionMessage: first refresh starting');
        if (activityStage) activityStage.textContent = '(Refreshing...)';
        loadStorageData(true);  // Force refresh, skip cache
        loadOverview();

        // Second refresh after 2 seconds as backup
        setTimeout(() => {
            console.log('showCompletionMessage: second refresh starting');
            if (activityStage) activityStage.textContent = '(Verifying...)';
            loadStorageData(true);  // Force refresh again
            loadOverview();

            // Reset cache skip flag
            skipStorageCache = false;

            // Hide activity bar after verification complete
            setTimeout(() => {
                if (activityBar) {
                    activityBar.classList.remove('visible');
                }
            }, 500);

            // Update global notification to show complete
            if (success && typeof broadcastAdminNotification === 'function') {
                broadcastAdminNotification({
                    id: 'complete-' + Date.now(),
                    type: 'success',
                    title: 'Operation Complete',
                    text: message,
                    showTimer: false
                });
            }
        }, 2000);
    }, 500);

    // Show a persistent completion toast that requires manual dismiss
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    const type = success ? 'success' : (success === false ? 'error' : 'warning');
    toast.className = `toast ${type} persistent`;

    // Build the toast using DOM methods for security
    const closeBtn = document.createElement('button');
    closeBtn.className = 'toast-close';
    closeBtn.title = 'Dismiss';
    closeBtn.textContent = '×';
    closeBtn.onclick = () => {
        toast.remove();
        // Also refresh on dismiss in case data was stale
        loadStorageData();
    };

    const content = document.createElement('div');
    content.className = 'toast-content';

    const icon = document.createElement('i');
    icon.setAttribute('data-lucide', success ? 'check-circle' : (success === false ? 'x-circle' : 'alert-triangle'));
    icon.className = 'toast-icon';
    icon.style.cssText = 'width:24px;height:24px;flex-shrink:0;';

    const body = document.createElement('div');
    body.className = 'toast-body';

    const titleEl = document.createElement('div');
    titleEl.className = 'toast-title';
    titleEl.textContent = title;

    const messageEl = document.createElement('div');
    messageEl.className = 'toast-message';
    messageEl.textContent = message;

    body.appendChild(titleEl);
    body.appendChild(messageEl);

    // Add meta info if provided
    if (meta) {
        const metaParts = [];
        if (meta.duration) metaParts.push('Duration: ' + meta.duration);
        if (meta.containers_stopped && meta.containers_stopped.length > 0) {
            metaParts.push('Containers: ' + meta.containers_stopped.length + ' affected');
        }
        if (meta.fstab_updated) metaParts.push('fstab updated');
        if (metaParts.length > 0) {
            const metaEl = document.createElement('div');
            metaEl.className = 'toast-meta';
            metaEl.textContent = metaParts.join(' • ');
            body.appendChild(metaEl);
        }
    }

    content.appendChild(icon);
    content.appendChild(body);
    toast.appendChild(closeBtn);
    toast.appendChild(content);
    container.appendChild(toast);
    lucide.createIcons();
    // No auto-dismiss - user must click close button
}

// Utilities
function getColorClass(value, warnThreshold = 75, critThreshold = 90) {
    if (value >= critThreshold) return 'red';
    if (value >= warnThreshold) return 'yellow';
    return 'green';
}

function formatUptime(seconds) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    return `${days}d ${hours}h ${mins}m`;
}

function formatBytes(bytes) {
    if (!bytes) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// ===== Stack/Compose File Management =====
let composeEditMode = false;
let originalComposeContent = '';
let currentEditingFile = null;

async function viewComposeFile() {
    // If in stack detail view, delegate to stack compose viewer
    if (currentStackName) { viewStackCompose(); return; }

    currentEditingFile = '/mnt/archie_brain/docker-compose.yml';
    composeEditMode = false;

    // Reset modal state
    document.getElementById('composeModalTitle').innerHTML = `
        <i data-lucide="file-code" style="width: 20px; height: 20px; display: inline-block; vertical-align: middle; margin-right: 8px;"></i>
        docker-compose.yml
    `;
    document.getElementById('composeViewer').style.display = 'block';
    document.getElementById('composeEditor').style.display = 'none';
    document.getElementById('composeEditToggle').style.display = 'inline-flex';
    document.getElementById('composeEditToggle').innerHTML = '<i data-lucide="edit" style="width: 14px; height: 14px;"></i> Edit';
    document.getElementById('composeSaveBtn').style.display = 'none';
    document.getElementById('composeApplyBtn').style.display = 'none';
    document.getElementById('composeStatus').textContent = 'Loading...';

    // Show modal
    document.getElementById('composeModal').classList.add('active');
    lucide.createIcons();

    try {
        const res = await fetch('/dashboard/api/stack/compose');
        const json = await res.json();

        if (json.success) {
            originalComposeContent = json.content;
            document.getElementById('composeViewer').querySelector('code').textContent = json.content;
            document.getElementById('composeEditor').value = json.content;
            document.getElementById('composeStatus').textContent = `Last modified: ${json.modified || 'Unknown'}`;

            // Update services count
            const servicesMatch = json.content.match(/^\s{2}\w+:/gm);
            const serviceCount = servicesMatch ? servicesMatch.length : 0;
            document.getElementById('stack-services-count').textContent = serviceCount;

            // Render services grid
            renderStackServices(json.services || []);
        } else {
            document.getElementById('composeViewer').querySelector('code').textContent = 'Error: ' + json.error;
            document.getElementById('composeStatus').textContent = 'Failed to load';
        }
    } catch (e) {
        document.getElementById('composeViewer').querySelector('code').textContent = 'Error: ' + e.message;
        document.getElementById('composeStatus').textContent = 'Failed to load';
    }
}

function editComposeFile() {
    viewComposeFile().then(() => {
        setTimeout(() => toggleComposeEdit(), 100);
    });
}

function toggleComposeEdit() {
    composeEditMode = !composeEditMode;

    if (composeEditMode) {
        document.getElementById('composeViewer').style.display = 'none';
        document.getElementById('composeEditor').style.display = 'block';
        document.getElementById('composeEditToggle').innerHTML = '<i data-lucide="eye" style="width: 14px; height: 14px;"></i> View';
        document.getElementById('composeSaveBtn').style.display = 'inline-flex';
        document.getElementById('composeApplyBtn').style.display = 'inline-flex';
        document.getElementById('composeStatus').textContent = 'Editing mode - changes not saved';
        document.getElementById('composeStatus').style.color = 'var(--archie-orange)';
    } else {
        document.getElementById('composeViewer').style.display = 'block';
        document.getElementById('composeEditor').style.display = 'none';
        document.getElementById('composeEditToggle').innerHTML = '<i data-lucide="edit" style="width: 14px; height: 14px;"></i> Edit';
        document.getElementById('composeSaveBtn').style.display = 'none';
        document.getElementById('composeApplyBtn').style.display = 'none';

        // Update viewer with current editor content
        document.getElementById('composeViewer').querySelector('code').textContent = document.getElementById('composeEditor').value;
        document.getElementById('composeStatus').style.color = 'var(--archie-text-muted)';
    }

    lucide.createIcons();
}

async function saveComposeFile() {
    const content = document.getElementById('composeEditor').value;

    if (content === originalComposeContent) {
        showToast('info', 'No changes to save');
        return;
    }

    document.getElementById('composeStatus').textContent = 'Saving...';

    try {
        // Use stack endpoint if in stack context
        const url = currentStackName
            ? `/dashboard/api/stacks/${currentStackName}/compose`
            : '/dashboard/api/stack/compose';
        const body = currentStackName
            ? { content }
            : { content, file: currentEditingFile };
        const res = await fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const json = await res.json();

        if (json.success) {
            originalComposeContent = content;
            document.getElementById('composeStatus').textContent = 'Saved successfully';
            document.getElementById('composeStatus').style.color = 'var(--archie-green)';
            showToast('success', 'Compose file saved');
        } else {
            document.getElementById('composeStatus').textContent = 'Save failed: ' + json.error;
            document.getElementById('composeStatus').style.color = 'var(--archie-red)';
            showToast('error', json.error || 'Failed to save');
        }
    } catch (e) {
        document.getElementById('composeStatus').textContent = 'Save error: ' + e.message;
        document.getElementById('composeStatus').style.color = 'var(--archie-red)';
        showToast('error', 'Save error: ' + e.message);
    }
}

function applyComposeChanges() {
    showConfirmModal(
        'Apply & Restart Stack',
        'This will save the compose file and restart all containers. Are you sure?',
        async () => {
            const content = document.getElementById('composeEditor').value;

            try {
                // Save file - use stack endpoint if in stack context
                const saveUrl = currentStackName
                    ? `/dashboard/api/stacks/${currentStackName}/compose`
                    : '/dashboard/api/stack/compose';
                const saveBody = currentStackName
                    ? { content }
                    : { content, file: currentEditingFile };
                const saveRes = await fetch(saveUrl, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(saveBody)
                });

                const saveJson = await saveRes.json();

                if (saveJson.requires_reauth) {
                    showReauthModal('Save Compose File', async () => {
                        applyComposeChanges();
                    });
                    return;
                }

                if (!saveJson.success) {
                    showToast('error', 'Save failed: ' + saveJson.error);
                    return;
                }

                originalComposeContent = content;
                showToast('success', 'Saved. Restarting containers...');

                // Then restart - use stack endpoint if in stack context
                const restartUrl = currentStackName
                    ? `/dashboard/api/stacks/${currentStackName}/action`
                    : '/dashboard/api/docker/compose';
                const restartBody = currentStackName
                    ? { action: 'up', confirm: true }
                    : { action: 'up', confirm: true };
                const restartRes = await fetch(restartUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(restartBody)
                });

                const restartJson = await restartRes.json();

                if (restartJson.success) {
                    showToast('success', 'Stack restarted successfully');
                    closeComposeModal();
                    setTimeout(() => loadDocker(), 2000);
                } else {
                    showToast('error', 'Restart failed: ' + restartJson.error);
                }
            } catch (e) {
                showToast('error', 'Error: ' + e.message);
            }
        }
    );
}

function closeComposeModal(forceClose = false) {
    const hasChanges = document.getElementById('composeEditor').value !== originalComposeContent;

    if (hasChanges && composeEditMode && !forceClose) {
        showConfirmModal(
            'Unsaved Changes',
            'You have unsaved changes. Close anyway?',
            () => {
                document.getElementById('composeModal').classList.remove('active');
                composeEditMode = false;
            }
        );
        return;
    }

    document.getElementById('composeModal').classList.remove('active');
    composeEditMode = false;
}

async function viewEnvFile(filename) {
    // If in stack detail view, delegate to stack env viewer
    if (currentStackName) { viewStackEnvFile(currentStackName, filename); return; }

    currentEditingFile = `/mnt/archie_brain/${filename}`;
    composeEditMode = false;

    // Update modal title
    document.getElementById('composeModalTitle').innerHTML = `
        <i data-lucide="file-text" style="width: 20px; height: 20px; display: inline-block; vertical-align: middle; margin-right: 8px;"></i>
        ${filename}
    `;
    document.getElementById('composeViewer').style.display = 'block';
    document.getElementById('composeEditor').style.display = 'none';
    document.getElementById('composeEditToggle').style.display = 'inline-flex';
    document.getElementById('composeSaveBtn').style.display = 'none';
    document.getElementById('composeApplyBtn').style.display = 'none';
    document.getElementById('composeStatus').textContent = 'Loading...';

    // Show modal
    document.getElementById('composeModal').classList.add('active');
    lucide.createIcons();

    try {
        const res = await fetch(`/dashboard/api/stack/env?file=${encodeURIComponent(filename)}`);
        const json = await res.json();

        if (json.success) {
            // Mask sensitive values for display
            let displayContent = json.content;
            displayContent = displayContent.replace(/(PASSWORD|SECRET|KEY|TOKEN)=(.+)/gi, '$1=********');

            originalComposeContent = json.content;
            document.getElementById('composeViewer').querySelector('code').textContent = displayContent;
            document.getElementById('composeEditor').value = json.content;
            document.getElementById('composeStatus').textContent = `Sensitive values masked in view mode`;
        } else {
            document.getElementById('composeViewer').querySelector('code').textContent = 'Error: ' + json.error;
            document.getElementById('composeStatus').textContent = 'Failed to load';
        }
    } catch (e) {
        document.getElementById('composeViewer').querySelector('code').textContent = 'Error: ' + e.message;
        document.getElementById('composeStatus').textContent = 'Failed to load';
    }
}

function renderStackServices(services) {
    const grid = document.getElementById('stack-services-grid');
    if (!grid) return;

    if (!services || services.length === 0) {
        grid.innerHTML = '<div style="color: var(--archie-text-muted); padding: 10px;">No services defined</div>';
        return;
    }

    grid.innerHTML = services.map(s => `
        <div class="stack-service-card">
            <div class="service-info">
                <i data-lucide="${s.running ? 'circle-check' : 'circle-x'}"
                   style="width: 18px; height: 18px; color: ${s.running ? 'var(--archie-green)' : 'var(--archie-red)'};"></i>
                <div>
                    <div class="service-name">${s.name}</div>
                    <div class="service-image">${s.image || 'custom build'}</div>
                </div>
            </div>
            <span class="badge ${s.running ? 'running' : 'stopped'}">${s.running ? 'Running' : 'Stopped'}</span>
        </div>
    `).join('');

    lucide.createIcons();
}

// ============================================================================
// MULTI-STACK DETAIL VIEW (Tasks 8-11)
// ============================================================================

// Show stack overview, hide detail view
function showStackOverview() {
    currentStackName = null;
    document.getElementById('docker-stack-overview').style.display = '';
    document.getElementById('docker-stack-detail').classList.remove('active');
    loadDocker();
}

// Open stack detail view
function openStackDetail(stackName) {
    currentStackName = stackName;
    document.getElementById('docker-stack-overview').style.display = 'none';
    document.getElementById('docker-stack-detail').classList.add('active');
    // Activate containers sub-tab
    showStackSubtab('containers');
    loadStackDetail(stackName);
}

// Toggle sub-tabs in detail view
function showStackSubtab(tabName) {
    // Update tab buttons
    document.querySelectorAll('#stack-subtabs .stack-subtab').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.subtab === tabName);
    });
    // Update tab content
    document.querySelectorAll('.stack-subtab-content').forEach(el => {
        el.classList.toggle('active', el.id === `subtab-${tabName}`);
    });
    // Load data on demand
    if (tabName === 'compose') loadStackCompose();
    if (tabName === 'env') loadStackEnv();
    if (tabName === 'logs') loadStackLogs();
}

// Load stack detail data (containers + header)
// NOTE: All data comes from authenticated admin-only API endpoints, not user input.
async function loadStackDetail(stackName) {
    try {
        const res = await fetch(`/dashboard/api/stacks/${stackName}`);
        const json = await res.json();
        if (!json.success) {
            showToast('error', json.error || 'Failed to load stack');
            return;
        }
        const stack = json.data;

        // Update header
        document.getElementById('detail-stack-name').textContent = stack.display_name;
        const colorDot = document.getElementById('detail-stack-color');
        colorDot.style.background = stack.color || '#00D4AA';
        colorDot.style.width = '12px';
        colorDot.style.height = '12px';
        colorDot.style.borderRadius = '50%';
        colorDot.style.display = 'inline-block';

        document.getElementById('detail-stack-badge').innerHTML = stack.is_system
            ? '<span class="badge system" style="font-size: 0.7rem; text-transform: uppercase; letter-spacing: 1px;">SYSTEM</span>'
            : '';

        // Action buttons in header
        const actionsEl = document.getElementById('detail-stack-actions');
        actionsEl.innerHTML = `
            <button class="docker-action-btn" onclick="stackAction('up')">
                <i data-lucide="play" style="width: 14px; height: 14px;"></i> Start
            </button>
            <button class="docker-action-btn" onclick="stackAction('restart')">
                <i data-lucide="refresh-cw" style="width: 14px; height: 14px;"></i> Restart
            </button>
            <button class="docker-action-btn warning" onclick="stackAction('down')">
                <i data-lucide="square" style="width: 14px; height: 14px;"></i> Stop
            </button>
            ${!stack.is_system ? `<button class="docker-action-btn" style="color: var(--archie-red);" onclick="deleteStack('${stack.name}')">
                <i data-lucide="trash-2" style="width: 14px; height: 14px;"></i> Delete
            </button>` : ''}
        `;

        // Render containers
        renderStackContainers(stack.containers || []);

        // Populate logs service selector
        const logSelect = document.getElementById('stack-log-service');
        const currentVal = logSelect.value;
        logSelect.innerHTML = '<option value="">All services</option>';
        (stack.containers || []).forEach(c => {
            const svc = c.Service || c.Name || '';
            if (svc) {
                const opt = document.createElement('option');
                opt.value = svc;
                opt.textContent = svc;
                logSelect.appendChild(opt);
            }
        });
        logSelect.value = currentVal;

        if (typeof lucide !== 'undefined') lucide.createIcons();
    } catch (e) {
        showToast('error', 'Error loading stack: ' + e.message);
    }
}

// Render container cards in the detail view
// NOTE: Data from authenticated admin-only API, follows existing codebase template literal pattern
function renderStackContainers(containers) {
    const grid = document.getElementById('stack-container-grid');
    if (!containers.length) {
        grid.innerHTML = '<div class="loading-placeholder"><i data-lucide="inbox" style="width: 32px; height: 32px;"></i>No containers found</div>';
    } else {
        grid.innerHTML = containers.map(c => {
            const state = (c.State || '').toLowerCase();
            const statusClass = state === 'running' ? 'running' : (state === 'paused' ? 'paused' : 'stopped');
            const statusText = c.Status || c.State || 'Unknown';
            const name = c.Name || c.Service || 'Unknown';
            const image = c.Image || 'No image';
            const ports = c.Ports || '';
            const cId = c.ID || '';
            return `
            <div class="docker-container-card ${statusClass}">
                <div class="docker-card-header">
                    <div class="docker-card-name">${name}</div>
                    <span class="docker-card-status ${statusClass}">${statusText}</span>
                </div>
                <div class="docker-card-image">${image}</div>
                <div class="docker-card-meta">
                    ${cId ? `<span><i data-lucide="hash" style="width: 12px; height: 12px;"></i> ${cId.substring(0, 12)}</span>` : ''}
                    ${ports ? `<span><i data-lucide="globe" style="width: 12px; height: 12px;"></i> ${ports}</span>` : ''}
                </div>
                <div class="docker-card-actions">
                    ${state === 'running' ? `
                        <button class="docker-card-btn stop" onclick="dockerAction('${cId}', 'stop')">
                            <i data-lucide="square" style="width: 12px; height: 12px;"></i> Stop
                        </button>
                        <button class="docker-card-btn" onclick="dockerAction('${cId}', 'restart')">
                            <i data-lucide="refresh-cw" style="width: 12px; height: 12px;"></i> Restart
                        </button>
                        <button class="docker-card-btn" onclick="viewDockerLogs('${cId}', '${name}')">
                            <i data-lucide="file-text" style="width: 12px; height: 12px;"></i> Logs
                        </button>
                    ` : `
                        <button class="docker-card-btn start" onclick="dockerAction('${cId}', 'start')">
                            <i data-lucide="play" style="width: 12px; height: 12px;"></i> Start
                        </button>
                    `}
                </div>
            </div>`;
        }).join('');
    }
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

// Load compose file for current stack
async function loadStackCompose() {
    if (!currentStackName) return;
    try {
        const res = await fetch(`/dashboard/api/stacks/${currentStackName}/compose`);
        const json = await res.json();
        if (json.success) {
            document.getElementById('detail-compose-path').textContent = json.compose_path || '-';
            document.getElementById('detail-services-count').textContent = (json.services || []).length;
            renderDetailStackServices(json.services || []);
        }
    } catch (e) {
        console.error('Stack compose error:', e);
    }
}

// Render services grid in the detail compose sub-tab
// NOTE: Trusted admin-only API data, follows existing codebase pattern
function renderDetailStackServices(services) {
    const grid = document.getElementById('detail-services-grid');
    if (!grid) return;
    if (!services || !services.length) {
        grid.innerHTML = '<div style="color: var(--archie-text-muted); padding: 10px;">No services defined</div>';
        return;
    }
    grid.innerHTML = services.map(s => `
        <div class="stack-service-card">
            <div class="service-info">
                <i data-lucide="${s.running ? 'circle-check' : 'circle-x'}"
                   style="width: 18px; height: 18px; color: ${s.running ? 'var(--archie-green)' : 'var(--archie-red)'};"></i>
                <div>
                    <div class="service-name">${s.name}</div>
                    <div class="service-image">${s.image || 'custom build'}</div>
                </div>
            </div>
            <span class="badge ${s.running ? 'running' : 'stopped'}">${s.running ? 'Running' : 'Stopped'}</span>
        </div>
    `).join('');
    lucide.createIcons();
}

// Load environment files for current stack
async function loadStackEnv() {
    if (!currentStackName) return;
    const container = document.getElementById('detail-env-files');
    container.innerHTML = '<div class="loading-placeholder">Loading environment files...</div>';
    try {
        const res = await fetch(`/dashboard/api/stacks/${currentStackName}/env`);
        const json = await res.json();
        if (json.success) {
            const envFiles = json.data || [];
            if (!envFiles.length) {
                container.innerHTML = '<div style="color: var(--archie-text-muted); padding: 10px;">No environment files found</div>';
                return;
            }
            container.innerHTML = envFiles.map(f => `
                <div class="env-file-item" onclick="viewStackEnvFile('${currentStackName}', '${f.filename}')">
                    <i data-lucide="file-text" style="width: 16px; height: 16px;"></i>
                    <span>${f.filename}</span>
                    <span class="env-file-path">${f.path || ''}</span>
                </div>
            `).join('');
            lucide.createIcons();
        }
    } catch (e) {
        container.innerHTML = '<div style="color: var(--archie-red); padding: 10px;">Error loading env files</div>';
    }
}

// Load logs for current stack
async function loadStackLogs() {
    if (!currentStackName) return;
    const output = document.getElementById('stack-log-output');
    const service = document.getElementById('stack-log-service').value;
    const tail = document.getElementById('stack-log-tail').value;
    output.textContent = 'Loading logs...';
    try {
        let url = `/dashboard/api/stacks/${currentStackName}/logs?tail=${tail}`;
        if (service) url += `&service=${encodeURIComponent(service)}`;
        const res = await fetch(url);
        const text = await res.text();
        output.textContent = text || '(no logs)';
        // Auto-scroll to bottom
        output.scrollTop = output.scrollHeight;
    } catch (e) {
        output.textContent = 'Error loading logs: ' + e.message;
    }
}

// View compose file for a specific stack via the modal
async function viewStackCompose() {
    if (!currentStackName) return;
    currentEditingFile = null; // Will use stack endpoint
    composeEditMode = false;

    document.getElementById('composeModalTitle').innerHTML = `
        <i data-lucide="file-code" style="width: 20px; height: 20px; display: inline-block; vertical-align: middle; margin-right: 8px;"></i>
        docker-compose.yml
    `;
    document.getElementById('composeViewer').style.display = 'block';
    document.getElementById('composeEditor').style.display = 'none';
    document.getElementById('composeEditToggle').style.display = 'inline-flex';
    document.getElementById('composeEditToggle').innerHTML = '<i data-lucide="edit" style="width: 14px; height: 14px;"></i> Edit';
    document.getElementById('composeSaveBtn').style.display = 'none';
    document.getElementById('composeApplyBtn').style.display = 'none';
    document.getElementById('composeStatus').textContent = 'Loading...';
    document.getElementById('composeModal').classList.add('active');
    lucide.createIcons();

    try {
        const res = await fetch(`/dashboard/api/stacks/${currentStackName}/compose`);
        const json = await res.json();
        if (json.success) {
            originalComposeContent = json.content;
            document.getElementById('composeViewer').querySelector('code').textContent = json.content;
            document.getElementById('composeEditor').value = json.content;
            document.getElementById('composeStatus').textContent = `Last modified: ${json.modified || 'Unknown'}`;
        } else {
            document.getElementById('composeViewer').querySelector('code').textContent = 'Error: ' + json.error;
            document.getElementById('composeStatus').textContent = 'Failed to load';
        }
    } catch (e) {
        document.getElementById('composeViewer').querySelector('code').textContent = 'Error: ' + e.message;
        document.getElementById('composeStatus').textContent = 'Failed to load';
    }
}

// Edit compose for current stack
function editStackCompose() {
    viewStackCompose().then(() => {
        setTimeout(() => toggleComposeEdit(), 100);
    });
}

// View env file for a specific stack via the modal
async function viewStackEnvFile(stackName, filename) {
    currentEditingFile = null; // Will use stack endpoint
    composeEditMode = false;

    document.getElementById('composeModalTitle').innerHTML = `
        <i data-lucide="file-text" style="width: 20px; height: 20px; display: inline-block; vertical-align: middle; margin-right: 8px;"></i>
        ${filename}
    `;
    document.getElementById('composeViewer').style.display = 'block';
    document.getElementById('composeEditor').style.display = 'none';
    document.getElementById('composeEditToggle').style.display = 'inline-flex';
    document.getElementById('composeSaveBtn').style.display = 'none';
    document.getElementById('composeApplyBtn').style.display = 'none';
    document.getElementById('composeStatus').textContent = 'Loading...';
    document.getElementById('composeModal').classList.add('active');
    lucide.createIcons();

    try {
        const res = await fetch(`/dashboard/api/stacks/${stackName}/env`);
        const json = await res.json();
        if (json.success) {
            // Find the specific env file
            const envFiles = json.data || [];
            const file = envFiles.find(f => f.filename === filename);
            if (file) {
                // Mask sensitive values for display
                let displayContent = file.content || '';
                displayContent = displayContent.replace(/(PASSWORD|SECRET|KEY|TOKEN)=(.+)/gi, '$1=********');
                originalComposeContent = file.content;
                document.getElementById('composeViewer').querySelector('code').textContent = displayContent;
                document.getElementById('composeEditor').value = file.content;
                document.getElementById('composeStatus').textContent = 'Sensitive values masked in view mode';
            } else {
                document.getElementById('composeViewer').querySelector('code').textContent = 'File not found: ' + filename;
                document.getElementById('composeStatus').textContent = 'Failed to load';
            }
        } else {
            document.getElementById('composeViewer').querySelector('code').textContent = 'Error: ' + json.error;
        }
    } catch (e) {
        document.getElementById('composeViewer').querySelector('code').textContent = 'Error: ' + e.message;
    }
}

// ============================================================================
// STACK ACTIONS (Two-Tier Safety)
// ============================================================================

// Quick action from overview card
function quickStackAction(stackName, action, isSystem) {
    const destructive = ['down', 'restart', 'pull'].includes(action);
    if (isSystem && destructive) {
        showReauthModal(`${action} system stack`, async () => {
            await executeStackAction(stackName, action);
        });
    } else {
        showConfirmModal('Stack Action', `${action} stack "${stackName}"?`, async () => {
            await executeStackAction(stackName, action);
        });
    }
}

// Action from detail view sub-tab
function stackAction(action) {
    if (!currentStackName) return;
    const stack = stacksData.find(s => s.name === currentStackName);
    const isSystem = stack ? !!stack.is_system : false;
    quickStackAction(currentStackName, action, isSystem);
}

// Execute the actual stack action API call
async function executeStackAction(stackName, action) {
    showToast('success', `Executing ${action} on ${stackName}...`);
    try {
        const res = await fetch(`/dashboard/api/stacks/${stackName}/action`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, confirm: true })
        });
        const json = await res.json();

        if (json.requires_reauth) {
            showReauthModal(`${action} stack`, async () => {
                await executeStackAction(stackName, action);
            });
            return;
        }

        if (json.success) {
            showToast('success', `Stack ${action} completed`);
            setTimeout(() => loadDocker(), 2000);
        } else {
            showToast('error', json.error || `Failed to ${action}`);
        }
    } catch (e) {
        showToast('error', 'Stack action error: ' + e.message);
    }
}

// Delete a non-system stack
function deleteStack(stackName) {
    showConfirmModal('Delete Stack', `Are you sure you want to delete stack "${stackName}"? This will stop all containers and remove the stack from management.`, async () => {
        try {
            const res = await fetch(`/dashboard/api/stacks/${stackName}`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ confirm: true })
            });
            const json = await res.json();
            if (json.success) {
                showToast('success', 'Stack deleted');
                showStackOverview();
            } else {
                showToast('error', json.error || 'Failed to delete stack');
            }
        } catch (e) {
            showToast('error', 'Delete error: ' + e.message);
        }
    });
}

// ============================================================================
// STACK CREATION WIZARD (Task 10)
// ============================================================================

let wizardStep = 1;
let wizardColor = '#00D4AA';

function openCreateStackWizard() {
    wizardStep = 1;
    wizardColor = '#00D4AA';
    // Reset fields
    document.getElementById('wizard-name').value = '';
    document.getElementById('wizard-display-name').value = '';
    document.getElementById('wizard-description').value = '';
    document.getElementById('wizard-compose').value = '';
    document.getElementById('wizard-env').value = '';
    document.getElementById('wizard-dir-preview').textContent = '/mnt/stacks/';
    // Reset color selection
    document.querySelectorAll('#wizard-color-picker .color-swatch').forEach((sw, i) => {
        sw.classList.toggle('selected', i === 0);
    });
    updateWizardStepUI();
    document.getElementById('createStackWizard').classList.add('active');
    lucide.createIcons();
}

function closeCreateStackWizard() {
    document.getElementById('createStackWizard').classList.remove('active');
}

function updateWizardSlug() {
    const raw = document.getElementById('wizard-name').value;
    const slug = raw.toLowerCase().replace(/[^a-z0-9-]/g, '').replace(/^-+|-+$/g, '');
    document.getElementById('wizard-name').value = slug;
    document.getElementById('wizard-dir-preview').textContent = `/mnt/stacks/${slug}`;
}

function selectWizardColor(el) {
    document.querySelectorAll('#wizard-color-picker .color-swatch').forEach(sw => sw.classList.remove('selected'));
    el.classList.add('selected');
    wizardColor = el.dataset.color;
}

function updateWizardStepUI() {
    // Show/hide step content
    for (let i = 1; i <= 4; i++) {
        const stepEl = document.getElementById(`wizard-step-${i}`);
        if (stepEl) stepEl.style.display = i === wizardStep ? '' : 'none';
    }
    // Update step indicators
    document.querySelectorAll('#wizard-steps .wizard-step-indicator').forEach(ind => {
        const step = parseInt(ind.dataset.step);
        ind.classList.toggle('active', step === wizardStep);
        ind.classList.toggle('completed', step < wizardStep);
    });
    // Update step lines
    const lines = document.querySelectorAll('#wizard-steps .wizard-step-line');
    lines.forEach((line, i) => {
        line.classList.toggle('completed', (i + 1) < wizardStep);
    });
    // Previous button
    document.getElementById('wizard-prev-btn').style.display = wizardStep > 1 ? '' : 'none';
    // Next button text
    const nextBtn = document.getElementById('wizard-next-btn');
    if (wizardStep === 4) {
        nextBtn.innerHTML = '<i data-lucide="check" style="width: 14px; height: 14px;"></i> Create Stack';
    } else {
        nextBtn.innerHTML = 'Next <i data-lucide="arrow-right" style="width: 14px; height: 14px;"></i>';
    }
    lucide.createIcons();
    // On step 4, populate review
    if (wizardStep === 4) updateWizardReview();
}

function wizardNext() {
    // Validate current step
    if (wizardStep === 1) {
        const name = document.getElementById('wizard-name').value.trim();
        const displayName = document.getElementById('wizard-display-name').value.trim();
        if (!name) { showToast('error', 'Stack name is required'); return; }
        if (!displayName) { showToast('error', 'Display name is required'); return; }
        if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(name) && name.length > 1) {
            showToast('error', 'Name must be lowercase alphanumeric with hyphens only');
            return;
        }
        if (name.length === 1 && !/^[a-z0-9]$/.test(name)) {
            showToast('error', 'Name must be lowercase alphanumeric');
            return;
        }
    }
    if (wizardStep === 4) {
        createStack();
        return;
    }
    wizardStep++;
    updateWizardStepUI();
}

function wizardPrev() {
    if (wizardStep > 1) {
        wizardStep--;
        updateWizardStepUI();
    }
}

function updateWizardReview() {
    const name = document.getElementById('wizard-name').value;
    const displayName = document.getElementById('wizard-display-name').value;
    const desc = document.getElementById('wizard-description').value;
    const compose = document.getElementById('wizard-compose').value;
    const env = document.getElementById('wizard-env').value;
    const reviewEl = document.getElementById('wizard-review-content');
    reviewEl.innerHTML = `
        <div style="margin-bottom: 8px;"><strong>Name:</strong> ${displayName} <span style="color: var(--archie-text-muted);">(${name})</span></div>
        ${desc ? `<div style="margin-bottom: 8px;"><strong>Description:</strong> ${desc}</div>` : ''}
        <div style="margin-bottom: 8px;"><strong>Directory:</strong> <code>/mnt/stacks/${name}</code></div>
        <div style="margin-bottom: 8px;"><strong>Color:</strong> <span style="display:inline-block;width:14px;height:14px;border-radius:50%;background:${wizardColor};vertical-align:middle;"></span> ${wizardColor}</div>
        <div style="margin-bottom: 8px;"><strong>Compose:</strong> ${compose ? `${compose.split('\n').length} lines` : '<span style="color:var(--archie-text-muted);">Not provided</span>'}</div>
        <div><strong>Environment:</strong> ${env ? `${env.split('\n').length} lines` : '<span style="color:var(--archie-text-muted);">Not provided</span>'}</div>
    `;
}

async function createStack() {
    const name = document.getElementById('wizard-name').value.trim();
    const display_name = document.getElementById('wizard-display-name').value.trim();
    const description = document.getElementById('wizard-description').value.trim();
    const compose_content = document.getElementById('wizard-compose').value;
    const env_content = document.getElementById('wizard-env').value;

    try {
        const res = await fetch('/dashboard/api/stacks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                display_name,
                description,
                color: wizardColor,
                compose_content,
                env_content
            })
        });
        const json = await res.json();
        if (json.success) {
            showToast('success', `Stack "${display_name}" created`);
            closeCreateStackWizard();
            loadDocker();
        } else {
            showToast('error', json.error || 'Failed to create stack');
        }
    } catch (e) {
        showToast('error', 'Create error: ' + e.message);
    }
}

// ============================================================================
// FIREWALL MANAGEMENT FUNCTIONS
// ============================================================================

async function loadFirewallData(retryCount = 0) {
    const maxRetries = 2;
    const retryDelay = 3000; // 3 seconds between retries

    // Load cached data immediately for instant display (only on first attempt)
    const hasCached = retryCount === 0 ? loadCachedFirewallData() : false;

    // If we have cached data, show "refreshing" indicator but don't block UI
    if (hasCached && retryCount === 0) {
        document.getElementById('firewall-status-label').textContent += ' (refreshing...)';
    }

    // Update UI to show retry status
    if (retryCount > 0) {
        document.getElementById('firewall-status-label').textContent = 'Retrying... (' + retryCount + '/' + maxRetries + ')';
    }

    // Set a timeout to show fallback if API takes too long
    const fallbackTimeout = setTimeout(() => {
        if (!hasCached && retryCount === 0) {
            document.getElementById('firewall-status-value').textContent = 'Loading...';
            document.getElementById('firewall-status-label').textContent = 'Waiting for host response (may take up to 30s)';
        }
    }, 2000);

    try {
        // Fetch fresh data - use AbortController for timeout
        const controller = new AbortController();
        const fetchTimeout = setTimeout(() => controller.abort(), 35000); // 35s client timeout

        const [statusRes, rulesRes] = await Promise.all([
            fetch('/dashboard/api/firewall/status', { signal: controller.signal }),
            fetch('/dashboard/api/firewall/rules', { signal: controller.signal })
        ]);

        clearTimeout(fetchTimeout);
        clearTimeout(fallbackTimeout);

        const statusData = await statusRes.json();
        const rulesData = await rulesRes.json();

        // Check if we got valid data or timeout error
        if (!statusData.success && statusData.error && statusData.error.includes('timeout')) {
            throw new Error('Server timeout');
        }

        // Apply fresh data to UI
        applyFirewallStatus(statusData);
        const fromConfig = rulesData.from_config || false;
        renderFirewallRules(rulesData.success ? rulesData.rules : [], fromConfig);

        // Cache the fresh data
        saveFirewallCache(statusData, rulesData);

        lucide.createIcons();
    } catch (e) {
        clearTimeout(fallbackTimeout);
        console.error('Firewall load error (attempt ' + (retryCount + 1) + '):', e);

        // Retry if we haven't exceeded max retries and don't have cached data
        if (retryCount < maxRetries && !hasCached) {
            console.log('Retrying firewall load in ' + (retryDelay/1000) + 's...');
            document.getElementById('firewall-status-value').textContent = 'Retrying...';
            document.getElementById('firewall-status-value').style.color = 'var(--archie-yellow)';
            document.getElementById('firewall-status-label').textContent = 'Attempt ' + (retryCount + 1) + ' failed, retrying...';

            setTimeout(() => {
                loadFirewallData(retryCount + 1);
            }, retryDelay);
            return;
        }

        // All retries exhausted or we have cached data
        const cached = localStorage.getItem('archie_firewall_cache');
        if (!cached) {
            document.getElementById('firewall-status-value').textContent = 'Error';
            document.getElementById('firewall-status-value').style.color = 'var(--archie-yellow)';
            document.getElementById('firewall-status-label').textContent = 'Could not load - click Refresh to retry';
            document.getElementById('firewall-rules-count').textContent = '--';
            document.getElementById('firewall-rules-table').innerHTML = '';
            document.getElementById('firewall-no-rules').style.display = 'block';
        } else if (hasCached) {
            // We have cached data showing, just remove the "refreshing" note
            const label = document.getElementById('firewall-status-label');
            label.textContent = label.textContent.replace(' (refreshing...)', '') + ' (cached)';
        }
    }
}

function loadCachedFirewallData() {
    try {
        const cached = localStorage.getItem('archie_firewall_cache');
        if (cached) {
            const data = JSON.parse(cached);
            // Apply cached status
            if (data.status) {
                applyFirewallStatus(data.status);
            }
            // Apply cached rules
            if (data.rules) {
                renderFirewallRules(data.rules.rules || [], data.rules.from_config || false);
            }
            console.log('Loaded cached firewall data');
            return true;
        }
    } catch (e) {
        console.log('No cached firewall data available');
    }
    return false;
}

function saveFirewallCache(statusData, rulesData) {
    try {
        localStorage.setItem('archie_firewall_cache', JSON.stringify({
            status: statusData,
            rules: rulesData,
            timestamp: Date.now()
        }));
    } catch (e) {
        console.error('Failed to cache firewall data:', e);
    }
}

function applyFirewallStatus(statusData) {
    const statusValue = document.getElementById('firewall-status-value');
    const statusLabel = document.getElementById('firewall-status-label');
    const enableBtn = document.getElementById('firewall-enable-btn');
    const disableBtn = document.getElementById('firewall-disable-btn');

    if (statusData.success) {
        if (statusData.active) {
            statusValue.textContent = 'Active';
            statusValue.style.color = 'var(--archie-green)';
            statusLabel.textContent = 'Firewall is protecting your system';
            enableBtn.disabled = true;
            enableBtn.style.opacity = '0.5';
            disableBtn.disabled = false;
            disableBtn.style.opacity = '1';
        } else {
            statusValue.textContent = 'Inactive';
            statusValue.style.color = 'var(--archie-red)';
            statusLabel.textContent = 'All ports are exposed';
            enableBtn.disabled = false;
            enableBtn.style.opacity = '1';
            disableBtn.disabled = true;
            disableBtn.style.opacity = '0.5';
        }
    } else {
        statusValue.textContent = 'Unknown';
        statusValue.style.color = 'var(--archie-yellow)';
        statusLabel.textContent = statusData.error || 'Could not determine status';
    }
}

function renderFirewallRules(rules, fromConfig) {
    const tbody = document.getElementById('firewall-rules-table');
    const noRulesDiv = document.getElementById('firewall-no-rules');
    const rulesCount = document.getElementById('firewall-rules-count');
    const configBadge = document.getElementById('firewall-from-config-badge');

    rulesCount.textContent = rules.length;
    tbody.textContent = '';  // Clear existing

    // Show/hide "from config" badge
    if (configBadge) {
        configBadge.style.display = fromConfig ? 'inline' : 'none';
    }

    if (!rules || rules.length === 0) {
        noRulesDiv.style.display = 'block';
        tbody.parentElement.style.display = 'none';
        return;
    }

    noRulesDiv.style.display = 'none';
    tbody.parentElement.style.display = '';

    rules.forEach(rule => {
        const tr = document.createElement('tr');

        // Rule number
        const tdNum = document.createElement('td');
        tdNum.textContent = rule.number;
        tdNum.style.fontFamily = 'monospace';
        tdNum.style.color = 'var(--archie-text-muted)';
        tr.appendChild(tdNum);

        // Port/Service
        const tdPort = document.createElement('td');
        tdPort.textContent = rule.port;
        tdPort.style.fontFamily = 'monospace';
        tdPort.style.fontWeight = '600';
        tr.appendChild(tdPort);

        // Action badge
        const tdAction = document.createElement('td');
        const actionBadge = document.createElement('span');
        actionBadge.textContent = rule.action;
        actionBadge.style.cssText = 'padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600;';
        if (rule.action === 'ALLOW') {
            actionBadge.style.background = 'rgba(16, 185, 129, 0.2)';
            actionBadge.style.color = 'var(--archie-green)';
        } else if (rule.action === 'DENY' || rule.action === 'REJECT') {
            actionBadge.style.background = 'rgba(239, 68, 68, 0.2)';
            actionBadge.style.color = 'var(--archie-red)';
        } else if (rule.action === 'LIMIT') {
            actionBadge.style.background = 'rgba(251, 191, 36, 0.2)';
            actionBadge.style.color = 'var(--archie-yellow)';
        }
        tdAction.appendChild(actionBadge);
        tr.appendChild(tdAction);

        // Direction
        const tdDir = document.createElement('td');
        tdDir.textContent = rule.direction || 'IN';
        tr.appendChild(tdDir);

        // From
        const tdFrom = document.createElement('td');
        tdFrom.textContent = rule.from || 'Anywhere';
        tr.appendChild(tdFrom);

        // Comment/Service
        const tdComment = document.createElement('td');
        tdComment.textContent = rule.comment || '';
        tdComment.style.color = 'var(--archie-text-muted)';
        tdComment.style.fontSize = '0.85rem';
        tr.appendChild(tdComment);

        // Actions - Edit and Delete buttons
        const tdActions = document.createElement('td');
        tdActions.style.cssText = 'display: flex; gap: 4px;';

        // Edit button
        const editBtn = document.createElement('button');
        editBtn.className = 'btn btn-secondary';
        editBtn.style.cssText = 'padding: 4px 8px; font-size: 0.75rem;';
        editBtn.title = 'Edit rule';
        const editIcon = document.createElement('i');
        editIcon.setAttribute('data-lucide', 'edit');
        editIcon.style.cssText = 'width: 12px; height: 12px;';
        editBtn.appendChild(editIcon);
        editBtn.onclick = () => openEditRuleModal(rule);
        tdActions.appendChild(editBtn);

        // Delete button
        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'btn btn-danger';
        deleteBtn.style.cssText = 'padding: 4px 8px; font-size: 0.75rem;';
        deleteBtn.title = 'Delete rule';
        const deleteIcon = document.createElement('i');
        deleteIcon.setAttribute('data-lucide', 'trash-2');
        deleteIcon.style.cssText = 'width: 12px; height: 12px;';
        deleteBtn.appendChild(deleteIcon);
        deleteBtn.onclick = () => confirmDeleteRule(rule.number, rule.port);
        tdActions.appendChild(deleteBtn);

        tr.appendChild(tdActions);

        tbody.appendChild(tr);
    });
}

async function enableFirewall(retryCount = 0) {
    const maxRetries = 2;
    const btn = document.getElementById('firewall-enable-btn');

    try {
        btn.disabled = true;
        btn.innerHTML = '<i data-lucide="loader" style="width: 14px; height: 14px; animation: spin 1s linear infinite;"></i> Enabling...';

        if (retryCount === 0) {
            showToast('info', 'Enabling firewall...');
        }

        const res = await fetch('/dashboard/api/firewall/enable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();

        if (data.requires_reauth) {
            btn.disabled = false;
            btn.innerHTML = '<i data-lucide="power" style="width: 14px; height: 14px;"></i> Enable';
            lucide.createIcons();
            requestReauth('Enable Firewall', () => enableFirewall());
            return;
        }

        if (data.success) {
            showToast('success', data.message || 'Firewall enabled');
            // Reset button and reload data
            btn.disabled = false;
            btn.innerHTML = '<i data-lucide="power" style="width: 14px; height: 14px;"></i> Enable';
            lucide.createIcons();
            loadFirewallData(0);
        } else {
            // Check if it's a timeout and we can retry
            if ((data.error && data.error.includes('timeout')) && retryCount < maxRetries) {
                showToast('warning', 'Request timed out, retrying... (' + (retryCount + 1) + '/' + maxRetries + ')');
                setTimeout(() => enableFirewall(retryCount + 1), 2000);
                return;
            }
            btn.disabled = false;
            btn.innerHTML = '<i data-lucide="power" style="width: 14px; height: 14px;"></i> Enable';
            lucide.createIcons();
            showToast('error', data.error || 'Failed to enable firewall');
        }
    } catch (e) {
        // Network error - retry
        if (retryCount < maxRetries) {
            showToast('warning', 'Connection error, retrying... (' + (retryCount + 1) + '/' + maxRetries + ')');
            setTimeout(() => enableFirewall(retryCount + 1), 2000);
            return;
        }
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="power" style="width: 14px; height: 14px;"></i> Enable';
        lucide.createIcons();
        showToast('error', 'Error enabling firewall: ' + e.message);
    }
}

function confirmDisableFirewall() {
    document.getElementById('firewallDisableModal').classList.add('active');
}

function closeFirewallDisableModal() {
    document.getElementById('firewallDisableModal').classList.remove('active');
}

async function disableFirewall(retryCount = 0) {
    closeFirewallDisableModal();
    const maxRetries = 2;
    const btn = document.getElementById('firewall-disable-btn');

    try {
        btn.disabled = true;
        btn.innerHTML = '<i data-lucide="loader" style="width: 14px; height: 14px; animation: spin 1s linear infinite;"></i> Disabling...';

        if (retryCount === 0) {
            showToast('info', 'Disabling firewall...');
        }

        const res = await fetch('/dashboard/api/firewall/disable', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();

        if (data.requires_reauth) {
            btn.disabled = false;
            btn.innerHTML = '<i data-lucide="power-off" style="width: 14px; height: 14px;"></i> Disable';
            lucide.createIcons();
            requestReauth('Disable Firewall', () => disableFirewall());
            return;
        }

        if (data.success) {
            showToast('success', data.message || 'Firewall disabled');
            btn.disabled = false;
            btn.innerHTML = '<i data-lucide="power-off" style="width: 14px; height: 14px;"></i> Disable';
            lucide.createIcons();
            loadFirewallData(0);
        } else {
            // Check if it's a timeout and we can retry
            if ((data.error && data.error.includes('timeout')) && retryCount < maxRetries) {
                showToast('warning', 'Request timed out, retrying... (' + (retryCount + 1) + '/' + maxRetries + ')');
                setTimeout(() => disableFirewall(retryCount + 1), 2000);
                return;
            }
            btn.disabled = false;
            btn.innerHTML = '<i data-lucide="power-off" style="width: 14px; height: 14px;"></i> Disable';
            lucide.createIcons();
            showToast('error', data.error || 'Failed to disable firewall');
        }
    } catch (e) {
        // Network error - retry
        if (retryCount < maxRetries) {
            showToast('warning', 'Connection error, retrying... (' + (retryCount + 1) + '/' + maxRetries + ')');
            setTimeout(() => disableFirewall(retryCount + 1), 2000);
            return;
        }
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="power-off" style="width: 14px; height: 14px;"></i> Disable';
        lucide.createIcons();
        showToast('error', 'Error disabling firewall: ' + e.message);
    }
}

function openAddRuleModal() {
    document.getElementById('firewall-port-input').value = '';
    document.getElementById('firewall-protocol-select').value = 'tcp';
    document.getElementById('firewall-action-select').value = 'allow';
    document.getElementById('firewall-add-rule-warning').style.display = 'none';
    document.getElementById('firewallAddRuleModal').classList.add('active');
    document.getElementById('firewall-port-input').focus();
}

function closeAddRuleModal() {
    document.getElementById('firewallAddRuleModal').classList.remove('active');
}

async function addFirewallRule() {
    const portInput = document.getElementById('firewall-port-input');
    const port = parseInt(portInput.value, 10);
    const protocol = document.getElementById('firewall-protocol-select').value;
    const action = document.getElementById('firewall-action-select').value;

    // Validate port
    if (!port || port < 1 || port > 65535) {
        showToast('error', 'Invalid port number. Must be 1-65535.');
        portInput.focus();
        return;
    }

    // Warn about critical ports
    const warningDiv = document.getElementById('firewall-add-rule-warning');
    const warningText = document.getElementById('firewall-add-rule-warning-text');
    if (action === 'deny' && (port === 22 || port === 3000)) {
        const portName = port === 22 ? 'SSH' : 'platform';
        warningText.textContent = 'Blocking port ' + port + ' (' + portName + ') may lock you out!';
        warningDiv.style.display = 'block';
    }

    closeAddRuleModal();
    showToast('info', 'Adding firewall rule for ' + port + '/' + protocol + '...');

    try {
        const endpoint = action === 'allow' ? '/api/firewall/allow' : '/api/firewall/deny';
        const res = await fetch('/dashboard' + endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ port, protocol })
        });
        const data = await res.json();

        if (data.success) {
            showToast('success', data.message || 'Rule added: ' + action + ' ' + port + '/' + protocol);
            loadFirewallData();
        } else {
            showToast('error', data.error || 'Failed to add rule');
        }
    } catch (e) {
        showToast('error', 'Error adding rule: ' + e.message);
    }
}

function openEditRuleModal(rule) {
    // Parse port/protocol from rule.port (e.g., "22/tcp")
    const parts = rule.port.split('/');
    const port = parseInt(parts[0], 10);
    const protocol = parts[1] || 'tcp';
    const action = rule.action.toLowerCase();

    // Store old values for the update
    document.getElementById('firewall-edit-old-port').value = port;
    document.getElementById('firewall-edit-old-protocol').value = protocol;

    // Set current values in form
    document.getElementById('firewall-edit-port-input').value = port;
    document.getElementById('firewall-edit-protocol-select').value = protocol;
    document.getElementById('firewall-edit-action-select').value = (action === 'allow' || action === 'deny') ? action : 'allow';

    document.getElementById('firewallEditRuleModal').classList.add('active');
    document.getElementById('firewall-edit-port-input').focus();
}

function closeEditRuleModal() {
    document.getElementById('firewallEditRuleModal').classList.remove('active');
}

async function updateFirewallRule() {
    const oldPort = parseInt(document.getElementById('firewall-edit-old-port').value, 10);
    const oldProtocol = document.getElementById('firewall-edit-old-protocol').value;
    const newPort = parseInt(document.getElementById('firewall-edit-port-input').value, 10);
    const newProtocol = document.getElementById('firewall-edit-protocol-select').value;
    const action = document.getElementById('firewall-edit-action-select').value;

    // Validate new port
    if (!newPort || newPort < 1 || newPort > 65535) {
        showToast('error', 'Invalid port number. Must be 1-65535.');
        document.getElementById('firewall-edit-port-input').focus();
        return;
    }

    closeEditRuleModal();
    showToast('info', 'Updating firewall rule...');

    try {
        const res = await fetch('/dashboard/api/firewall/update', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                old_port: oldPort,
                old_protocol: oldProtocol,
                new_port: newPort,
                new_protocol: newProtocol,
                action: action
            })
        });
        const data = await res.json();

        if (data.success) {
            showToast('success', data.message || 'Rule updated successfully');
            loadFirewallData();
        } else {
            showToast('error', data.error || 'Failed to update rule');
        }
    } catch (e) {
        showToast('error', 'Error updating rule: ' + e.message);
    }
}

function confirmDeleteRule(ruleNumber, portInfo) {
    pendingDeleteRuleNumber = ruleNumber;
    document.getElementById('firewall-delete-rule-details').textContent = 'Rule #' + ruleNumber + ': ' + portInfo;
    document.getElementById('firewallDeleteModal').classList.add('active');
}

function closeDeleteRuleModal() {
    document.getElementById('firewallDeleteModal').classList.remove('active');
    pendingDeleteRuleNumber = null;
}

async function deleteFirewallRule() {
    const ruleNumber = pendingDeleteRuleNumber;
    closeDeleteRuleModal();

    if (!ruleNumber) {
        showToast('error', 'No rule selected for deletion');
        return;
    }

    showToast('info', 'Deleting firewall rule #' + ruleNumber + '...');

    try {
        const res = await fetch('/dashboard/api/firewall/delete', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rule_number: ruleNumber })
        });
        const data = await res.json();

        if (data.requires_reauth) {
            requestReauth('Delete Firewall Rule', () => {
                pendingDeleteRuleNumber = ruleNumber;
                deleteFirewallRule();
            });
            return;
        }

        if (data.success) {
            showToast('success', data.message || 'Rule #' + ruleNumber + ' deleted');
            loadFirewallData();
        } else {
            showToast('error', data.error || 'Failed to delete rule');
        }
    } catch (e) {
        showToast('error', 'Error deleting rule: ' + e.message);
    }
}
