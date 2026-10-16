import { htmlLang, type Locale } from "./config";

export function formatNumber(locale: Locale, value: number | null | undefined, maxDigits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return new Intl.NumberFormat(htmlLang[locale], { maximumFractionDigits: maxDigits }).format(value);
}

export function formatPercent(locale: Locale, ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return "–";
  return new Intl.NumberFormat(htmlLang[locale], { style: "percent", maximumFractionDigits: 0 }).format(ratio);
}

/** Format an ISO date (YYYY-MM-DD) without timezone drift. */
export function formatDate(locale: Locale, isoDate: string, style: "medium" | "short" = "medium"): string {
  const [y, m, d] = isoDate.slice(0, 10).split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  const opts: Intl.DateTimeFormatOptions =
    style === "short" ? { month: "short", day: "numeric", timeZone: "UTC" } : { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" };
  return new Intl.DateTimeFormat(htmlLang[locale], opts).format(date);
}

export function formatList(locale: Locale, items: string[]): string {
  // Explicit separators: Intl.ListFormat "unit" style has no separator in zh-CN ("1313").
  const sep: Record<string, string> = { "zh-cn": "、", en: ", ", fr: ", " };
  return items.join(sep[locale] ?? ", ");
}
