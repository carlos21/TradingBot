#!/bin/bash

# connect_windows_pc.sh
# Wakes up a Windows PC with Wake-on-LAN, scans the local network to find it by
# MAC address or hostname, then opens a RustDesk connection with the configured
# password.

set -euo pipefail

# Configuration
# Default Windows PC MAC address (edit this if your PC's MAC changes)
DEFAULT_MAC="04:7c:16:e4:7d:83"

# Wake-on-LAN configuration
WOL_BROADCAST="192.168.1.255"
WOL_MAC="04:7C:16:E4:7D:83"
WOL_RETRIES=5
WOL_WAIT_SEC=20
WOL_INITIAL_WAIT=15
WOL_BURST=3
WOL_RETRY_BURST=2

# RustDesk password
# Single-quoted to preserve special characters literally (do not change to double quotes)
RUSTDESK_PASSWORD='2b*wztX9j9}:z$4Ry@'
RUSTDESK_BIN="${RUSTDESK_BIN:-}"

# Discovery defaults
MAC_FILTER=""
HOST_FILTER=""
TIMEOUT_MS=200
LIST_ALL=false
USE_WOL=true
DEBUG=false

# Print a debug line when -d/--debug is set.
debug() {
    if [[ "$DEBUG" == true ]]; then
        echo -e "${BLUE}[debug]${NC} $*"
    fi
}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Wakes up the Windows PC (if needed), scans the local network to find it,
and connects via RustDesk.

Options:
  -m MAC         Partial or full MAC address of the Windows PC (e.g., "aa:bb:cc")
  -n HOSTNAME    Hostname of the Windows PC (case-insensitive substring match)
  -a             List all discovered devices and exit without connecting
  -d             Print debug output (interfaces, ARP parsing, match decisions)
  -t TIMEOUT     Ping timeout in milliseconds (default: 200)
  -B BROADCAST   Wake-on-LAN broadcast address (default: $WOL_BROADCAST)
  -M MAC         Wake-on-LAN target MAC address (default: $WOL_MAC)
  -r COUNT       Number of scan retries after waking (default: $WOL_RETRIES)
  -w SECONDS     Seconds to wait between scan retries (default: $WOL_WAIT_SEC)
  --no-wol       Skip sending the Wake-on-LAN magic packet
  -p PASSWORD    RustDesk password (default: configured in script)
  -b BINARY      Path to RustDesk executable
  -h             Show this help

Environment:
  RUSTDESK_BIN  Path to RustDesk executable

Examples:
  $0                          # wake default PC, find it, and connect
  $0 --no-wol                 # skip Wake-on-LAN
  $0 -m aa:bb:cc
  $0 -n DESKTOP-JOHN
  $0 -a                       # list all discovered devices
EOF
}

# Pre-process long options before getopts
args=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-wol) USE_WOL=false; shift ;;
        --debug) DEBUG=true; shift ;;
        -h|-\?|--help) usage; exit 0 ;;
        *) args+=("$1"); shift ;;
    esac
done
if [[ ${#args[@]} -eq 0 ]]; then
    set --
else
    set -- "${args[@]}"
fi

while getopts "m:n:adt:B:M:r:w:p:b:h" opt; do
    case $opt in
        m) MAC_FILTER="$OPTARG" ;;
        n) HOST_FILTER="$OPTARG" ;;
        a) LIST_ALL=true ;;
        d) DEBUG=true ;;
        t) TIMEOUT_MS="$OPTARG" ;;
        B) WOL_BROADCAST="$OPTARG" ;;
        M) WOL_MAC="$OPTARG" ;;
        r) WOL_RETRIES="$OPTARG" ;;
        w) WOL_WAIT_SEC="$OPTARG" ;;
        p) RUSTDESK_PASSWORD="$OPTARG" ;;
        b) RUSTDESK_BIN="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done

# Lowercase helper for bash 3.2 compatibility (macOS default)
lowercase() {
    echo "$1" | tr '[:upper:]' '[:lower:]'
}

# Normalize MAC address to colon-separated bytes with no leading zeros.
# Handles formats like 04-7C-16-E4-7D-83, 04:7c:16:e4:7d:83,
# and macOS ARP output that drops leading zeros (e.g., 4:7c:16:e4:7d:83).
normalize_mac() {
    local mac
    mac=$(lowercase "$1" | tr '-' ':')
    echo "$mac" | awk -F: '{
        out=""
        for(i=1;i<=NF;i++) {
            gsub(/^0+/, "", $i)
            if ($i == "") $i = "0"
            out = out (i>1?":":"") $i
        }
        print out
    }'
}

# Send Wake-on-LAN magic packet(s) to the configured broadcast address and MAC.
# Optional argument: number of packets to send (default: WOL_BURST).
send_wol() {
    local count="${1:-$WOL_BURST}"

    if ! command -v wakeonlan >/dev/null 2>&1; then
        echo -e "${YELLOW}Warning:${NC} wakeonlan not found in PATH. Skipping Wake-on-LAN." >&2
        echo "Install it with: brew install wakeonlan" >&2
        return 0
    fi

    echo -e "${BLUE}Sending ${count} Wake-on-LAN magic packet(s) to ${WOL_MAC} via ${WOL_BROADCAST}${NC}"
    local i
    for ((i = 0; i < count; i++)); do
        wakeonlan -i "$WOL_BROADCAST" "$WOL_MAC" || true
        if ((i < count - 1)); then
            sleep 1
        fi
    done
}

