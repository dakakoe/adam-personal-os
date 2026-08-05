// Shared money/number formatting for the budget module.

const SYMBOLS: Record<string, string> = {
  USD: "$", THB: "฿", RUB: "₽", EUR: "€", GBP: "£", BTC: "₿",
};

/** Format an amount in a given asset/currency code. Crypto gets more decimals. */
export function fmtMoney(amount: number | null | undefined, code = "USD"): string {
  if (amount == null) return "—";
  const sym = SYMBOLS[code] ?? "";
  const crypto = !["USD", "THB", "RUB", "EUR", "GBP", "USDT", "USDC"].includes(code);
  const decimals = crypto ? (Math.abs(amount) < 1 ? 6 : 4) : 2;
  const n = amount.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return sym ? `${sym}${n}` : `${n} ${code}`;
}

/** Compact form for dashboards: $1.2M, ฿340k. */
export function fmtCompact(amount: number | null | undefined, code = "USD"): string {
  if (amount == null) return "—";
  const sym = SYMBOLS[code] ?? "";
  const abs = Math.abs(amount);
  const sign = amount < 0 ? "-" : "";
  let body: string;
  if (abs >= 1_000_000) body = `${(abs / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 2 })}M`;
  else if (abs >= 1_000) body = `${(abs / 1_000).toLocaleString(undefined, { maximumFractionDigits: 1 })}k`;
  else body = abs.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return sym ? `${sign}${sym}${body}` : `${sign}${body} ${code}`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" })
      .format(new Date(iso));
  } catch {
    return iso;
  }
}
