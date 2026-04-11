# Windows 11 GPU Node Setup — Docker + NVIDIA RTX 3070

Guide for setting up fresh Windows 11 machines as Docker-based GPU compute nodes
running Ollama, ComfyUI, fleet agent, and Tailscale mesh networking.

---

## 1. BIOS Settings (Do First)

Enter BIOS (usually Del or F2 at boot):

- **Enable Intel VT-x / AMD SVM** (CPU virtualization) — REQUIRED for WSL2/Hyper-V
- **Enable Intel VT-d / AMD IOMMU** (I/O virtualization) — recommended
- **Disable Secure Boot** — optional but avoids potential driver signing issues
- **Enable Above 4G Decoding** — helps with GPU memory mapping (if available)

---

## 2. Install Order (CRITICAL — follow exactly)

Conflicts arise when these are installed out of order. This sequence is proven:

### Step 1: Windows Update (fully patched)
```
Settings > Windows Update > Check for updates
Reboot as needed until fully current
```
Windows 11 22H2+ includes WSL2 kernel with GPU paravirtualization support built in.

### Step 2: NVIDIA GPU Driver (already done per your notes)
Verify with:
```powershell
nvidia-smi
```
Must show the RTX 3070, driver version, CUDA version. If this fails, nothing else will work.

**Important:** You need the standard NVIDIA Game/Studio driver (or DCH driver).
Do NOT install the "NVIDIA Container Toolkit" on Windows itself — that is Linux-only.
The Windows NVIDIA driver already includes WSL2 GPU support since driver 510+.

### Step 3: Enable Windows Features
Run PowerShell as Administrator:
```powershell
# Enable WSL
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart

# Enable Virtual Machine Platform (required for WSL2)
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

# Enable Hyper-V (Docker Desktop needs this OR WSL2 backend)
dism.exe /online /enable-feature /featurename:Microsoft-Hyper-V-All /all /norestart

# Reboot
Restart-Computer
```

Alternatively via GUI: Settings > Apps > Optional Features > More Windows Features:
- [x] Hyper-V
- [x] Virtual Machine Platform
- [x] Windows Subsystem for Linux

### Step 4: Install WSL2
```powershell
# After reboot, set WSL2 as default
wsl --set-default-version 2

# Install Ubuntu 22.04 (or 24.04)
wsl --install -d Ubuntu-22.04
```
This will prompt you to create a Linux username/password.

### Step 5: Verify GPU in WSL2
```bash
# Inside WSL2 terminal:
nvidia-smi
```
This should show your RTX 3070. The Windows NVIDIA driver automatically exposes
the GPU to WSL2 via /dev/dxg — no separate Linux driver install needed.

**DO NOT install NVIDIA drivers inside WSL2.** The Windows driver handles everything.
Installing Linux NVIDIA drivers inside WSL2 will BREAK GPU access.

### Step 6: Install Docker Desktop
Download from https://docs.docker.com/desktop/install/windows-install/

During install:
- [x] Use WSL 2 based engine (NOT Hyper-V backend)
- [x] Add shortcut to desktop

After install:
- Open Docker Desktop
- Settings > General > "Use the WSL 2 based engine" = ON
- Settings > Resources > WSL Integration > Enable for your Ubuntu distro
- Apply & Restart

### Step 7: Install NVIDIA Container Toolkit (inside WSL2)
```bash
# Inside WSL2 Ubuntu terminal:

# Add NVIDIA container toolkit repo
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Configure Docker to use NVIDIA runtime
sudo nvidia-ctk runtime configure --runtime=docker

# Restart Docker Desktop from Windows side after this
```

### Step 8: Verify GPU in Docker
```powershell
# From PowerShell or WSL2:
docker run --rm --gpus all nvidia/cuda:12.3.1-base-ubuntu22.04 nvidia-smi
```
Must show the RTX 3070 inside the container.

---

## 3. Docker Desktop vs WSL2 + Docker Engine

### Recommendation: Docker Desktop with WSL2 backend

**Docker Desktop (recommended for your use case):**
- Simpler setup and maintenance
- GUI for container management
- Automatic WSL2 integration
- GPU passthrough works out of the box once NVIDIA Container Toolkit is installed
- Handles Docker daemon lifecycle (starts on boot)
- License: free for personal/small business use

