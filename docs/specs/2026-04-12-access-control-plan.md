# KSO Access Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate KSM features by subscription tier — free users get read-only dashboard with frosted teasers on locked tabs, local admins get Pro-equivalent, OAuth subscribers get tier from ARCHIE.

**Architecture:** Wire existing `subscription_service.py` tier resolution + `middleware/tier_gate.py` decorators into the template layer via context processor. Add frosted CSS overlays and JS tier checking client-side. Consolidate dashboard to remove duplicate sections.

**Tech Stack:** Flask (Python), Jinja2 templates, vanilla JS, CSS backdrop-filter

---

### Task 1: Tier Context Injection

**Files:**
- Modify: `kytran_system_operations/theme.py` — add tier info to context processor
- Modify: `kytran_system_operations/auth.py` — set local admin tier on login
- Modify: `kytran_system_operations/templates/base.html` — add `window.KSO_TIER` JS global

- [ ] **Step 1: Update context processor in theme.py**

Add user tier resolution to the existing `inject_sysops_theme` context processor. Import `get_user_tier` and `tier_at_least` from subscription_service. Return `user_tier` and a `user_tier_at_least` callable in the template context.

```python
# In init_theme(), update the context processor:
@app.context_processor
def inject_sysops_theme():
    current_name = os.environ.get("SYSOPS_THEME", DEFAULT_THEME)
    current_theme = load_theme(current_name)
    
    # Resolve user tier
    from flask_login import current_user
    from .services.subscription_service import get_user_tier, tier_at_least
    user_tier = "free"
    if current_user and current_user.is_authenticated:
        user_tier = get_user_tier(current_user.id)
    
    return {
        "sysops_theme": current_theme,
        "user_tier": user_tier,
        "user_tier_at_least": lambda min_tier: tier_at_least(user_tier, min_tier),
    }
```

- [ ] **Step 2: Set local admin default tier to Pro**

In `auth.py`, after creating admin via setup or after local login, if user has role `admin` and no subscription record exists, auto-insert a `pro` subscription:

```python
# In verify_password(), after successful login:
from .services.subscription_service import get_user_tier, set_user_tier
if user.is_admin and get_user_tier(user.id) == "free":
    set_user_tier(user.id, "pro")
```

Also in `create_admin()`:
```python
from .services.subscription_service import set_user_tier
# After creating admin user, get the new user's id and set tier
row = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
if row:
    set_user_tier(row["id"], "pro")
```

- [ ] **Step 3: Add window.KSO_TIER to base.html**

In `templates/base.html`, before the closing `</head>` tag, add:

```html
<script>
    window.KSO_TIER = "{{ user_tier | default('free') }}";
    window.KSO_TIER_AT_LEAST = function(minTier) {
        var levels = {free: 0, pro: 1, business: 2, enterprise: 3};
        return (levels[window.KSO_TIER] || 0) >= (levels[minTier] || 0);
    };
</script>
```

- [ ] **Step 4: Verify tier injection works**

Rebuild container, login as admin, open browser console:
```
console.log(window.KSO_TIER)  // Should print "pro"
console.log(window.KSO_TIER_AT_LEAST('pro'))  // true
console.log(window.KSO_TIER_AT_LEAST('business'))  // false
```

- [ ] **Step 5: Commit**

```bash
git add kytran_system_operations/theme.py kytran_system_operations/auth.py kytran_system_operations/templates/base.html
git commit -m "feat: inject user tier into template context + JS global"
```

---

### Task 2: Frosted Overlay CSS

**Files:**
- Modify: `kytran_system_operations/static/css/system-operations.css` — add tier-locked overlay styles

- [ ] **Step 1: Add frosted overlay CSS**

Add after the `/* --- Modern Frame --- */` section in `system-operations.css`:

