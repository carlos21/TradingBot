import { describe, it, expect } from 'vitest';
import {
  LINE_STYLE,
  COLORS,
  getTradeEntryLabel,
  buildTradeLineDescriptors,
  buildSingleTradeLineDescriptors,
  mergeTradeUpdate,
  upsertTrade,
} from '../../domain/trade.js';

describe('trade domain', () => {
  const baseTrade = {
    trade_id: 't1',
    entry: 100,
    stop_loss: 99,
    take_profit: 102,
    type: 'long',
    status: 'open',
  };

  it('builds entry labels', () => {
    expect(getTradeEntryLabel(baseTrade, 1)).toBe('Entry #1');
    expect(getTradeEntryLabel(baseTrade, 2)).toBe('Re-entry #2');
    expect(getTradeEntryLabel({ ...baseTrade, close_on_opposite_cross: true }, 3)).toBe(
      'Entry #3 → OppCross'
    );
  });

  it('builds standard trade lines', () => {
    const lines = buildTradeLineDescriptors(baseTrade, { index: 1 });
    expect(lines).toHaveLength(3);
    expect(lines[0]).toEqual({
      price: 100,
      color: COLORS.ENTRY,
      lineWidth: 2,
      lineStyle: LINE_STYLE.SOLID,
      title: 'Entry #1',
      key: 'entry',
    });
    expect(lines[1].title).toBe('SL #1');
    expect(lines[2]).toEqual({
      price: 102,
      color: COLORS.TP,
      lineWidth: 2,
      lineStyle: LINE_STYLE.SOLID,
      title: 'TP #1',
      key: 'tp',
    });
  });

  it('builds opp-cross trade lines', () => {
    const lines = buildTradeLineDescriptors(
      { ...baseTrade, close_on_opposite_cross: true },
      { index: 1 }
    );
    expect(lines[2]).toEqual({
      price: 100,
      color: COLORS.OPP_CROSS_EXIT,
      lineWidth: 1,
      lineStyle: LINE_STYLE.DASHED,
      title: 'Exit: next opp. cross',
      key: 'tp',
    });
  });

  it('uses stopLoss alias when stop_loss is missing', () => {
    const trade = { ...baseTrade, stop_loss: undefined, stopLoss: 98, take_profit: undefined, takeProfit: 103 };
    const lines = buildTradeLineDescriptors(trade, { index: 1 });
    expect(lines[1].price).toBe(98);
    expect(lines[2].price).toBe(103);
  });

  it('adds adjusted SL line for snapshot when SL moved', () => {
    const trade = { ...baseTrade, orig_sl: 98 };
    const lines = buildSingleTradeLineDescriptors(trade, 1);
    expect(lines).toHaveLength(4);
    expect(lines[3].title).toBe('Adj SL #1');
    expect(lines[3].lineStyle).toBe(LINE_STYLE.DASHED);
  });

  it('does not add adjusted SL line when SL did not move', () => {
    const lines = buildSingleTradeLineDescriptors(baseTrade, 1);
    expect(lines).toHaveLength(3);
  });

  it('merges trade updates', () => {
    const merged = mergeTradeUpdate(baseTrade, { stop_loss: 98.5, status: 'closed' });
    expect(merged.entry).toBe(100);
    expect(merged.stop_loss).toBe(98.5);
    expect(merged.status).toBe('closed');
  });

  it('upserts trades by id', () => {
    const trades = [baseTrade];
    const updated = upsertTrade(trades, { trade_id: 't1', stop_loss: 98 });
    expect(updated).toHaveLength(1);
    expect(updated[0].stop_loss).toBe(98);

    const added = upsertTrade(updated, { trade_id: 't2', entry: 200 });
    expect(added).toHaveLength(2);
  });
});