# Scan the local subnet and populate MATCH_IP/MATCH_HOST/MATCH_MAC if the target
# is found. Always returns 0; callers check MATCH_IP to see if the PC was found.
MATCH_IP=""
MATCH_HOST=""
MATCH_MAC=""
discover_pc() {
    local label="${1:-}"
    local found=0

    MATCH_IP=""
    MATCH_HOST=""
    MATCH_MAC=""

    echo ""
    if [[ -n "$label" ]]; then
        echo -e "${YELLOW}Scanning...${NC} $label"
    else
        echo -e "${YELLOW}Scanning...${NC}"
    fi

    # Ping sweep in parallel
    local i ip
    for i in $(seq 1 254); do
        ip="${SUBNET}.${i}"
        ping -c 1 -W "$PING_WAIT_ARG" "$ip" >/dev/null 2>&1 &
    done
    wait

    echo ""
    echo -e "${YELLOW}ARP results:${NC}"
    echo "------------------------------"

    local arp_total=0 arp_resolved=0 arp_incomplete=0
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        arp_total=$((arp_total + 1))

        # Parse arp -a output format on macOS:
        # ? (192.168.1.10) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]
        local ip mac host mac_normalized host_lower
        ip=$(echo "$line" | sed -n 's/.*[(]\([0-9.]*\)[)].*/\1/p')
        mac=$(echo "$line" | sed -n 's/.*at \([0-9a-f:]*\).*/\1/p')
        host=$(echo "$line" | awk '{print $1}')

        if [[ -z "$ip" ]]; then
            debug "skip (no IP parsed): $line"
            continue
        fi
        if [[ -z "$mac" || "$mac" == "(incomplete)" ]]; then
            arp_incomplete=$((arp_incomplete + 1))
            continue
        fi
        arp_resolved=$((arp_resolved + 1))

        mac_normalized=$(normalize_mac "$mac")
        host_lower=$(lowercase "$host")

        local match=0
        if [[ "$LIST_ALL" == true ]]; then
            match=1
        elif [[ -n "$MAC_FILTER_NORMALIZED" && "$mac_normalized" == *"$MAC_FILTER_NORMALIZED"* ]]; then
            match=1
        elif [[ -n "$HOST_FILTER_LOWER" && "$host_lower" == *"$HOST_FILTER_LOWER"* ]]; then
            match=1
        fi
        debug "entry: $host -> $ip ($mac_normalized) match=$match"

        if [[ "$match" -eq 1 ]]; then
            if [[ "$LIST_ALL" == true ]]; then
                printf "%-25s -> %-15s (%s)\n" "$host" "$ip" "$mac"
            else
                echo -e "${GREEN}Found:${NC} $host -> $ip ($mac)"
            fi
            found=$((found + 1))
            if [[ -z "$MATCH_IP" ]]; then
                MATCH_IP="$ip"
                MATCH_HOST="$host"
                MATCH_MAC="$mac"
            fi
        fi
    done < <(arp -a)

    debug "ARP entries: $arp_total total, $arp_resolved resolved"
    if [[ "$arp_resolved" -eq 0 ]]; then
        debug "no resolved ARP entries at all — ping sweep may not be reaching the subnet"
    fi

    return 0
}

# If no filter specified and not listing all, use the default Windows PC MAC
if [[ "$LIST_ALL" == false && -z "$MAC_FILTER" && -z "$HOST_FILTER" ]]; then
    MAC_FILTER="$DEFAULT_MAC"
fi

MAC_FILTER_NORMALIZED=$(normalize_mac "$MAC_FILTER")
HOST_FILTER_LOWER=$(lowercase "$HOST_FILTER")

# Validate timeout and compute the right -W argument for ping.
# macOS/BSD ping expects milliseconds; Linux ping expects seconds.
if ! [[ "$TIMEOUT_MS" =~ ^[0-9]+$ ]]; then
    echo -e "${YELLOW}Warning:${NC} Invalid timeout '$TIMEOUT_MS'; using 1000ms." >&2
    TIMEOUT_MS=1000
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
    PING_WAIT_ARG="$TIMEOUT_MS"
else
    PING_WAIT_ARG=$(awk "BEGIN {printf \"%.1f\", $TIMEOUT_MS/1000}")
fi

# Get the default gateway and subnet
DEFAULT_ROUTE=$(route -n get default 2>/dev/null)
GATEWAY=$(echo "$DEFAULT_ROUTE" | awk '/gateway:/{print $2}' | head -1)
DEFAULT_IFACE=$(echo "$DEFAULT_ROUTE" | awk '/interface:/{print $2}' | head -1)
if [[ -z "$GATEWAY" ]]; then
    echo -e "${RED}Error:${NC} Could not determine default gateway. Are you connected to a network?"
    exit 1
