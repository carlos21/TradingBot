//+------------------------------------------------------------------+
//|                                     Tests/Fakes/Fakes.mqh        |
//|  Umbrella include for all test fakes + PlatformApis wiring.      |
//+------------------------------------------------------------------+
#property strict

#include "FakeZmqNetwork.mqh"
#include "FakeLogger.mqh"
#include "FakeAccountApi.mqh"
#include "FakeSymbolApi.mqh"
#include "FakeTradeApi.mqh"
#include "FakeMarketDataApi.mqh"
#include "FakeTimeApi.mqh"
#include "FakePositionApi.mqh"
#include "FakeDealHistoryApi.mqh"

//+------------------------------------------------------------------+
//| MakeFakePlatformApis — build a PlatformApis holder wired to      |
//|  fakes. Caller owns the returned holder (delete after use);      |
//|  the fakes themselves are owned by the caller.                   |
//+------------------------------------------------------------------+
PlatformApis *MakeFakePlatformApis(FakeAccountApi *account,
                                   FakeSymbolApi *symbol,
                                   FakeTradeApi *trade,
                                   FakeMarketDataApi *marketData,
                                   FakeTimeApi *time,
                                   FakePositionApi *position,
                                   FakeDealHistoryApi *dealHistory)
{
   PlatformApis *apis = new PlatformApis();
   apis.account = account;
   apis.symbol = symbol;
   apis.trade = trade;
   apis.marketData = marketData;
   apis.time = time;
   apis.position = position;
   apis.dealHistory = dealHistory;
   return apis;
}
