/**
 * Pure calendar helpers for the trade-history calendar view.
 * No DOM or I/O dependencies.
 */

export const RESULT = {
  WIN: 'win',
  LOSS: 'loss',
  BREAKEVEN: 'be',
  SCALE_PARTIAL: 'sp',
  UNKNOWN: 'unknown',
};

const BREAKEVEN_THRESHOLD = 0.5;

/**
 * Classify a trade result into a calendar badge category.
 */
export function getTradeResultCategory(trade) {
  if (trade.result_type === 'SP') return RESULT.SCALE_PARTIAL;
  const r = trade.result;
  if (r === null || r === undefined) return RESULT.UNKNOWN;
  if (Math.abs(r) < BREAKEVEN_THRESHOLD) return RESULT.BREAKEVEN;
  return r > 0 ? RESULT.WIN : RESULT.LOSS;
}

/**
 * Compute day-of-week where Monday = 0 ... Sunday = 6.
 */
export function getMondayBasedDay(date) {
  return (date.getDay() + 6) % 7;
}

/**
 * Group trades by month. Returns an array of month objects sorted
 * chronologically.
 */
export function groupTradesByMonth(trades) {
  const monthMap = new Map();

  for (const trade of trades) {
    if (!trade.entry_time) continue;
    const date = new Date(trade.entry_time * 1000);
    const monthKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;

    if (!monthMap.has(monthKey)) {
      monthMap.set(monthKey, {
        key: monthKey,
        label: date.toLocaleString('en-US', { month: 'long', year: 'numeric' }),
        year: date.getFullYear(),
        month: date.getMonth(),
        trades: [],
        stats: { wins: 0, losses: 0, be: 0, sp: 0, totalPnl: 0 },
      });
    }

    const month = monthMap.get(monthKey);
    month.trades.push(trade);

    const category = getTradeResultCategory(trade);
    if (category === RESULT.SCALE_PARTIAL) month.stats.sp++;
    else if (category === RESULT.BREAKEVEN) month.stats.be++;
    else if (category === RESULT.WIN) month.stats.wins++;
    else if (category === RESULT.LOSS) month.stats.losses++;

    if (trade.pnl_usd !== null && trade.pnl_usd !== undefined) {
      month.stats.totalPnl += trade.pnl_usd;
    }
  }

  return Array.from(monthMap.values()).sort((a, b) => {
    if (a.year !== b.year) return a.year - b.year;
    return a.month - b.month;
  });
}

/**
 * Group trades within a single month by day of month.
 */
export function groupTradesByDay(month) {
  const dayMap = new Map();
  for (const trade of month.trades) {
    const date = new Date(trade.entry_time * 1000);
    const day = date.getDate();
    if (!dayMap.has(day)) dayMap.set(day, []);
    dayMap.get(day).push(trade);
  }
  return dayMap;
}

/**
 * Compute the total P&L for an array of trades.
 */
export function sumPnl(trades) {
  return trades.reduce((sum, t) => sum + (t.pnl_usd || 0), 0);
}

/**
 * Build a calendar-week structure for a month, including only Mon-Fri.
 * Each week is an array of day descriptors. Empty cells are represented
 * by null.
 */
export function buildWeeks(month) {
  const dayMap = groupTradesByDay(month);
  const firstDay = new Date(month.year, month.month, 1);
  const lastDay = new Date(month.year, month.month + 1, 0);
  const startOffset = getMondayBasedDay(firstDay);
  const daysInMonth = lastDay.getDate();

  const weeks = [];
  let currentWeek = Array(startOffset).fill(null);

  for (let day = 1; day <= daysInMonth; day++) {
    const date = new Date(month.year, month.month, day);
    const dayOfWeek = getMondayBasedDay(date);
    if (dayOfWeek === 5 || dayOfWeek === 6) continue; // skip Sat/Sun

    currentWeek.push({
      day,
      trades: dayMap.get(day) || [],
    });

    if (dayOfWeek === 4 || day === daysInMonth) {
      while (currentWeek.length < 5) {
        currentWeek.push(null);
      }
      weeks.push(currentWeek);
      currentWeek = [];
    }
  }

  return weeks;
}
