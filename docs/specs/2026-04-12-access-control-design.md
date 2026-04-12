# KSO Access Control & Subscription Gating — Design Spec

**Date:** 2026-04-12
**Product:** Kytran System Operations (KSO)
**Reference UI:** Civic Watch / Legal Watch standalone pattern

---

## 1. Overview

KSM has three access modes with a unified tier resolution chain. Features are gated by subscription tier, enforced server-side and presented client-side with frosted teaser overlays on locked content.

## 2. User Types & Access Modes

| Type | How they get in | Default tier | Can upgrade? |
|------|----------------|-------------|-------------|
| **Visitor** | No login | Free | Must create account |
| **Local Admin** | Setup page (self-hosted) | Pro-equivalent | Business/Enterprise via Kytran OAuth |
| **Kytran Subscriber** | "Sign in with Kytran" OAuth | Per ARCHIE Account Center | Yes, via Stripe billing |

### Tier Resolution Order
1. `KSO_TIER_OVERRIDE` env var (internal deployments)
2. OAuth entitlements from ARCHIE Account Center (SSO users)
3. Local DB `subscriptions` table (Stripe payment)
4. Local admin default → **Pro**
5. Fallback → **Free**

## 3. Feature Matrix

| Feature | Free | Pro $29/mo | Business $49/mo | Enterprise $99/mo |
|---------|------|-----------|----------------|-------------------|
| **Dashboard tab** | View only (no actions) | Full + actions | Full + actions | Full + actions |
| **Other 7 tabs** | Frosted teaser | Unlocked | Unlocked | Unlocked |
| **Actions** (kill, firewall, stacks) | Locked | Unlocked | Unlocked | Unlocked |
| **Themes** | 2 (Kytran + LCARS) | 3 (+Midnight) | All 5 | All 5 |
| **Compliance Scan** | Run + view results | + Fix (3 packs) | + All 5 packs + PDF | + Custom packs |
| **Scan Frequency** | Manual only | Every 6h | Every 1h | Configurable |
| **AI Analysis** | — | — | — | S.H.I.E.L.D. integration |
| **ARCHIE Monitoring** | — | — | — | Agent loop alerts |
| **Support** | Community | Email | Priority | Dedicated + AI |

## 4. UI Gating — Frosted Teasers

### 4.1 Dashboard Tab (Free/Local-Admin-Free)
- Live data visible: CPU, memory, GPU, disk overview, system info
- Refresh button works (read-only, not dead)
- History range limited to 1h (24h/7d/30d show upgrade prompt)
- No action buttons rendered

### 4.2 Locked Tabs (Pro+ required)
Tabs are clickable — content renders behind a frosted overlay:

```css
.tier-locked-overlay {
    position: absolute;
    inset: 0;
    backdrop-filter: blur(8px);
    background: rgba(var(--archie-bg-primary-rgb), 0.85);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 50;
    border-radius: var(--sysops-border-radius);
}
```

Overlay card contains:
- Tab icon + name
- 2-3 bullet points describing what the tab does
- "Unlock with Pro — $29/mo" CTA button
- "Already have an account? Sign in with Kytran" link

### 4.3 Compliance Scanner (new tab, position 9)
- **Free:** Scan runs, results show pass/fail — "Fix" buttons have lock icons
- **Pro:** Fix buttons active for CIS Ubuntu, Ubuntu STIG, Network STIG
- **Business:** All 5 packs + batch remediation + PDF evidence export
- **Enterprise:** Custom rule packs + API access

### 4.4 Technical Implementation
- Server returns full data regardless of tier (no tier-conditional API queries)
- JS checks `window.KSO_TIER` and applies `.tier-locked` class to gated sections
- Action API endpoints enforce `@require_tier()` server-side (defense in depth)
- Template injects `{{ user_tier }}` and `{{ tier_features }}` via context processor

## 5. Authentication Flow

Follows Civic Watch / Legal Watch pattern:

