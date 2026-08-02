//+------------------------------------------------------------------+
//|                                Infrastructure/MqlPlatformApi.mqh |
//|  Real I*Api implementations delegating to the MQL5 globals.      |
//|  MqlPlatformApis is the factory used by the composition root.    |
//+------------------------------------------------------------------+
#property strict

#include "../Domain/PlatformApi.mqh"

//+------------------------------------------------------------------+
//| MqlAccountApi — AccountInfoInteger/Double                        |
//+------------------------------------------------------------------+
class MqlAccountApi : public IAccountApi
{
public:
   long   Login() override   { return AccountInfoInteger(ACCOUNT_LOGIN); }
   double Balance() override { return AccountInfoDouble(ACCOUNT_BALANCE); }
   //--- "Demo" here means non-real trading: demo OR contest account
   bool   IsDemo() override
   {
      ENUM_ACCOUNT_TRADE_MODE mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
      return (mode == ACCOUNT_TRADE_MODE_DEMO || mode == ACCOUNT_TRADE_MODE_CONTEST);
   }
};

//+------------------------------------------------------------------+
//| MqlSymbolApi — SymbolSelect/SymbolInfo*                          |
//+------------------------------------------------------------------+
class MqlSymbolApi : public ISymbolApi
{
public:
   bool   Select(string sym) override                 { return SymbolSelect(sym, true); }
   double Point(string sym) override                  { return SymbolInfoDouble(sym, SYMBOL_POINT); }
   int    Digits(string sym) override                 { return (int)SymbolInfoInteger(sym, SYMBOL_DIGITS); }
   double Bid(string sym) override                    { return SymbolInfoDouble(sym, SYMBOL_BID); }
   double Ask(string sym) override                    { return SymbolInfoDouble(sym, SYMBOL_ASK); }
   bool   Tick(string sym, MqlTick &tick) override    { return SymbolInfoTick(sym, tick); }
   double VolumeMin(string sym) override              { return SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN); }
   double VolumeMax(string sym) override              { return SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX); }
   double VolumeStep(string sym) override             { return SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP); }
   double TickValue(string sym) override              { return SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE); }
   double TickSize(string sym) override               { return SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE); }
};

//+------------------------------------------------------------------+
//| MqlTradeApi — OrderSend                                          |
//+------------------------------------------------------------------+
class MqlTradeApi : public ITradeApi
{
public:
   bool Send(MqlTradeRequest &request, TradeResult &result) override
   {
      MqlTradeResult mqlResult = {};
      bool sent = OrderSend(request, mqlResult);
      result.retcode = mqlResult.retcode;
      result.order   = mqlResult.order;
      result.price   = mqlResult.price;
      result.volume  = mqlResult.volume;
      result.error   = sent ? 0 : (int)GetLastError();
      return sent;
   }
};

//+------------------------------------------------------------------+
//| MqlMarketDataApi — iTime/CopyRates/Bars on PERIOD_M1             |
//+------------------------------------------------------------------+
class MqlMarketDataApi : public IMarketDataApi
{
public:
   datetime CurrentBarTime(string sym) override
   {
      return iTime(sym, PERIOD_M1, 0);
   }

   int CopyM1Rates(string sym, int startPos, int count, MqlRates &rates[]) override
   {
      return CopyRates(sym, PERIOD_M1, startPos, count, rates);
   }

   int BarsCount(string sym, datetime start, datetime end) override
   {
      return Bars(sym, PERIOD_M1, start, end);
   }
};

//+------------------------------------------------------------------+
//| MqlTimeApi — GetTickCount/TimeCurrent                            |
//+------------------------------------------------------------------+
class MqlTimeApi : public ITimeApi
{
public:
   long     TickCount() override { return (long)GetTickCount(); }
   datetime Now() override       { return TimeCurrent(); }
};

