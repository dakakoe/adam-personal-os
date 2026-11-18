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

/**
 * Group digits as they're typed into a money field: "1500000" -> "1,500,000".
 * Grouping is done by hand rather than via Number(): award_usd is NUMERIC, and
 * round-tripping through a float would quietly mangle long values. A trailing
 * "." is preserved so the separator can still be typed mid-entry.
 */
export function fmtMoneyInput(raw: string): string {
  const cleaned = raw.replace(/[^\d.]/g, "");
  if (cleaned === "") return "";
  const [head, ...rest] = cleaned.split(".");
  const int = head.replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  if (!cleaned.includes(".")) return int;
  return `${int || "0"}.${rest.join("").slice(0, 2)}`;
}

/**
 * Back to a number for the API. Empty (or a lone ".") reads as no value.
 * Parsing goes through the same formatter the field displays, so what gets
 * saved is exactly what the user is looking at — including its 2-decimal cap.
 */
export function parseMoneyInput(raw: string): number | null {
  // No digit at all means no amount — a lone "." must clear the field, not
  // save the 0 that formatting would otherwise imply.
  if (!/\d/.test(raw)) return null;
  const cleaned = fmtMoneyInput(raw).replace(/,/g, "");
  if (cleaned === "" || cleaned === ".") return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}