**WSL2 + Docker Engine (bare metal alternative):**
- Install `docker-ce` directly inside WSL2 (no Docker Desktop)
- Slightly less overhead
- Must manually start Docker daemon (`sudo service docker start`)
- Must configure systemd in WSL2 (`/etc/wsl.conf` with `[boot] systemd=true`)
- More complex to auto-start on boot
- Better if you want to avoid Docker Desktop licensing

For fleet nodes that need reliability and auto-start, Docker Desktop is simpler.
For headless/unattended operation, bare Docker Engine with systemd in WSL2 is more robust
but requires more setup.

---

## 4. Common Gotchas That Break GPU Access

### Gotcha 1: Installing NVIDIA drivers inside WSL2
**NEVER** install `nvidia-driver-*` packages inside WSL2.
The Windows host driver provides GPU access via `/dev/dxg`.
Installing Linux drivers overwrites this and breaks everything.
Fix: `sudo apt remove --purge nvidia-*` inside WSL2, then reboot.

### Gotcha 2: Wrong Docker backend
Docker Desktop must use "WSL 2 based engine", NOT "Hyper-V backend".
GPU passthrough only works with WSL2 backend.
Check: Docker Desktop > Settings > General > "Use the WSL 2 based engine"

### Gotcha 3: WSL2 kernel too old
GPU support requires WSL2 kernel 5.10.43+.
```powershell
wsl --update
wsl --shutdown
```
Then reopen your distro.

### Gotcha 4: Docker Compose `deploy` syntax
For GPU access in docker-compose.yml, use:
```yaml
services:
  ollama:
    image: ollama/ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all  # or count: 1
              capabilities: [gpu]
    # OR the simpler legacy syntax (Docker Compose v2.x):
    # runtime: nvidia
    # environment:
    #   - NVIDIA_VISIBLE_DEVICES=all
```

### Gotcha 5: WSL2 memory limits
By default WSL2 takes up to 50% of host RAM. For 32GB machines running Ollama:
Create/edit `%USERPROFILE%\.wslconfig`:
```ini
[wsl2]
memory=28GB
swap=4GB
processors=8
localhostForwarding=true
```
Then `wsl --shutdown` and restart.

### Gotcha 6: Docker Desktop auto-start
For unattended nodes, ensure Docker Desktop starts on login:
Docker Desktop > Settings > General > "Start Docker Desktop when you sign in" = ON
Also set the Windows user to auto-login if needed for headless operation.

### Gotcha 7: Windows Fast Startup interfering
Disable Fast Startup to ensure clean boot state:
Control Panel > Power Options > Choose what the power buttons do >
Change settings > Uncheck "Turn on fast startup"

### Gotcha 8: NVIDIA Container Toolkit version mismatch
Always use the latest nvidia-container-toolkit. Older versions may not support
WSL2 GPU passthrough correctly.

---

## 5. Network Configuration for LAN Access

The node needs to be reachable from 192.168.1.x (your hub at .200).

### Port forwarding from Windows to WSL2/Docker

Docker Desktop with WSL2 backend automatically forwards container ports to
the Windows host. So `docker run -p 11434:11434 ollama/ollama` makes Ollama
available at `<windows-ip>:11434` from LAN.

### Static IP
Set a static IP on the Windows machine:
Settings > Network > Ethernet > IP assignment > Edit:
- IP: 192.168.1.245 (or .246)
- Subnet: 255.255.255.0
- Gateway: 192.168.1.1
- DNS: 192.168.1.200 (your AdGuard), 8.8.8.8 fallback

### Windows Firewall
Allow inbound connections for Docker container ports:
```powershell
# Run as Administrator
# Ollama
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -Port 11434 -Protocol TCP -Action Allow
# ComfyUI
New-NetFirewallRule -DisplayName "ComfyUI" -Direction Inbound -Port 8188 -Protocol TCP -Action Allow
# Fleet agent (adjust port as needed)
New-NetFirewallRule -DisplayName "Fleet Agent" -Direction Inbound -Port 8090 -Protocol TCP -Action Allow
# Tailscale (usually handles its own firewall rules)
```

### WSL2 localhost forwarding
Ensure `%USERPROFILE%\.wslconfig` has:
```ini
[wsl2]
localhostForwarding=true
```