//+------------------------------------------------------------------+
//| MqlPositionApi — PositionsTotal/PositionGet*                     |
//+------------------------------------------------------------------+
class MqlPositionApi : public IPositionApi
{
public:
   int    Total() override                       { return PositionsTotal(); }
   ulong  TicketByIndex(int index) override      { return PositionGetTicket(index); }
   bool   SelectByTicket(ulong ticket) override  { return PositionSelectByTicket(ticket); }
   long   Magic() override                       { return PositionGetInteger(POSITION_MAGIC); }
   string Comment() override                     { return PositionGetString(POSITION_COMMENT); }
   long   Type() override                        { return PositionGetInteger(POSITION_TYPE); }
   double PriceOpen() override                   { return PositionGetDouble(POSITION_PRICE_OPEN); }
   double StopLoss() override                    { return PositionGetDouble(POSITION_SL); }
   double TakeProfit() override                  { return PositionGetDouble(POSITION_TP); }
   double Volume() override                      { return PositionGetDouble(POSITION_VOLUME); }
};

//+------------------------------------------------------------------+
//| MqlDealHistoryApi — History*/HistoryDealGet*/HistoryOrderGet*    |
//+------------------------------------------------------------------+
class MqlDealHistoryApi : public IDealHistoryApi
{
public:
   bool   Select(datetime from, datetime to) override { return HistorySelect(from, to); }
   int    Total() override                            { return HistoryDealsTotal(); }
   ulong  TicketByIndex(int index) override           { return HistoryDealGetTicket(index); }
   long   Magic(ulong ticket) override                { return HistoryDealGetInteger(ticket, DEAL_MAGIC); }
   long   Entry(ulong ticket) override                { return HistoryDealGetInteger(ticket, DEAL_ENTRY); }
   long   Reason(ulong ticket) override               { return HistoryDealGetInteger(ticket, DEAL_REASON); }
   double Price(ulong ticket) override                { return HistoryDealGetDouble(ticket, DEAL_PRICE); }
   string Comment(ulong ticket) override              { return HistoryDealGetString(ticket, DEAL_COMMENT); }
   long   OrderTicket(ulong ticket) override          { return HistoryDealGetInteger(ticket, DEAL_ORDER); }
   long   Time(ulong ticket) override                 { return HistoryDealGetInteger(ticket, DEAL_TIME); }
   double Volume(ulong ticket) override               { return HistoryDealGetDouble(ticket, DEAL_VOLUME); }
   double Profit(ulong ticket) override               { return HistoryDealGetDouble(ticket, DEAL_PROFIT); }
   double Commission(ulong ticket) override           { return HistoryDealGetDouble(ticket, DEAL_COMMISSION); }

   bool   SelectOrder(ulong orderTicket) override       { return HistoryOrderSelect(orderTicket); }
   double OrderStopLoss(ulong orderTicket) override     { return HistoryOrderGetDouble(orderTicket, ORDER_SL); }
   double OrderTakeProfit(ulong orderTicket) override   { return HistoryOrderGetDouble(orderTicket, ORDER_TP); }
};

//+------------------------------------------------------------------+
//| MqlPlatformApis — factory: news up one real API per interface    |
//+------------------------------------------------------------------+
class MqlPlatformApis
{
public:
   static PlatformApis *Create()
   {
      PlatformApis *apis = new PlatformApis();
      apis.account     = new MqlAccountApi();
      apis.symbol      = new MqlSymbolApi();
      apis.trade       = new MqlTradeApi();
      apis.marketData  = new MqlMarketDataApi();
      apis.time        = new MqlTimeApi();
      apis.position    = new MqlPositionApi();
      apis.dealHistory = new MqlDealHistoryApi();
      return apis;
   }

   static void Destroy(PlatformApis *apis)
   {
      if(apis == NULL) return;
      if(apis.account != NULL)     { delete apis.account;     apis.account = NULL; }
      if(apis.symbol != NULL)      { delete apis.symbol;      apis.symbol = NULL; }
      if(apis.trade != NULL)       { delete apis.trade;       apis.trade = NULL; }
      if(apis.marketData != NULL)  { delete apis.marketData;  apis.marketData = NULL; }
      if(apis.time != NULL)        { delete apis.time;        apis.time = NULL; }
      if(apis.position != NULL)    { delete apis.position;    apis.position = NULL; }
      if(apis.dealHistory != NULL) { delete apis.dealHistory; apis.dealHistory = NULL; }
      delete apis;
   }
};