```css
/* --- Tier Gating — Frosted Teasers --- */
.tier-tab-wrapper {
    position: relative;
}

.tier-locked-overlay {
    position: absolute;
    inset: 0;
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    background: rgba(10, 10, 26, 0.85);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 50;
    border-radius: var(--sysops-border-radius, 8px);
    animation: fadeIn 0.3s ease-out;
}

.tier-locked-card {
    text-align: center;
    max-width: 400px;
    padding: 40px 32px;
}

.tier-locked-icon {
    width: 48px;
    height: 48px;
    margin: 0 auto 16px;
    color: var(--archie-accent);
    opacity: 0.8;
}

.tier-locked-title {
    font-family: var(--sysops-font-heading, 'Inter'), sans-serif;
    font-size: 1.3rem;
    font-weight: 700;
    color: var(--archie-text-primary);
    margin-bottom: 12px;
}

.tier-locked-features {
    list-style: none;
    padding: 0;
    margin: 0 0 24px;
    text-align: left;
}

.tier-locked-features li {
    color: var(--archie-text-secondary);
    font-size: 0.9rem;
    padding: 6px 0;
    display: flex;
    align-items: center;
    gap: 8px;
}

.tier-locked-cta {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 12px 28px;
    background: var(--archie-accent);
    color: var(--archie-bg-primary);
    border: none;
    border-radius: var(--archie-radius-md);
    font-weight: 600;
    font-size: 0.95rem;
    cursor: pointer;
    transition: background 0.2s;
    text-decoration: none;
}

.tier-locked-cta:hover {
    filter: brightness(1.15);
}

.tier-locked-signin {
    display: block;
    margin-top: 12px;
    color: var(--archie-text-muted);
    font-size: 0.8rem;
}

.tier-locked-signin a {
    color: var(--archie-accent);
    text-decoration: none;
}

/* Hide action buttons for free tier */
.tier-action-locked {
    display: none;
}
```

- [ ] **Step 2: Commit**

```bash
git add kytran_system_operations/static/css/system-operations.css
git commit -m "style: add frosted teaser overlay CSS for tier gating"
```

---

### Task 3: JS Tier Gating Logic

**Files:**
- Modify: `kytran_system_operations/static/js/system-operations.js` — add tier overlay logic on tab switch

- [ ] **Step 1: Add tab teaser definitions**

At the top of `system-operations.js`, after the ArchieTime stub, add tab feature descriptions and overlay builder using safe DOM methods (no innerHTML — use createElement + textContent for security):

```javascript
// Tier gating — frosted tab teasers
var TAB_TEASERS = {
    storage:    { icon: 'hard-drive',    title: 'Storage Management',   features: ['Interactive disk map with LVM management', 'SMART health monitoring', 'Mount/unmount and format operations'] },
    hardware:   { icon: 'server',        title: 'Hardware Inventory',    features: ['CPU, GPU, and memory details', 'PCI expansion slot mapping', 'SATA port status and upgrade potential'] },
    memory:     { icon: 'memory-stick',  title: 'Memory Configuration', features: ['DIMM slot inventory with serials', 'Memory controller details', 'Upgrade capacity calculator'] },
    processes:  { icon: 'cpu',           title: 'Process Manager',       features: ['Live process table with CPU/memory', 'Process kill controls', 'Systemd service management'] },
    docker:     { icon: 'container',     title: 'Docker & Stack Manager',features: ['Container health monitoring', 'Stack creation and management', 'Resource usage tracking'] },
    network:    { icon: 'network',       title: 'Network Monitor',       features: ['Interface status and bandwidth', 'Active connections and port map', 'Docker network topology'] },
    firewall:   { icon: 'shield',        title: 'Firewall Management',   features: ['UFW rule management', 'Enable/disable firewall', 'Port allow/deny controls'] }
};

function buildTeaserOverlay(teaser) {
    var overlay = document.createElement('div');
    overlay.className = 'tier-locked-overlay';

    var card = document.createElement('div');
    card.className = 'tier-locked-card';

    // Icon
    var iconDiv = document.createElement('div');
    iconDiv.className = 'tier-locked-icon';
    var iconEl = document.createElement('i');
    iconEl.setAttribute('data-lucide', teaser.icon);
    iconEl.style.cssText = 'width:48px;height:48px;';
    iconDiv.appendChild(iconEl);
    card.appendChild(iconDiv);

    // Title
    var titleEl = document.createElement('h3');
    titleEl.className = 'tier-locked-title';
    titleEl.textContent = teaser.title;
    card.appendChild(titleEl);

    // Features list
    var ul = document.createElement('ul');
    ul.className = 'tier-locked-features';
    teaser.features.forEach(function(f) {
        var li = document.createElement('li');
        li.textContent = f;
        ul.appendChild(li);
    });
    card.appendChild(ul);

    // CTA button
    var cta = document.createElement('a');
    cta.href = '/settings';
    cta.className = 'tier-locked-cta';
    cta.textContent = 'Unlock with Pro \u2014 $29/mo';
    card.appendChild(cta);

    // Sign-in link
    var signin = document.createElement('span');
    signin.className = 'tier-locked-signin';
    signin.textContent = 'Already subscribed? ';
    var signinLink = document.createElement('a');
    signinLink.href = '/auth/kytran/login';
    signinLink.textContent = 'Sign in with Kytran';
    signin.appendChild(signinLink);
    card.appendChild(signin);

    overlay.appendChild(card);
    return overlay;
}

function applyTierGating() {
    if (window.KSO_TIER_AT_LEAST('pro')) return;

    Object.keys(TAB_TEASERS).forEach(function(tabName) {
        var tabContent = document.getElementById('tab-' + tabName);
        if (!tabContent) return;
        if (tabContent.querySelector('.tier-locked-overlay')) return;

        tabContent.classList.add('tier-tab-wrapper');
        tabContent.appendChild(buildTeaserOverlay(TAB_TEASERS[tabName]));
    });

    if (typeof lucide !== 'undefined') lucide.createIcons();

    // Hide action buttons marked as tier-gated
    document.querySelectorAll('[data-tier-required]').forEach(function(el) {
        if (!window.KSO_TIER_AT_LEAST(el.dataset.tierRequired)) {
            el.classList.add('tier-action-locked');
        }
    });
}
```

