/**
 * Utility functions for token formatting and currency conversion.
 */

// Token unit auto-adaptation
export function formatTokens(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

// Format token count with context-aware unit selection
export function formatTokensContext(n: number, context: 'input' | 'output' | 'total' | 'price' = 'total'): string {
  if (context === 'price') {
    // For pricing, show per-unit cost
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(2)}K`;
    return String(n);
  }
  return formatTokens(n);
}

// Format price with currency
export function formatPrice(amount: number, currency: string = 'CNY', showCurrency: boolean = true): string {
  const symbolMap: Record<string, string> = {
    CNY: '¥',
    USD: '$',
    EUR: '€',
    GBP: '£',
    JPY: '¥',
  };
  const symbol = symbolMap[currency] || currency;
  if (amount >= 1) {
    return `${symbol}${amount.toFixed(2)}`;
  }
  if (amount >= 0.01) {
    return `${symbol}${amount.toFixed(4)}`;
  }
  return `${symbol}${amount.toFixed(6)}`;
}

// Format price per token unit
export function formatPricePerUnit(price: number, currency: string = 'CNY', unit: string = '1M'): string {
  const formatted = formatPrice(price, currency, false);
  return `${formatted}/${unit}`;
}

// Convert token count between units
export function convertTokenUnit(value: number, fromUnit: string, toUnit: string): number {
  const unitMultiplier: Record<string, number> = {
    '1': 1,
    '1K': 1_000,
    '1M': 1_000_000,
    '1B': 1_000_000_000,
  };
  const fromMul = unitMultiplier[fromUnit] || 1;
  const toMul = unitMultiplier[toUnit] || 1;
  return (value * fromMul) / toMul;
}

// Convert price between token units
export function convertPricePerUnit(price: number, fromUnit: string, toUnit: string): number {
  // e.g., $0.002/1K -> $2.0/1M
  const fromMul = getTokenUnitMultiplier(fromUnit);
  const toMul = getTokenUnitMultiplier(toUnit);
  return (price / fromMul) * toMul;
}

function getTokenUnitMultiplier(unit: string): number {
  const map: Record<string, number> = {
    '1': 1,
    '1K': 1_000,
    '1M': 1_000_000,
    '1B': 1_000_000_000,
  };
  return map[unit] || 1;
}

// Currency conversion (using exchange rates from config)
export function convertCurrency(
  amount: number,
  fromCurrency: string,
  toCurrency: string,
  rates: Record<string, number> = {}
): number {
  if (fromCurrency === toCurrency) return amount;

  // rates are expected to be fromCurrency->toCurrency
  const key = `${fromCurrency}_${toCurrency}`;
  if (rates[key]) return amount * rates[key];

  // Try reverse
  const reverseKey = `${toCurrency}_${fromCurrency}`;
  if (rates[reverseKey]) return amount / rates[reverseKey];

  // No rate available, return as-is
  return amount;
}

// Get best display unit for a token count
export function getBestTokenUnit(count: number): string {
  if (count >= 1_000_000_000) return '1B';
  if (count >= 1_000_000) return '1M';
  if (count >= 1_000) return '1K';
  return '1';
}
