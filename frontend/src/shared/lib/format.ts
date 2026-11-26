/**
 * Display formatting only. Amounts arrive from the API as decimal strings and are never
 * re-calculated here (ARCHITECTURE.md §8): Intl formats the string exactly, without first
 * converting it to a binary float.
 */

export const LOCALE = "en-ZA";
const BUSINESS_TIME_ZONE = "Africa/Johannesburg";

type Numeric = Intl.StringNumericLiteral;

const moneyFormatters = new Map<string, Intl.NumberFormat>();

function moneyFormatter(currency: string): Intl.NumberFormat {
  let formatter = moneyFormatters.get(currency);
  if (!formatter) {
    formatter = new Intl.NumberFormat(LOCALE, {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    moneyFormatters.set(currency, formatter);
  }
  return formatter;
}

export function formatMoney(amount: string | null | undefined, currency: string): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  return moneyFormatter(currency).format(amount as Numeric);
}

const percentFormatter = new Intl.NumberFormat(LOCALE, {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** A ratio string such as "0.3512" shown as a percentage ("35,1%"). */
export function formatPercent(ratio: string | number | null | undefined): string {
  if (ratio === null || ratio === undefined || ratio === "") return "—";
  return percentFormatter.format(typeof ratio === "number" ? ratio : (ratio as Numeric));
}

const integerFormatter = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });

export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : integerFormatter.format(value);
}

/** Probability of default to two significant decimals of a percent, e.g. "3.33%". */
export function formatProbability(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat(LOCALE, {
    style: "percent",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value as Numeric);
}

const dateFormatter = new Intl.DateTimeFormat(LOCALE, {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/** A calendar date ("2026-11-25") — no time-zone shift. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return value;
  // Non-breaking spaces keep "26 Nov 2026" on one line in narrow table columns.
  return dateFormatter.format(new Date(Date.UTC(year, month - 1, day))).replace(/ /g, "\u00a0");
}

const dateTimeFormatter = new Intl.DateTimeFormat(LOCALE, {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: BUSINESS_TIME_ZONE,
});

/** A timestamp shown in South African time (the platform's business time zone). */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return dateTimeFormatter.format(new Date(value));
}

/** "UNDER_REVIEW" → "Under review". */
export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  const words = value.replace(/[_-]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Today's business date (SAST) as YYYY-MM-DD. */
export function businessToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: BUSINESS_TIME_ZONE }).format(now);
}

const compactFormatter = new Intl.NumberFormat(LOCALE, {
  notation: "compact",
  maximumFractionDigits: 1,
});

/** Chart axis ticks such as "45K" (axis labels only; exact values are shown alongside). */
export function compactTick(value: number): string {
  return compactFormatter.format(value);
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
