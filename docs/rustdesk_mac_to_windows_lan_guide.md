# RustDesk Setup Guide: Control Windows PC from Mac on a Local Network

This guide covers controlling a Windows PC from a Mac on the same local network using [RustDesk](https://rustdesk.com).

## Big Picture

RustDesk gives you two ways to connect:

1. **Public servers (easiest)** — RustDesk's free relay servers handle the connection. No extra server needed.
2. **Self-hosted server (most private)** — You run a small RustDesk server (e.g., on a local Linux box, NAS, or cheap VPS) so traffic never leaves your infrastructure.

For a **local network only**, you can also enable **Direct IP access** so the Mac connects straight to the Windows PC by its LAN IP (`192.168.x.x`), bypassing any relay.

---

## Part 1 — Install RustDesk on the Windows PC (the machine to be controlled)

### 1. Download

- Go to [rustdesk.com](https://rustdesk.com) or the [GitHub releases](https://github.com/rustdesk/rustdesk/releases/latest).
- Download the Windows installer (`.exe` or `.msi`).

### 2. Install

- Run the installer.
- Choose **Install** (not just "Run"). This registers RustDesk as a system service, which is required for:
  - Unattended access
  - Surviving logouts
  - Working through UAC prompts

> If you only run the portable `.exe`, the connection drops on logout or UAC.

### 3. Start RustDesk

- After install, open RustDesk.
- On the main window you will see:
  - **ID** (e.g., `123 456 789`)
  - **One-time password**
  - A message like "Ready. For faster connection, please set up your own server"

### 4. Set a permanent password

- Click the **menu (⋮)** next to your ID → **Settings**.
- Go to **Security**.
- Click the **pencil icon** next to the password.
- Select **Set permanent password** and type a strong password.
- This is the password you will enter from your Mac to connect.

### 5. Enable unattended access / service

- Go to **Settings → General**.
- Make sure **Service** is toggled **ON**.
- If you see an **Install service** button, click it and allow admin rights.

### 6. For local network direct connection (optional but recommended for LAN)

- Go to **Settings → Security**.
- Unlock security settings with admin rights.
- Enable **Enable direct IP access**.
- Default port is **21118**.
- Make a note of the Windows PC's local IP:
  - Open PowerShell or Command Prompt and run:
    ```cmd
    ipconfig
    ```
  - Look for `IPv4 Address` under your active adapter, e.g., `192.168.1.45`.

### 7. Windows Firewall

- If you use direct IP access, Windows Defender Firewall may block RustDesk.
- Add an inbound rule to allow RustDesk, or temporarily disable the firewall for testing.
- RustDesk's default listener port for direct IP is **21118**.

---

## Part 2 — Install RustDesk on the Mac (the controller)

### 1. Download

- Go to [rustdesk.com](https://rustdesk.com) or [GitHub releases](https://github.com/rustdesk/rustdesk/releases/latest).
- Download the macOS `.dmg`.
- Make sure you pick the right architecture:
  - Apple Silicon Mac (M1/M2/M3/M4) → `aarch64` / `arm64`
  - Intel Mac → `x86_64` / `x64`

### 2. Install

- Open the `.dmg`.
- Drag **RustDesk** into **Applications**.
- Do **not** run it from the `.dmg`.

### 3. First launch permissions

- macOS will warn that RustDesk was downloaded from the internet. Click **Open**.
- If Gatekeeper blocks it, go to **System Settings → Privacy & Security**, scroll down, and click **Open Anyway**.

### 4. Grant macOS permissions

RustDesk needs permissions to view and control the screen. Since you are using the Mac only to **control** the Windows PC, the critical permissions are mainly for screen capture on the local side, but the app will ask for them anyway.

Go to **System Settings → Privacy & Security**:

- **Screen Recording** → add/check **RustDesk**
- **Accessibility** → add/check **RustDesk**
- **Input Monitoring** → add/check **RustDesk** (needed on newer macOS for local input capture)

> If RustDesk appears in the list but does not work, remove it with the **−** button and re-add it with the **+** button from **Applications → RustDesk**. A reboot may be required.

---

## Part 3 — Connect Mac to Windows PC

### Option A — Use RustDesk ID (via public servers)

1. On the Mac, open RustDesk.
2. In the **Remote Device ID** box on the right, enter the Windows PC's **ID**.
3. Click **Connect**.
4. Enter the **permanent password** you set on Windows.
5. You should now see the Windows desktop.

### Option B — Direct IP on local network (no relay)

1. On the Windows PC, confirm direct IP access is enabled and note the LAN IP (`192.168.x.x`).
2. On the Mac, open RustDesk.
3. In the **Remote Device ID** box, type the Windows PC's local IP:
   ```
   192.168.1.45
   ```
   or with port:
   ```
   192.168.1.45:21118
   ```
4. Click **Connect**.
5. Enter the permanent password.
6. You are connected directly over LAN.

> Direct IP connections without a relay server are **unencrypted** according to some community docs. For encrypted LAN-only traffic, run your own relay server (see Part 4) or use a VPN like Tailscale/WireGuard.

---

## Part 4 — Self-host a RustDesk server (optional, for privacy / full control)

If you do not want to use RustDesk's public servers, run the open-source server on a Linux machine (a local Raspberry Pi/NAS, or a cheap VPS).

### Server requirements

- Linux host
- Docker and Docker Compose installed
- Ports open: `21114` (API/web), `21115-21119` TCP

### Docker Compose example

```yaml
version: '3'

services:
  hbbs:
    image: rustdesk/hbbs:latest
    container_name: hbbs
    network_mode: host
    restart: unless-stopped
    volumes:
      - ./data:/root

  hbbr:
    image: rustdesk/hbbr:latest
    container_name: hbbr
    network_mode: host
    restart: unless-stopped
    volumes:
      - ./data:/root
```

### Start the server

```bash
mkdir rustdesk && cd rustdesk
# paste the compose file above into docker-compose.yml
sudo docker compose up -d
```

### Get the public key

```bash
cat ./data/id_ed25519.pub
```

Copy the full key including the trailing `=`.

### Configure both clients

On both Windows and Mac:

1. Open RustDesk → **Settings → Network**.
2. Click **Unlock Network Settings** and enter admin credentials.
3. Set:
   - **ID Server**: your server IP or domain (e.g., `192.168.1.100` or `rd.example.com`)
   - **Relay Server**: same as ID server, or leave blank if deduced automatically
   - **Key**: paste the public key from `id_ed25519.pub`
4. Click **Apply**.
5. Restart RustDesk.

Now both machines register with your private server, and connections show as **"Direct and Encrypted"**.

---

## Part 5 — Network / firewall checklist

| Location | What to allow |
|---|---|
| Windows PC | RustDesk executable allowed in Windows Defender Firewall; inbound port 21118 if using direct IP |
| macOS | RustDesk allowed for Screen Recording, Accessibility, Input Monitoring |
| Router (if self-hosting) | Forward ports 21114–21119 TCP to your server; or use NAT hairpin if accessing by public domain from inside LAN |
| VPN (optional) | If not home, use Tailscale/WireGuard; then connect to Windows via its VPN IP |

---

## Quick summary of what goes on each machine

| Machine | Main tasks |
|---|---|
| **Windows PC** | Install RustDesk as service, set permanent password, enable direct IP access if desired, allow firewall. |
| **Mac** | Install RustDesk, grant Screen Recording / Accessibility / Input Monitoring, connect via Windows ID or LAN IP. |
| **Optional server** | Run `rustdesk/hbbs` + `rustdesk/hbbr` via Docker, open ports, copy public key to both clients. |

---

## References

- [RustDesk Client Documentation](https://rustdesk.com/docs/en/client/)
- [RustDesk macOS Documentation](https://rustdesk.com/docs/en/client/mac/)
- [RustDesk Windows Documentation](https://rustdesk.com/docs/en/client/windows/)
- [RustDesk Self-Host Client Configuration](https://rustdesk.com/docs/en/self-host/client-configuration/)
- [RustDesk GitHub Releases](https://github.com/rustdesk/rustdesk/releases/latest)
