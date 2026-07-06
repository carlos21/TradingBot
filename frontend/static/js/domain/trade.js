/**
 * Pure trade-line and trade-state helpers.
 * No DOM or chart-library dependencies.
 */

export const LINE_STYLE = {
  SOLID: 'solid',
  DASHED: 'dashed',
  DOTTED: 'dotted',
};

export const COLORS = {
  ENTRY: 'yellow',
  SL: 'red',
  TP: 'green',
  ADJUSTED_SL: '#ff5252',
  OPP_CROSS_EXIT: '#4CAF50',
};

/**
 * Build the human-readable entry label for a trade.
 */
export function getTradeEntryLabel(trade, index) {
  const isOppCross = trade.close_on_opposite_cross;
  const prefix = isOppCross
    ? `Entry #${index} → OppCross`
    : index > 1
    ? `Re-entry #${index}`
    : `Entry #${index}`;
  return isOppCross ? prefix : prefix; // kept explicit for readability
}

/**
 * Build descriptors for the three main trade lines (entry, SL, TP).
 *
 * @param {object} trade
 * @param {object} options
 * @param {number} options.index 1-based trade index for labels
 * @param {number} [options.slPrice] override SL price (e.g. original SL)
 * @returns {Array<{price:number,color:string,lineWidth:number,lineStyle:string,title:string}>}
 */
export function buildTradeLineDescriptors(trade, { index, slPrice } = {}) {
  const isOppCross = trade.close_on_opposite_cross;
  const entryLabel = getTradeEntryLabel(trade, index);
  const sl = slPrice ?? trade.stop_loss ?? trade.stopLoss;

  const lines = [
    {
      price: trade.entry,
      color: COLORS.ENTRY,
      lineWidth: 2,
      lineStyle: LINE_STYLE.SOLID,
      title: entryLabel,
      key: 'entry',
    },
    {
      price: sl,
      color: COLORS.SL,
      lineWidth: 2,
      lineStyle: LINE_STYLE.SOLID,
      title: `SL #${index}`,
      key: 'sl',
    },
  ];

  if (isOppCross) {
    lines.push({
      price: trade.entry,
      color: COLORS.OPP_CROSS_EXIT,
      lineWidth: 1,
      lineStyle: LINE_STYLE.DASHED,
      title: 'Exit: next opp. cross',
      key: 'tp',
    });
  } else {
    lines.push({
      price: trade.take_profit ?? trade.takeProfit,
      color: COLORS.TP,
      lineWidth: 2,
      lineStyle: LINE_STYLE.SOLID,
      title: `TP #${index}`,
      key: 'tp',
    });
  }

  return lines;
}

/**
 * Build descriptors when showing a single trade snapshot, including an
 * optional adjusted-SL line when the SL moved.
 */
export function buildSingleTradeLineDescriptors(trade, index) {
  const origSL = trade.orig_sl ?? trade.stop_loss ?? trade.stopLoss;
  const currSL = trade.stop_loss ?? trade.stopLoss;
  const lines = buildTradeLineDescriptors(trade, { index, slPrice: origSL });

  if (currSL && origSL && Math.abs(currSL - origSL) > 0.01) {
    lines.push({
      price: currSL,
      color: COLORS.ADJUSTED_SL,
      lineWidth: 1,
      lineStyle: LINE_STYLE.DASHED,
      title: `Adj SL #${index}`,
      key: 'adjSl',
    });
  }

  return lines;
}

/**
 * Merge an update payload into an existing trade object (pure).
 */
export function mergeTradeUpdate(existing, update) {
  return {
    ...existing,
    ...update,
    status: update.status ?? existing.status,
  };
}

/**
 * Resolve a trade list to the same object by id, or push a new one.
 */
export function upsertTrade(trades, trade) {
  const idx = trades.findIndex(t => t.trade_id === trade.trade_id);
  if (idx !== -1) {
    const next = [...trades];
    next[idx] = { ...next[idx], ...trade };
    return next;
  }
  return [...trades, trade];
}
