#!/bin/bash
# View trade logs from the SQLite database (no app required)
# Usage: ./bin/trade_logs.sh [trade_id]

set -euo pipefail

DB="database.db"

if [ ! -f "$DB" ]; then
    echo "Database not found: $DB"
    exit 1
fi

# All DB access via python3 (no sqlite3 CLI needed)
run_query() {
    python3 -c "
import sqlite3, sys, json

db = sqlite3.connect('$DB')
db.row_factory = sqlite3.Row
action = sys.argv[1]
arg = sys.argv[2] if len(sys.argv) > 2 else ''

if action == 'list':
    rows = db.execute('''
        SELECT trade_id, trade_type, entry_price, exit_price, result, result_type, created_at
        FROM trades ORDER BY created_at DESC LIMIT 15
    ''').fetchall()
    if not rows:
        print('  (no trades)')
    else:
        print(f\"  {'trade_id':<38s} {'type':<6s} {'entry':>10s} {'exit':>10s} {'result':>8s} {'r_type':<8s} {'created_at'}\")
        print('  ' + '-' * 110)
        for r in rows:
            tid = r['trade_id'][:36]
            exit_p = f\"{r['exit_price']:.2f}\" if r['exit_price'] else '-'
            res = f\"{r['result']:.2f}\" if r['result'] is not None else '-'
            rt = r['result_type'] or '-'
            print(f\"  {tid:<38s} {r['trade_type'] or '-':<6s} {r['entry_price']:>10.2f} {exit_p:>10s} {res:>8s} {rt:<8s} {r['created_at'] or ''}\")

elif action == 'list_e2e':
    rows = db.execute('''
        SELECT trade_id, result_type, result, created_at
        FROM trades WHERE entry_price = 21000.0 AND take_profit = 21080.0
        ORDER BY created_at DESC LIMIT 15
    ''').fetchall()
    if not rows:
        print('  (no E2E test trades)')
    else:
        print(f\"  {'trade_id':<38s} {'r_type':<8s} {'result':>8s} {'created_at'}\")
        print('  ' + '-' * 80)
        for r in rows:
            res = f\"{r['result']:.2f}\" if r['result'] is not None else '-'
            rt = r['result_type'] or '-'
            print(f\"  {r['trade_id']:<38s} {rt:<8s} {res:>8s} {r['created_at'] or ''}\")

elif action == 'logs':
    row = db.execute('SELECT * FROM trades WHERE trade_id = ?', (arg,)).fetchone()
    if not row:
        print(f'  Trade not found: {arg}')
    else:
        print(f\"  Type: {row['trade_type']}  Entry: {row['entry_price']}  SL: {row['stop_loss']}  TP: {row['take_profit']}\")
        exit_p = row['exit_price']
        if exit_p:
            print(f\"  Exit: {exit_p}  Result: {row['result']}R  Type: {row['result_type']}\")
        else:
            print('  (still open)')
        print()
        raw = row['logs']
        if not raw:
            print('  (no logs)')
        else:
            logs = json.loads(raw) if isinstance(raw, str) else raw
            for e in logs:
                ts = e.get('ts', '')
                time_part = ts[11:19] if len(ts) >= 19 else ts
                print(f\"  {time_part}  {e.get('event', ''):<15s}{e.get('msg', '')}\")

db.close()
" "$@"
}

# If trade_id passed as argument, show it directly
if [ -n "${1:-}" ]; then
    echo ""
    run_query logs "$1"
    echo ""
    exit 0
fi

# Interactive mode
BOLD='\033[1m'
CYAN='\033[96m'
RST='\033[0m'

echo -e "${BOLD}${CYAN}  Trade Logs Viewer${RST}"
echo ""
echo "  1) List recent trades"
echo "  2) View logs by trade_id"
echo "  3) List recent E2E test trades"
echo "  4) Quit"
echo ""
read -p "  Choose [1-4]: " choice

case "$choice" in
    1)
        echo ""
        run_query list
        echo ""
        read -p "  View logs for trade_id: " tid
        if [ -n "$tid" ]; then
            echo ""
            run_query logs "$tid"
        fi
        ;;
    2)
        read -p "  Trade ID: " tid
        if [ -n "$tid" ]; then
            echo ""
            run_query logs "$tid"
        fi
        ;;
    3)
        echo ""
        run_query list_e2e
        echo ""
        read -p "  View logs for trade_id: " tid
        if [ -n "$tid" ]; then
            echo ""
            run_query logs "$tid"
        fi
        ;;
    4|q|Q)
        exit 0
        ;;
    *)
        echo "Invalid choice."
        ;;
esac
echo ""
