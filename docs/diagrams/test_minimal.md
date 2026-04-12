# Test Minimal

```mermaid
graph LR
    A[Python] --> B[NinjaTrader]
    B --> C[Market]
```

```mermaid
sequenceDiagram
    Python->>NinjaTrader: ORDER_OPEN
    NinjaTrader->>Python: ENTRY_FILL
```
