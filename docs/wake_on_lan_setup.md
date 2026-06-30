# Wake-on-LAN Setup Guide

This guide covers waking a Windows PC from a Mac on the same local network using Wake-on-LAN (WoL).

## Use case

You want to turn on a Windows PC remotely from your Mac, then connect to it with RustDesk or another remote desktop tool.

## Hardware used in this guide

- Motherboard: **MSI B550M PRO-VDH WIFI (MS-7C95)**
- Network adapter: **Realtek PCIe GbE Family Controller**
- Windows PC connected by **Ethernet**
- Mac connected by **Wi-Fi**

> WoL over Wi-Fi is less reliable and not covered here. Use a wired Ethernet connection on the PC you want to wake.

---

## Part 1 — Check adapter support before changing BIOS

Before rebooting into BIOS, confirm the Ethernet adapter actually supports wake events.

### 1. PowerShell commands

Run these in PowerShell as Administrator on the Windows PC:

```powershell
# Devices currently allowed to wake the PC
powercfg /devicequery wake_armed

# All devices that can wake the PC
powercfg /devicequery wake_from_any

# Why the PC last woke
powercfg /lastwake

# Available sleep states
powercfg /a
```

Look for **Realtek PCIe GbE Family Controller** (or your Ethernet adapter) in the output. If it appears, the adapter supports wake events.

### 2. Device Manager check

1. Open **Device Manager**.
2. Expand **Network adapters**.
3. Right-click **Realtek PCIe GbE Family Controller** → **Properties**.
4. Go to the **Power Management** tab.
5. Check:
   - **Allow the computer to turn off this device to save power**
   - **Allow this device to wake the computer**
   - **Only allow a magic packet to wake the computer**

6. Go to the **Advanced** tab and set:
   - **Wake on Magic Packet** → **Enabled**
   - **Shutdown Wake-On-Lan** → **Enabled**
   - **Wake on Pattern Match** → **Enabled** (optional)
   - **Energy Efficient Ethernet** → **Disabled**
   - **Green Ethernet** → **Disabled**

7. Click **OK**.

> If your adapter does not show these options, your hardware may not support WoL, or you may need a different driver.

---

## Part 2 — BIOS settings

Reboot the PC and enter BIOS/UEFI. On MSI boards this is usually **Delete** or **F2** during boot.

Navigate to: **Settings → Advanced → Wake Up Event Setup**

| Setting | Value |
|---|---|
| Wake Up Event By | BIOS (OS may also work later) |
| Resume By PCI-E Device | **Enabled** |
| Resume By USB Device | Enabled (optional) |
| Resume By RTC Alarm | Disabled |

The critical setting is **Resume By PCI-E Device = Enabled**. Without it, the motherboard may receive the magic packet but ignore the wake event.

Save and exit BIOS.

---

## Part 3 — Disable Fast Startup (if WoL from shutdown doesn't work)

Windows Fast Startup can prevent WoL from a full shutdown.

1. Open **Control Panel → Power Options → Choose what the power buttons do**.
2. Click **Change settings that are currently unavailable**.
3. Uncheck **Turn on fast startup (recommended)**.
4. Save changes.

---

## Part 4 — Get the target MAC address

On the Windows PC, run:

```powershell
getmac
```

Find the MAC address for **Realtek PCIe GbE Family Controller**. It looks like:

```
04-7C-16-E4-7D-83
```

Write it down. You will use it in the magic packet.

---

## Part 5 — Send the magic packet from macOS

### Install wakeonlan

```bash
brew install wakeonlan
```

### Send the packet

Use the broadcast address of your local network and the Windows MAC address:

```bash
wakeonlan -i 192.168.1.255 04:7C:16:E4:7D:83
```

Replace `192.168.1.255` with your network's broadcast address and the MAC with your PC's MAC address.

> If your network is `192.168.0.x`, the broadcast address is `192.168.0.255`.

---

## Part 6 — Verify the magic packet

You can confirm the packet is being sent correctly using Wireshark.

1. Install Wireshark on either machine.
2. Capture on the Ethernet adapter (on Windows) or Wi-Fi adapter (on Mac).
3. Apply a display filter: `udp.port == 9` or `wol`
4. Send the magic packet from the Mac.

You should see a packet with:

- Destination MAC: `ff:ff:ff:ff:ff:ff`
- Destination IP: `192.168.1.255`
- UDP Port: `9`
- Payload: `FF FF FF FF FF FF` followed by the target MAC repeated 16 times

---

## Part 7 — Full workflow with RustDesk

1. Send the magic packet from the Mac.
2. Wait 10–20 seconds for the PC to boot.
3. Connect from Mac to Windows with RustDesk using the Windows LAN IP or RustDesk ID.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| PC doesn't wake at all | BIOS PCI-E wake disabled | Enable **Resume By PCI-E Device** |
| PC wakes from sleep but not shutdown | Fast Startup enabled | Disable Fast Startup |
| Magic packet not seen in Wireshark | Wrong broadcast address or network | Use the correct subnet broadcast (`192.168.x.255`) |
| PC wakes randomly | Wake on Pattern Match enabled | Disable it or restrict wake sources |
| Adapter missing WoL options | Driver or hardware limitation | Update Realtek driver or check motherboard manual |

---

## References

- [RustDesk macOS Documentation](https://rustdesk.com/docs/en/client/mac/)
- [RustDesk Windows Documentation](https://rustdesk.com/docs/en/client/windows/)