fi

# Derive subnet from gateway (assumes /24 home network)
SUBNET=$(echo "$GATEWAY" | sed 's/\.[0-9]*$//')
NETWORK="${SUBNET}.0/24"

debug "default interface: ${DEFAULT_IFACE:-unknown}"
debug "interface config: $(ifconfig "$DEFAULT_IFACE" 2>/dev/null | grep 'inet ' || echo 'no IPv4 address')"
debug "MAC filter: '${MAC_FILTER:-none}' (normalized: '${MAC_FILTER_NORMALIZED:-none}')"
debug "hostname filter: '${HOST_FILTER:-none}'"
debug "ping -W argument: $PING_WAIT_ARG ($(uname -s) mode)"

echo -e "${BLUE}Gateway:${NC}   $GATEWAY"
echo -e "${BLUE}Network:${NC}   $NETWORK"
if [[ "$LIST_ALL" == true ]]; then
    echo -e "${BLUE}Mode:${NC}      List all discovered devices"
elif [[ -n "$MAC_FILTER" ]]; then
    echo -e "${BLUE}Filter:${NC}    MAC contains '$MAC_FILTER'"
elif [[ -n "$HOST_FILTER" ]]; then
    echo -e "${BLUE}Filter:${NC}    Hostname contains '$HOST_FILTER'"
fi
echo -e "${BLUE}Timeout:${NC}   ${TIMEOUT_MS}ms per host"

# For list-all mode, just scan once and exit; no WoL or retries needed
if [[ "$LIST_ALL" == true ]]; then
    discover_pc
    echo ""
    echo -e "${GREEN}Done.${NC}"
    exit 0
fi

# Send Wake-on-LAN magic packet before scanning
TOTAL_WAIT=0
if [[ "$USE_WOL" == true ]]; then
    send_wol
    echo -e "${YELLOW}Waiting ${WOL_INITIAL_WAIT}s for the PC to wake up before scanning...${NC}"
    sleep "$WOL_INITIAL_WAIT"
    TOTAL_WAIT=$WOL_INITIAL_WAIT
fi

# Scan with retries to give the PC time to wake up
ATTEMPT=0
while [[ $ATTEMPT -le $WOL_RETRIES ]]; do
    if [[ $ATTEMPT -gt 0 ]]; then
        echo ""
        echo -e "${YELLOW}PC not found yet. Waiting ${WOL_WAIT_SEC}s before retry ${ATTEMPT}/${WOL_RETRIES} (waited ${TOTAL_WAIT}s so far)...${NC}"
        sleep "$WOL_WAIT_SEC"
        TOTAL_WAIT=$((TOTAL_WAIT + WOL_WAIT_SEC))
        if [[ "$USE_WOL" == true ]]; then
            send_wol "$WOL_RETRY_BURST"
        fi
    fi

    discover_pc "attempt $((ATTEMPT + 1))/$((WOL_RETRIES + 1))"

    if [[ -n "$MATCH_IP" ]]; then
        break
    fi

    ATTEMPT=$((ATTEMPT + 1))
done

if [[ -z "$MATCH_IP" ]]; then
    echo ""
    echo -e "${RED}No matching device found after ${WOL_RETRIES} retries (${TOTAL_WAIT}s waited).${NC}"
    echo ""
    echo "Suggestions:"
    echo "  1. Make sure the Windows PC supports Wake-on-LAN and it is enabled in BIOS/Windows."
    echo "  2. Make sure the PC is connected via Ethernet (WoL usually does not work over Wi-Fi)."
    echo "  3. Double-check the MAC address or hostname."
    echo "  4. Increase the timeout with -t (e.g., -t 500) or retries with -r."
    echo "  5. Run the script again after the PC has been online for a minute."
    exit 1
fi

# Locate RustDesk binary if not explicitly provided
if [[ -z "$RUSTDESK_BIN" ]]; then
    if command -v rustdesk >/dev/null 2>&1; then
        RUSTDESK_BIN="rustdesk"
    elif [[ -x "/Applications/RustDesk.app/Contents/MacOS/RustDesk" ]]; then
        RUSTDESK_BIN="/Applications/RustDesk.app/Contents/MacOS/RustDesk"
    elif [[ -x "/Applications/RustDesk.app/Contents/MacOS/rustdesk" ]]; then
        RUSTDESK_BIN="/Applications/RustDesk.app/Contents/MacOS/rustdesk"
    else
        echo -e "${RED}Error:${NC} RustDesk binary not found." >&2
        echo "Set RUSTDESK_BIN or install RustDesk in /Applications." >&2
        exit 1
    fi
fi

echo ""
echo -e "${GREEN}Connecting to $MATCH_HOST ($MATCH_IP) via RustDesk...${NC}"

# Launch RustDesk. The password is passed as a single quoted argument so shell
# metacharacters are not expanded.
exec "$RUSTDESK_BIN" --connect "$MATCH_IP" --password "$RUSTDESK_PASSWORD"
