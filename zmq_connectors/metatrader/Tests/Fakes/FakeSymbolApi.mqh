//+------------------------------------------------------------------+
//|                                 Tests/Fakes/FakeSymbolApi.mqh    |
//|  Fake ISymbolApi — programmable per-symbol quotes/properties.    |
//|  Unknown symbols fall back to sane FX defaults.                  |
//+------------------------------------------------------------------+
#property strict

#include "../../Domain/PlatformApi.mqh"

//+------------------------------------------------------------------+
//| FakeSymbolApi — scripted symbol state                            |
//+------------------------------------------------------------------+
class FakeSymbolApi : public ISymbolApi
{
private:
   string m_symbols[];
   double m_points[];
   int    m_digits[];
   double m_bids[];
   double m_asks[];
   bool   m_selectResults[];
   double m_volumeMins[];
   double m_volumeMaxs[];
   double m_volumeSteps[];
   double m_tickValues[];
   double m_tickSizes[];

   //--- Scripted tick state (MarketStreamer tests) -------------------
   double m_tickLasts[];
   long   m_tickVolumes[];
   long   m_tickTimes[];
   long   m_tickMscs[];
   bool   m_tickResults[];

   //--- Select() call accounting (watchdog recovery tests) -----------
   int    m_selectCounts[];

   int _Find(string sym)
   {
      for(int i = 0; i < ArraySize(m_symbols); i++)
         if(m_symbols[i] == sym)
            return i;
      return -1;
   }

   int _Ensure(string sym)
   {
      int idx = _Find(sym);
      if(idx >= 0)
         return idx;
      idx = ArraySize(m_symbols);
      int n = idx + 1;
      ArrayResize(m_symbols, n);       m_symbols[idx] = sym;
      ArrayResize(m_points, n);        m_points[idx] = 0.00001;
      ArrayResize(m_digits, n);        m_digits[idx] = 5;
      ArrayResize(m_bids, n);          m_bids[idx] = 1.10000;
      ArrayResize(m_asks, n);          m_asks[idx] = 1.10010;
      ArrayResize(m_selectResults, n); m_selectResults[idx] = true;
      ArrayResize(m_volumeMins, n);    m_volumeMins[idx] = 0.01;
      ArrayResize(m_volumeMaxs, n);    m_volumeMaxs[idx] = 100.0;
      ArrayResize(m_volumeSteps, n);   m_volumeSteps[idx] = 0.01;
      ArrayResize(m_tickValues, n);    m_tickValues[idx] = 1.0;
      ArrayResize(m_tickSizes, n);     m_tickSizes[idx] = 0.00001;
      ArrayResize(m_tickLasts, n);     m_tickLasts[idx] = 0.0;
      ArrayResize(m_tickVolumes, n);   m_tickVolumes[idx] = 0;
      ArrayResize(m_tickTimes, n);     m_tickTimes[idx] = 0;
      ArrayResize(m_tickMscs, n);      m_tickMscs[idx] = 0;
      ArrayResize(m_tickResults, n);   m_tickResults[idx] = true;
      ArrayResize(m_selectCounts, n);  m_selectCounts[idx] = 0;
      return idx;
   }

public:
   //--- Test hooks -------------------------------------------------
   void SetQuote(string sym, double bid, double ask)
   {
      int i = _Ensure(sym);
      m_bids[i] = bid;
      m_asks[i] = ask;
   }
   void SetPoint(string sym, double point)     { m_points[_Ensure(sym)] = point; }
   void SetDigits(string sym, int digits)      { m_digits[_Ensure(sym)] = digits; }
   void SetSelectResult(string sym, bool r)    { m_selectResults[_Ensure(sym)] = r; }
   void SetVolumeLimits(string sym, double min, double max, double step)
   {
      int i = _Ensure(sym);
      m_volumeMins[i] = min;
      m_volumeMaxs[i] = max;
      m_volumeSteps[i] = step;
   }
   void SetTickValue(string sym, double value) { m_tickValues[_Ensure(sym)] = value; }
   void SetTickSize(string sym, double size)   { m_tickSizes[_Ensure(sym)] = size; }

   //--- Scripted full tick (bid/ask/last/volume/time); Tick() replays it
   void SetTick(string sym, double bid, double ask, double last,
                long volume, datetime time, long timeMsc)
   {
      int i = _Ensure(sym);
      m_bids[i] = bid;
      m_asks[i] = ask;
      m_tickLasts[i] = last;
      m_tickVolumes[i] = volume;
      m_tickTimes[i] = (long)time;
      m_tickMscs[i] = timeMsc;
   }
   void SetTickResult(string sym, bool r) { m_tickResults[_Ensure(sym)] = r; }

   //--- Select() call accounting
   int SelectCount(string sym)
   {
      int i = _Find(sym);
      return i >= 0 ? m_selectCounts[i] : 0;
   }
   int TotalSelectCount()
   {
      int total = 0;
      for(int i = 0; i < ArraySize(m_selectCounts); i++)
         total += m_selectCounts[i];
      return total;
   }

   //--- ISymbolApi -------------------------------------------------
   bool   Select(string sym) override
   {
      int i = _Ensure(sym);
      m_selectCounts[i]++;
      return m_selectResults[i];
   }
   double Point(string sym) override  { return m_points[_Ensure(sym)]; }
   int    Digits(string sym) override { return m_digits[_Ensure(sym)]; }
   double Bid(string sym) override    { return m_bids[_Ensure(sym)]; }
   double Ask(string sym) override    { return m_asks[_Ensure(sym)]; }

   bool Tick(string sym, MqlTick &tick) override
   {
      int i = _Ensure(sym);
      if(!m_tickResults[i])
         return false;
      tick.bid = m_bids[i];
      tick.ask = m_asks[i];
      tick.last = m_tickLasts[i];
      tick.volume = m_tickVolumes[i];
      tick.time = (datetime)m_tickTimes[i];
      tick.time_msc = m_tickMscs[i];
      return true;
   }

   double VolumeMin(string sym) override  { return m_volumeMins[_Ensure(sym)]; }
   double VolumeMax(string sym) override  { return m_volumeMaxs[_Ensure(sym)]; }
   double VolumeStep(string sym) override { return m_volumeSteps[_Ensure(sym)]; }
   double TickValue(string sym) override  { return m_tickValues[_Ensure(sym)]; }
   double TickSize(string sym) override   { return m_tickSizes[_Ensure(sym)]; }
};