```
Landing Page (/) → Product intro, feature highlights, pricing
    ↓
Login (/login) → Local username/password form
    |            + "Sign in with Kytran" OAuth button
    ↓
Dashboard (/dashboard) → Single consolidated view
    |                     No duplicate data sections
    ↓
Settings (/settings) → Theme, subscription, account, password
```

### 5.1 Local Admin Flow
1. First visit → `/setup` page → create admin account
2. Admin logs in → Pro-equivalent access
3. Upgrade path: connect Kytran OAuth → pay via Stripe → Business/Enterprise

### 5.2 Kytran OAuth Flow
1. Click "Sign in with Kytran" → redirect to ARCHIE `/oauth/authorize`
2. ARCHIE authenticates → returns code → KSM exchanges for token
3. Token includes `entitlements` array with subscribed product IDs
4. KSM creates/updates local user with `sso_provider='kytran'`
5. Tier resolved from entitlements → stored in local `subscriptions` table

### 5.3 Contract with ARCHIE OAuth Provider
KSM expects these endpoints on ARCHIE Account Center:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/oauth/authorize` | GET | Authorization redirect (client_id, redirect_uri, state) |
| `/oauth/token` | POST | Code → token exchange |
| `/oauth/userinfo` | GET | User profile + entitlements array |

Userinfo response must include:
```json
{
  "sub": "user_uuid",
  "username": "kytran",
  "email": "user@example.com",
  "role": "admin",
  "entitlements": ["kso", "civic-watch", "market-watch"]
}
```

## 6. Dashboard Consolidation

Current state has duplicate sections from the platform module copy. Consolidation plan:

1. **Single data source per metric** — remove duplicate API calls
2. **Match Watch product layout** — landing → login → clean dashboard
3. **One header** — modern-frame header (Kytran theme) or LCARS bar (LCARS theme), not both
4. **Tab content de-duplicated** — each tab renders once, not twice

## 7. Subscription Tier Enforcement Points

| Layer | Mechanism | What it gates |
|-------|-----------|---------------|
| **Route decorator** | `@require_tier("pro")` | API actions (kill, firewall, fix) |
| **Template context** | `{% if user_tier_at_least('pro') %}` | Button rendering |
| **JS client** | `window.KSO_TIER` | Frosted overlay, disabled buttons |
| **Middleware** | `require_tier()` in `middleware/tier_gate.py` | Batch route protection |

## 8. Separate Project: Cross-Product Auth Evaluation

**Not in scope for this spec** but must be tracked:

- Audit auth flow across all standalones (KSM, Civic Watch, Legal Watch, Market Watch, Gov Watch, The News, Business Suite)
- Ensure consistent OAuth contract with ARCHIE Account Center
- Verify subscription/entitlement sync across products
- Build ARCHIE-side OAuth provider if not complete

This should be tracked as its own project in Dev HQ.

## 9. Files to Modify

| File | Change |
|------|--------|
| `auth.py` | Add tier to User object, inject tier context |
| `routes/__init__.py` | Add `@require_tier` to action routes |
| `templates/dashboard.html` | Add frosted overlay conditionals per tab |
| `static/css/system-operations.css` | Add `.tier-locked-overlay` styles |
| `static/js/system-operations.js` | Add tier checking + overlay logic |
| `theme.py` | Inject `user_tier` into template context |
| `app.py` | Set `window.KSO_TIER` in base template |
| `templates/base.html` | Add tier JS global |
| `templates/landing.html` | Update with product intro + pricing |

## 10. Success Criteria

- [ ] Free user sees Dashboard tab with live read-only data
- [ ] Free user sees frosted teasers on tabs 2-8 with upgrade CTA
- [ ] Local admin gets Pro-level access (all tabs + actions, 3 compliance packs)
- [ ] Kytran OAuth user gets tier from ARCHIE entitlements
- [ ] Theme switching respects tier limits (2/3/5/5)
- [ ] Compliance scan: free sees results, pro can fix, business gets PDF
- [ ] Action endpoints return 403 for insufficient tier
- [ ] Upgrade buttons link to Stripe checkout via ARCHIE hub
- [ ] No duplicate data sections on dashboard
- [ ] UI matches Civic Watch / Legal Watch standalone pattern
