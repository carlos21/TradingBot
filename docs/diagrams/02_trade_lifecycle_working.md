# Trade Lifecycle: Working SL Modification

```mermaid
sequenceDiagram
    participant PY as Python TradingBot
    participant GW as TradingGateway
    participant NT as NinjaTrader
    participant ATM as ATM Strategy
    participant MK as Market

    Note over PY,MK: === 1. TRADE ENTRY ===
    PY->>GW: send_open_order()
    GW->>NT: ORDER_OPEN
    NT->>ATM: Start ATM Strategy
    ATM->>MK: Entry order
    MK->>ATM: Fill at entry price
    ATM->>ATM: Auto-create SL order
    ATM->>ATM: Auto-create TP order
    ATM->>NT: Entry filled
    NT->>NT: Track SL order in _stopLossOrders
    NT->>PY: ENTRY_FILL

    Note over PY,MK: === 2. MODIFY SL (NOW WORKING) ===
    PY->>PY: Price moves in favor
    PY->>PY: Strategy decides breakeven
    PY->>GW: send_modify_order(SL=new_value)
    GW->>NT: ORDER_MODIFY
    
    NT->>NT: Look up cached SL order
    alt Order found in cache
        NT->>NT: Use cached Order reference
    else Not cached
        NT->>NT: Search Account.Orders
        NT->>NT: Find matching stop order
    end
    
    NT->>NT: Validate order state (Working/Accepted)
    NT->>ATM: ChangeOrder(newStopPrice)
    ATM->>MK: Update stop order
    MK->>ATM: Confirm modification
    
    NT->>PY: TRADE_LOG (NT:MODIFY success)
    PY->>PY: Log SL change in DB

    Note over PY,MK: === 3. TAKE PROFIT HIT ===
    MK->>ATM: Price hits TP
    ATM->>MK: Execute TP order
    MK->>ATM: Fill
    ATM->>ATM: Cancel SL / Close strategy
    ATM->>NT: TP filled
    NT->>PY: EXIT_FILL (TP)
    PY->>PY: DB Status=CLOSED_WIN
```

## Key Implementation Details

### Stop Order Tracking
```csharp
// Dictionary to cache stop orders
private readonly Dictionary<string, Order> _stopLossOrders = 
    new Dictionary<string, Order>();

// Updated on every order update
OnOrderUpdate() {
    if (order.Name == "Stop") {
        _stopLossOrders[tradeId] = order;
    }
}
```

### Modification Flow
```csharp
HandleModifyOrder(tradeId, newSl) {
    // 1. Get cached order
    if (!_stopLossOrders.TryGetValue(tradeId, out stopOrder)) {
        // 2. Or search for it
        stopOrder = FindStopOrderForTrade(tradeId);
    }
    
    // 3. Validate
    if (stopOrder.OrderState != Working) return error;
    
    // 4. Modify using NinjaTrader API
    _account.ChangeOrder(
        stopOrder,
        stopOrder.Quantity,      // Same qty
        stopOrder.LimitPrice,    // Same limit
        newSl,                   // NEW stop price
        stopOrder.AtmStrategyId  // Keep ATM
    );
}
```

## Error Scenarios Handled

| Scenario | Response |
|----------|----------|
| Order not found | Search account orders, then error if still not found |
| Order not modifiable (Filled/Cancelled) | Error: "Stop order not modifiable" |
| ChangeOrder throws exception | Error logged + sent to Python |
| Account not connected | Error: "No account available" |