- [ ] **Step 2: Call applyTierGating after DOM ready**

In the existing `DOMContentLoaded` handler, add after `refreshAll()`:

```javascript
applyTierGating();
```

- [ ] **Step 3: Verify overlays render for free tier**

Set `KSO_TIER_OVERRIDE=free` in docker-compose, rebuild, login. Click Storage tab — should show frosted overlay with "Unlock with Pro" CTA. Dashboard tab should show data without overlay.

- [ ] **Step 4: Commit**

```bash
git add kytran_system_operations/static/js/system-operations.js
git commit -m "feat: JS tier gating with frosted tab teasers"
```

---

### Task 4: Server-Side Route Gating

**Files:**
- Modify: `kytran_system_operations/routes/firewall_routes.py` — gate firewall actions
- Modify: `kytran_system_operations/routes/process_routes.py` — gate kill actions
- Modify: `kytran_system_operations/routes/stack_routes.py` — gate stack mutations
- Modify: `kytran_system_operations/routes/docker_routes.py` — gate docker actions

- [ ] **Step 1: Add @require_tier to firewall mutation routes**

In `firewall_routes.py`, add `@require_tier("pro")` after `@admin_required_decorator` to:
- `api_firewall_enable` (POST)
- `api_firewall_disable` (POST)
- `api_firewall_allow` (POST)
- `api_firewall_deny` (POST)

Import at top of register function:
```python
from ..middleware.tier_gate import require_tier
```

Read-only routes (`api_firewall_status`, `api_firewall_rules`) stay ungated.

- [ ] **Step 2: Add @require_tier to process kill route**

In `process_routes.py`, gate the kill endpoint with `@require_tier("pro")`.

- [ ] **Step 3: Add @require_tier to stack mutation routes**

In `stack_routes.py`, gate create/delete/start/stop/restart endpoints with `@require_tier("pro")`.

- [ ] **Step 4: Add @require_tier to docker action routes**

In `docker_routes.py`, gate container stop/restart/start endpoints with `@require_tier("pro")`.

- [ ] **Step 5: Verify gating works**

With `KSO_TIER_OVERRIDE=free`, attempt POST to `/dashboard/api/firewall/enable`. Should return 403 JSON with upgrade message.

- [ ] **Step 6: Commit**

```bash
git add kytran_system_operations/routes/*.py
git commit -m "feat: server-side @require_tier on all action endpoints"
```

---

### Task 5: Local Admin Pro Default

**Files:**
- Modify: `kytran_system_operations/auth.py` — auto-set Pro tier for admin users

