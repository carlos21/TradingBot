#!/usr/bin/env python3
"""
Generate an HTML file with all Mermaid diagrams for easy viewing.
Open the generated HTML file in any browser.
"""

import os

# The diagrams (using simpler syntax for maximum compatibility)
DIAGRAMS = {
    "Socket Architecture": """
graph LR
    subgraph Python
        P1[SUB :5555 Market Data]
        P2[PUSH :5556 Commands]
        P3[REP :5557 Queries]
        P4[SUB :5558 Heartbeat]
    end
    
    subgraph NinjaTrader
        N1[PUB :5555]
        N2[PULL :5556]
        N3[REQ :5557]
        N4[PUB :5558]
    end
    
    N1 -->|Ticks/Bars| P1
    P2 -->|Orders| N2
    N3 <-->|Sync| P3
    N4 -->|Health| P4
""",
    "Trade Lifecycle (SL Modification WORKING)": """
sequenceDiagram
    participant PY as Python
    participant GW as Gateway
    participant NT as NinjaTrader
    participant ATM as ATM Strategy
    participant MK as Market

    Note over PY,MK: Initialization
    NT->>PY: CONNECT + History
    loop Live Market
        MK->>NT: Price
        NT->>PY: TICK
    end

    Note over PY,MK: 1. ENTRY
    PY->>GW: send_open_order
    GW->>NT: ORDER_OPEN
    NT->>ATM: Market order
    ATM->>MK: Entry order
    MK->>ATM: Fill
    ATM->>ATM: Auto SL/TP
    ATM->>NT: Filled
    NT->>NT: Track SL order
    NT->>PY: ENTRY_FILL
    PY->>PY: DB Status=OPEN

    Note over PY,MK: 2. MODIFY SL (WORKING)
    PY->>GW: send_modify_order
    GW->>NT: ORDER_MODIFY
    NT->>NT: Get cached SL order
    alt Not cached
        NT->>NT: Search Account.Orders
    end
    NT->>NT: Validate state
    NT->>ATM: ChangeOrder(newSL)
    ATM->>MK: Update stop
    NT->>PY: TRADE_LOG NT:MODIFY
    PY->>PY: Log SL change

    Note over PY,MK: 3. TP HIT - WIN
    MK->>ATM: Price hits TP
    ATM->>MK: Execute TP
    MK->>ATM: Fill
    ATM->>ATM: Cancel SL/Close
    ATM->>NT: TP filled
    NT->>PY: EXIT_FILL TP
    PY->>PY: DB Status=CLOSED_WIN
""",
    "State Machine": """
stateDiagram-v2
    [*] --> Pending: send_open_order
    Pending --> Open: ENTRY_FILL
    Pending --> Rejected: ORDER_REJECTED
    Open --> Modified: send_modify_order
    Open --> Closed: EXIT_FILL
    Modified --> Closed: EXIT_FILL
    Closed --> [*]: Update DB
    Rejected --> [*]: Log error
""",
    "SL Modification Flow": """
sequenceDiagram
    participant PY as Python
    participant NT as NinjaTrader
    participant ATM as ATM Strategy

    PY->>NT: ORDER_MODIFY<br/>{trade_id, stop_loss}
    
    NT->>NT: _stopLossOrders.TryGetValue()
    
    alt Found in cache
        NT->>NT: Use cached Order
    else Not found
        NT->>NT: FindStopOrderForTrade()
        NT->>NT: Search Account.Orders
        NT->>NT: Cache result
    end
    
    NT->>NT: Validate OrderState<br/>Working or Accepted
    
    alt Valid state
        NT->>ATM: _account.ChangeOrder()
        ATM->>ATM: Update stop price
        NT->>PY: TRADE_LOG<br/>event: NT:MODIFY
    else Invalid state
        NT->>PY: TRADE_LOG<br/>event: NT:ERROR
    end
""",
}

def main():
    diagrams_html = ""
    
    for title, content in DIAGRAMS.items():
        diagrams_html += f"""
    <h2>{title}</h2>
    <div class="diagram">
        <div class="mermaid">
{content.strip()}
        </div>
    </div>
"""
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>ZMQ Communication Flow Diagrams</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }}
        h1 {{ color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 10px; }}
        h2 {{ color: #0066cc; margin-top: 40px; }}
        .diagram {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .mermaid {{ text-align: center; }}
        code {{ background: #e9ecef; padding: 2px 6px; border-radius: 3px; }}
        .note {{ background: #fff3cd; padding: 10px; border-left: 4px solid #ffc107; margin: 10px 0; }}
        .success {{ background: #d4edda; padding: 10px; border-left: 4px solid #28a745; margin: 10px 0; }}
    </style>
</head>
<body>
    <h1>ZeroMQ Communication Flow Diagrams</h1>
    <p>Python TradingBot ↔ NinjaTrader via ZeroMQ</p>
    
    <div class="success">
        <strong>✅ STOP LOSS MODIFICATION IS NOW WORKING!</strong><br>
        The <code>order_modify</code> command now fully supports updating stop loss orders via 
        <code>_account.ChangeOrder()</code> while maintaining the ATM strategy attachment.
    </div>
    
    <div class="note">
        <strong>Socket Configuration:</strong><br>
        • Port 5555 (PUB/SUB): Market data from NT → Python<br>
        • Port 5556 (PUSH/PULL): Commands from Python → NT<br>
        • Port 5557 (REQ/REP): Synchronous queries<br>
        • Port 5558 (PUB/SUB): Heartbeats from NT → Python
    </div>

    <h2>Key Implementation Details</h2>
    <div class="note">
        <strong>Stop Loss Modification:</strong>
        <ul>
            <li>Stop orders are tracked in <code>_stopLossOrders[trade_id]</code></li>
            <li>OnOrderUpdate caches stop orders when they appear</li>
            <li>HandleModifyOrder uses <code>_account.ChangeOrder()</code> to modify</li>
            <li>ATM strategy remains attached after modification</li>
            <li>Confirmation sent via TRADE_LOG with event: NT:MODIFY</li>
        </ul>
    </div>

{diagrams_html}
    
    <script>
        mermaid.initialize({{ startOnLoad: true }});
    </script>
</body>
</html>
"""
    
    output_path = os.path.join(os.path.dirname(__file__), "zmq_diagrams.html")
    with open(output_path, "w") as f:
        f.write(html)
    
    print(f"Generated: {output_path}")
    print(f"Open this file in any browser to view the diagrams")

if __name__ == "__main__":
    main()