### Tailscale/Headscale
Install Tailscale on the Windows host (not inside WSL2/Docker).
This gives the machine a mesh IP (100.64.x.x) and routes traffic to Docker containers.
```powershell
# Download and install Tailscale from https://tailscale.com/download/windows
# Then join your Headscale coordination server:
tailscale up --login-server=https://your-headscale-url --authkey=YOUR_KEY
```

---

## 6. Verification Checklist

Run these in order after full setup:

```powershell
# 1. Windows: NVIDIA driver works
nvidia-smi
# Expected: Shows RTX 3070, driver version, CUDA version

# 2. WSL2: GPU visible
wsl nvidia-smi
# Expected: Same GPU info from inside WSL2

# 3. Docker: GPU accessible in container
docker run --rm --gpus all nvidia/cuda:12.3.1-base-ubuntu22.04 nvidia-smi
# Expected: RTX 3070 visible inside container

# 4. Ollama: GPU inference works
docker run -d --gpus all -p 11434:11434 --name ollama ollama/ollama
docker exec ollama ollama run llama3.2:1b "Hello"
# Expected: Response generated, check `docker exec ollama ollama ps` shows GPU layers

# 5. Network: LAN accessible
# From hub (192.168.1.200):
curl http://192.168.1.245:11434/api/tags
# Expected: JSON response with loaded models

# 6. Tailscale: Mesh connected
tailscale status
# Expected: Shows connection to headscale, peer list includes hub
```

---

## 7. Docker Compose Template for Node

```yaml
# docker-compose.yml for GPU compute node
version: "3.8"

services:
  ollama:
    image: ollama/ollama:latest
    container_name: node_ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: unless-stopped
    environment:
      - OLLAMA_HOST=0.0.0.0
      - OLLAMA_KEEP_ALIVE=24h
      - OLLAMA_MAX_LOADED_MODELS=4
      - OLLAMA_NUM_PARALLEL=2

  # Optional: ComfyUI for image generation
  # comfyui:
  #   image: ghcr.io/ai-dock/comfyui:latest
  #   container_name: node_comfyui
  #   ports:
  #     - "8188:8188"
  #   volumes:
  #     - comfyui_data:/workspace
  #   deploy:
  #     resources:
  #       reservations:
  #         devices:
  #           - driver: nvidia
  #             count: all
  #             capabilities: [gpu]
  #   restart: unless-stopped

  # Fleet management agent
  fleet-agent:
    image: ghcr.io/kytrankatarn/archie-node:latest
    container_name: node_fleet_agent
    ports:
      - "8090:8090"
    environment:
      - HUB_URL=http://192.168.1.200:3000
      - NODE_NAME=node-245
      - OLLAMA_HOST=http://ollama:11434
    depends_on:
      - ollama
    restart: unless-stopped

volumes:
  ollama_data:
  comfyui_data:
```

---

## 8. RTX 3070 Specific Notes

- **VRAM:** 8GB GDDR6 — same as your hub Quadro M4000 but much faster
- **Ollama models:** Can load 1 large (7B q4) or 2-3 small models in VRAM simultaneously
- **ComfyUI:** SDXL runs well, can do 1024x1024 in ~5-8 seconds
- **Context window:** Keep `num_ctx` conservative (2048-4096) to avoid VRAM overflow
- **Power:** 220W TDP — ensure adequate PSU (650W+ recommended)
- **Concurrent GPU use:** Ollama + ComfyUI will fight for VRAM. Use the hub's VRAM swap
  pattern to unload Ollama models before SDXL generation on the node.

---

## Summary: Minimum Install Checklist

1. BIOS: Enable virtualization (VT-x/SVM)
2. Windows Update: Fully patched
3. NVIDIA Driver: Verify with `nvidia-smi` (already done)
4. Windows Features: WSL, Virtual Machine Platform, Hyper-V
5. Reboot
6. WSL2: `wsl --install -d Ubuntu-22.04`
7. Verify: `nvidia-smi` inside WSL2
8. Docker Desktop: Install with WSL2 backend
9. NVIDIA Container Toolkit: Install inside WSL2
10. Restart Docker Desktop
11. Verify: `docker run --rm --gpus all nvidia/cuda:12.3.1-base-ubuntu22.04 nvidia-smi`
12. Static IP + Firewall rules
13. Tailscale: Install on Windows, join mesh
14. Deploy containers with docker-compose