- [ ] **Step 1: Set Pro tier on admin creation**

In `auth.py`, update `create_admin()` to auto-grant Pro after insert:

```python
def create_admin(username, password):
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db = get_db()
    db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
               (username, pw_hash))
    db.commit()
    row = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    db.close()
    if row:
        from .services.subscription_service import set_user_tier
        set_user_tier(row["id"], "pro")
```

- [ ] **Step 2: Set Pro tier on existing admin login if still free**

In `auth.py`, in the `login()` route handler, after `login_user(user)`:

```python
if user.is_admin:
    from .services.subscription_service import get_user_tier, set_user_tier
    if get_user_tier(user.id) == "free":
        set_user_tier(user.id, "pro")
```

- [ ] **Step 3: Verify**

Login as admin, check `window.KSO_TIER` in console — should be `"pro"`.

- [ ] **Step 4: Commit**

```bash
git add kytran_system_operations/auth.py
git commit -m "feat: auto-grant Pro tier to local admin users"
```

---

### Task 6: Dashboard Consolidation

**Files:**
- Modify: `kytran_system_operations/templates/dashboard.html` — ensure tab content IDs + remove duplicates

- [ ] **Step 1: Audit dashboard for duplicate sections**

Read through `dashboard.html` and identify any sections that render the same data twice. Remove the duplicate, keeping the more complete version.

- [ ] **Step 2: Ensure tab content divs have consistent IDs**

Each tab's content section must have `id="tab-{name}"` for the tier gating JS:
- `id="tab-dashboard"`, `id="tab-storage"`, `id="tab-hardware"`, `id="tab-memory"`
- `id="tab-processes"`, `id="tab-docker"`, `id="tab-network"`, `id="tab-firewall"`

Check existing IDs and add any missing ones.

- [ ] **Step 3: Verify single render per tab**

Rebuild, navigate each tab, confirm no duplicate sections.

- [ ] **Step 4: Commit**

```bash
git add kytran_system_operations/templates/dashboard.html
git commit -m "fix: consolidate dashboard — add tab IDs, remove duplicates"
```

---

### Task 7: Compliance Scanner Tab

**Files:**
- Modify: `kytran_system_operations/templates/dashboard.html` — add tab 9
- Modify: `kytran_system_operations/static/js/system-operations.js` — compliance tab logic

- [ ] **Step 1: Add Compliance tab button**

In `dashboard.html`, after the Firewall tab button:

```html
<button class="tab-btn" data-tab="compliance">
    <i data-lucide="shield-check" class="sysops-icon-16"></i>
    Compliance
</button>
```

- [ ] **Step 2: Add Compliance tab content**

Add `<div id="tab-compliance" class="tab-content">` with scan trigger, results table placeholder, fix buttons with `data-tier-required="pro"`, and PDF export with `data-tier-required="business"`.

- [ ] **Step 3: Add JS to load compliance data**

Add `loadCompliance()` function fetching from existing `/dashboard/api/compliance/latest` endpoint.

- [ ] **Step 4: Verify and commit**

```bash
git add kytran_system_operations/templates/dashboard.html kytran_system_operations/static/js/system-operations.js
git commit -m "feat: Compliance Scanner tab with tier-gated fix buttons"
```

---

### Task 8: Landing Page Update

**Files:**
- Modify: `kytran_system_operations/templates/landing.html`

- [ ] **Step 1: Update landing page**

Replace content with: product hero, feature highlights (8 tabs + compliance), pricing cards (Free/Pro/Business/Enterprise), "Get Started" CTA, "Sign in with Kytran" link. Follow Civic Watch pattern.

- [ ] **Step 2: Verify and commit**

```bash
git add kytran_system_operations/templates/landing.html
git commit -m "feat: product landing page with pricing tiers"
```

---

### Task 9: Integration Test

- [ ] **Step 1: Test free tier** — `KSO_TIER_OVERRIDE=free`: Dashboard read-only, frosted teasers on tabs 2-8, POST actions return 403
- [ ] **Step 2: Test Pro tier** — Local admin login: all tabs, actions work, 3 themes
- [ ] **Step 3: Test theme switching** — tier persists across theme changes
- [ ] **Step 4: Push**

```bash
git push origin feat/resync-from-platform-module
```
