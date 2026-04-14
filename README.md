<div align="center">

# Kytran System Operations

**Self-hosted server management dashboard — CPU, RAM, disk, Docker, network, firewall, and compliance scanning in one interface.**

[![License](https://img.shields.io/badge/License-AGPL%203.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776ab.svg)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ed.svg)](https://hub.docker.com)

</div>

---

## What It Does

Kytran System Operations gives you complete visibility and control over your Linux server from a single web dashboard. Monitor hardware metrics in real time, manage Docker stacks, control your UFW firewall, browse the filesystem, track processes, and run compliance scans — all without SSH.

Unlike tools that focus on one thing (Portainer for containers, Cockpit for OS), KSO handles the full stack. It ships with five LCARS-themed UI variants and a compliance scanner covering Ubuntu STIG, Docker STIG, HIPAA, Network STIG, and CIS Ubuntu rule packs.

## Quick Start

```bash
git clone https://github.com/KytranKatarn/kytran-system-operations.git
cd kytran-system-operations
cp .env.example .env   # edit as needed
docker compose up -d
```

Open http://localhost:8085 and create your admin account.

## Features

- **Real-Time Monitoring** — CPU, RAM, disk usage with auto-refresh gauges. Hardware detection with upgrade recommendations.
- **Docker Management** — Full container lifecycle (start/stop/restart/logs). Multi-stack compose editor. Health monitoring for all stacks.
- **Firewall Management** — UFW rules: add, edit, delete, enable/disable. Visual rule table with port/protocol/action.
- **Storage Management** — Disk map with mount points, LVM volume management (extend/resize), RAID status, file browser.
- **Network Monitoring** — Interface status, active connections, port mapping, bandwidth usage.
- **Process Control** — Real-time process table sorted by CPU/memory. Kill processes. Systemd service management.
- **Health Alerts** — Configurable CPU/RAM/disk threshold alerts. Webhook integration for Slack, Discord, custom endpoints.
- **Compliance Scanning** — 5 rule packs (Ubuntu STIG, Docker STIG, HIPAA, Network STIG, CIS Ubuntu). Live SVG badges. One-click remediation.
- **LCARS Themes** — 5 themes: Kytran (default), LCARS, Midnight, Arctic, Ember. Custom themes via JSON config.

## Screenshots

| Dashboard | Docker Stacks |
|-----------|--------------|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Docker](docs/screenshots/docker.png) |

| Storage & LVM | Network |
|---------------|---------|
| ![Storage](docs/screenshots/storage.png) | ![Network](docs/screenshots/network.png) |

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `SECRET_KEY` | `change-me-in-production` | Flask secret key |
| `KSO_PORT` | `8085` | Server port |
| `DB_HOST` | _(empty)_ | PostgreSQL host (SQLite used if unset) |
| `DB_NAME` | `archie` | Database name |
| `DB_USER` | `archie` | Database user |
| `DB_PASSWORD` | _(empty)_ | Database password |
| `HOST_IP` | _(empty)_ | IP of the host being monitored |
| `HOST_FQDN` | _(empty)_ | Fully qualified domain name of the host |
| `KSO_ARCHIE_HUB_URL` | _(empty)_ | A.R.C.H.I.E. hub URL for SSO + compliance reporting |

See `.env.example` for the full list.

## Tech Stack

- **Backend:** Python 3.9+, Flask
- **Frontend:** Vanilla JS, Chart.js
- **System Data:** psutil
- **Auth:** bcrypt + Flask-Login
- **Compliance:** JSON-driven rule packs, auto-scan every 6 hours

## License

AGPL-3.0 — [Kytran Empowerment Inc.](https://kytranempowerment.com)

---

<div align="center">

Part of the [Kytran Empowerment](https://kytranempowerment.com) ecosystem.

</div>
