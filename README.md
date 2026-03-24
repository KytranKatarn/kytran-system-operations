<div align="center">

# Kytran Server Manager

**Self-hosted server management dashboard**

CPU • RAM • Disk • Docker • Network • Firewall — all in one beautiful interface.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776ab.svg)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ed.svg)](https://hub.docker.com)

[Features](#features) • [Quick Start](#quick-start) • [Screenshots](#screenshots) • [Themes](#themes) • [Documentation](#documentation)

</div>

---

## Why Kytran Server Manager?

Most server management tools are either too complex (Webmin), too limited (Cockpit), or focused on just containers (Portainer). Kytran Server Manager gives you **everything in one place** with a clean, modern interface.

| Feature | Kytran SM | Portainer | Cockpit | Webmin |
|---------|:---------:|:---------:|:-------:|:------:|
| CPU/RAM/Disk monitoring | ✅ | ❌ | ✅ | ✅ |
| Docker + Compose stacks | ✅ | ✅ | ✅ | ❌ |
| UFW Firewall management | ✅ | ❌ | ❌ | ✅ |
| LVM Storage management | ✅ | ❌ | ✅ | ✅ |
| Network port mapping | ✅ | ❌ | ❌ | ❌ |
| File browser | ✅ | ❌ | ❌ | ✅ |
| Process management | ✅ | ❌ | ✅ | ✅ |
| Health alerts + webhooks | ✅ | ❌ | ❌ | ❌ |
| Config-driven themes | ✅ | ❌ | ❌ | ✅ |
| Single-file install | ✅ | ✅ | ✅ | ❌ |

## Features

### 🖥️ Real-Time Monitoring
CPU, memory, disk usage with auto-refresh gauges. Hardware detection with upgrade recommendations.

### 🐳 Docker Management
Full container lifecycle — start, stop, restart, logs. Multi-stack orchestration with compose editor. Health monitoring for all stacks.

### 🔥 Firewall Management
UFW rules management — add, edit, delete rules. Enable/disable firewall. Visual rule table with port/protocol/action.

### 💾 Storage Management
Disk map with mount points, LVM volume management (extend, resize), RAID status, file browser with directory navigation.

### 🌐 Network Monitoring
Interface status, active connections, port mapping, bandwidth usage. See what's listening on which ports.

### ⚡ Process Control
Real-time process table sorted by CPU/memory. Kill processes directly. Systemd service management — start, stop, restart, enable, disable.

### 🔔 Health Alerts
Configurable alerts for CPU, memory, disk thresholds. Webhook integration for Slack, Discord, custom endpoints.

### 🎨 Themeable
Ships with two themes:
- **Kytran** (default) — Clean, modern, professional
- **LCARS** — Sci-fi aesthetic inspired by Star Trek

Create your own theme with a simple JSON config file.

## Screenshots

| Dashboard | Docker Stacks |
|-----------|--------------|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Docker](docs/screenshots/docker.png) |

| Storage & LVM | Network |
|---------------|---------|
| ![Storage](docs/screenshots/storage.png) | ![Network](docs/screenshots/network.png) |

| Hardware Info | Processes |
|--------------|-----------|
| ![Hardware](docs/screenshots/hardware.png) | ![Processes](docs/screenshots/processes.png) |

## Quick Start

### pip install

```bash
pip install kytran-server-manager
kytran-server-manager
```

Open http://localhost:8080 and create your admin account.

### Docker

```bash
docker run -d \
  --name kytran-server-manager \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v ksm-data:/data \
  ghcr.io/kytrankatarn/kytran-server-manager
```

### Docker Compose

```yaml
version: "3.8"
services:
  kytran-server-manager:
    image: ghcr.io/kytrankatarn/kytran-server-manager
    ports:
      - "8080:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ksm-data:/data
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
    environment:
      - KSM_SECRET_KEY=your-secret-key
      - KSM_THEME=kytran
    restart: unless-stopped

volumes:
  ksm-data:
```

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `KSM_SECRET_KEY` | `change-me` | Flask secret key |
| `KSM_PORT` | `8080` | Server port |
| `KSM_HOST` | `0.0.0.0` | Bind address |
| `KSM_THEME` | `kytran` | Theme name (`kytran` or `lcars`) |
| `KSM_DATA_DIR` | `~/.kytran-server-manager` | Data directory (SQLite DB) |
| `KSM_DEBUG` | `false` | Debug mode |

## Themes

Create a custom theme by adding a JSON file to the `themes/` directory:

```json
{
  "product_name": "My Server Manager",
  "frame_style": "modern",
  "colors": {
    "accent": "#8b5cf6",
    "bg_primary": "#1a1a2e"
  },
  "fonts": {
    "heading": "Inter",
    "body": "Inter"
  }
}
```

Set `KSM_THEME=mytheme` to use it.

## Tech Stack

- **Backend:** Python, Flask
- **Frontend:** Vanilla JS, Chart.js
- **Database:** SQLite (zero-config)
- **System Data:** psutil
- **Auth:** bcrypt + Flask-Login

## License

Apache 2.0 — [Kytran Empowerment Inc.](https://kytranempowerment.com)

---

<div align="center">

**Built with ❤️ by [Kytran Empowerment](https://kytranempowerment.com)**

</div>
