//+------------------------------------------------------------------+
//|                                     Domain/PlatformApi.mqh       |
//|  Testability seams over the MQL5 global API.                     |
//|  One interface per API group (MQL5 has single inheritance);      |
//|  fakes can replace the real terminal in automated tests.         |
//+------------------------------------------------------------------+
#property strict

//+------------------------------------------------------------------+
//| TradeResult — outcome of a single trade-server request           |
//|  'error' is GetLastError() captured immediately after the send.  |
//+------------------------------------------------------------------+
struct TradeResult
{
   uint   retcode;
   ulong  order;
   double price;
   double volume;
   int    error;
};

//+------------------------------------------------------------------+
//| IAccountApi — account info (AccountInfoInteger/Double)           |
//+------------------------------------------------------------------+
class IAccountApi
{
public:
   virtual ~IAccountApi() {}

   virtual long   Login() = 0;     // ACCOUNT_LOGIN
   virtual double Balance() = 0;   // ACCOUNT_BALANCE
   virtual bool   IsDemo() = 0;    // ACCOUNT_TRADE_MODE == DEMO
};

//+------------------------------------------------------------------+
//| ISymbolApi — symbol selection and quotes                         |
//+------------------------------------------------------------------+
class ISymbolApi
{
public:
   virtual ~ISymbolApi() {}

   virtual bool   Select(string sym) = 0;                       // SymbolSelect(sym, true)
   virtual double Point(string sym) = 0;                        // SYMBOL_POINT
   virtual int    Digits(string sym) = 0;                       // SYMBOL_DIGITS
   virtual double Bid(string sym) = 0;                          // SYMBOL_BID
   virtual double Ask(string sym) = 0;                          // SYMBOL_ASK
   virtual bool   Tick(string sym, MqlTick &tick) = 0;          // SymbolInfoTick
   virtual double VolumeMin(string sym) = 0;                    // SYMBOL_VOLUME_MIN
   virtual double VolumeMax(string sym) = 0;                    // SYMBOL_VOLUME_MAX
   virtual double VolumeStep(string sym) = 0;                   // SYMBOL_VOLUME_STEP
   virtual double TickValue(string sym) = 0;                    // SYMBOL_TRADE_TICK_VALUE
   virtual double TickSize(string sym) = 0;                     // SYMBOL_TRADE_TICK_SIZE
};

//+------------------------------------------------------------------+
//| ITradeApi — trade-server send (OrderSend)                        |
//|  Handlers keep building MqlTradeRequest; the seam is the send.   |
//+------------------------------------------------------------------+
class ITradeApi
{
public:
   virtual ~ITradeApi() {}

   virtual bool Send(MqlTradeRequest &request, TradeResult &result) = 0;
};

//+------------------------------------------------------------------+
//| IMarketDataApi — M1 bars (iTime/CopyRates/Bars)                  |
//+------------------------------------------------------------------+
class IMarketDataApi
{
public:
   virtual ~IMarketDataApi() {}

   virtual datetime CurrentBarTime(string sym) = 0;                               // iTime(sym, PERIOD_M1, 0)
   virtual int      CopyM1Rates(string sym, int startPos, int count, MqlRates &rates[]) = 0;
   virtual int      BarsCount(string sym, datetime start, datetime end) = 0;      // Bars(sym, PERIOD_M1, start, end)
};

//+------------------------------------------------------------------+
//| ITimeApi — wall/tick clocks (TimeCurrent/GetTickCount)           |
//+------------------------------------------------------------------+
class ITimeApi
{
public:
   virtual ~ITimeApi() {}

   virtual long     TickCount() = 0;   // GetTickCount()
   virtual datetime Now() = 0;         // TimeCurrent()
};

//+------------------------------------------------------------------+
//| IPositionApi — open positions iteration                          |
//|  TicketByIndex/SelectByTicket select the position; the getters   |
//|  then read the currently selected position (MQL5 semantics).     |
//+------------------------------------------------------------------+
class IPositionApi
{
public:
   virtual ~IPositionApi() {}

   virtual int    Total() = 0;                          // PositionsTotal()
   virtual ulong  TicketByIndex(int index) = 0;         // PositionGetTicket(index)
   virtual bool   SelectByTicket(ulong ticket) = 0;     // PositionSelectByTicket
   virtual long   Magic() = 0;                          // POSITION_MAGIC
   virtual string Comment() = 0;                        // POSITION_COMMENT
   virtual long   Type() = 0;                           // POSITION_TYPE
   virtual double PriceOpen() = 0;                      // POSITION_PRICE_OPEN
   virtual double StopLoss() = 0;                       // POSITION_SL
   virtual double TakeProfit() = 0;                     // POSITION_TP
   virtual double Volume() = 0;                         // POSITION_VOLUME
};

//+------------------------------------------------------------------+
//| IDealHistoryApi — deal history for fill detection                |
//+------------------------------------------------------------------+
class IDealHistoryApi
{
public:
   virtual ~IDealHistoryApi() {}

   virtual bool   Select(datetime from, datetime to) = 0;        // HistorySelect
   virtual int    Total() = 0;                                   // HistoryDealsTotal
   virtual ulong  TicketByIndex(int index) = 0;                  // HistoryDealGetTicket
   virtual long   Magic(ulong ticket) = 0;                       // DEAL_MAGIC
   virtual long   Entry(ulong ticket) = 0;                       // DEAL_ENTRY
   virtual long   Reason(ulong ticket) = 0;                      // DEAL_REASON
   virtual double Price(ulong ticket) = 0;                       // DEAL_PRICE
   virtual string Comment(ulong ticket) = 0;                     // DEAL_COMMENT
   virtual long   OrderTicket(ulong ticket) = 0;                 // DEAL_ORDER
   virtual long   Time(ulong ticket) = 0;                        // DEAL_TIME
   virtual double Volume(ulong ticket) = 0;                      // DEAL_VOLUME
   virtual double Profit(ulong ticket) = 0;                      // DEAL_PROFIT
   virtual double Commission(ulong ticket) = 0;                  // DEAL_COMMISSION

   //--- SL/TP lookup on the order that produced an entry fill
   virtual bool   SelectOrder(ulong orderTicket) = 0;            // HistoryOrderSelect
   virtual double OrderStopLoss(ulong orderTicket) = 0;          // ORDER_SL
   virtual double OrderTakeProfit(ulong orderTicket) = 0;        // ORDER_TP
};

//+------------------------------------------------------------------+
//| PlatformApis — plain holder so constructors take a single param  |
//+------------------------------------------------------------------+
class PlatformApis
{
public:
   IAccountApi     *account;
   ISymbolApi      *symbol;
   ITradeApi       *trade;
   IMarketDataApi  *marketData;
   ITimeApi        *time;
   IPositionApi    *position;
   IDealHistoryApi *dealHistory;

   PlatformApis()
   {
      account = NULL;
      symbol = NULL;
      trade = NULL;
      marketData = NULL;
      time = NULL;
      position = NULL;
      dealHistory = NULL;
   }
};
