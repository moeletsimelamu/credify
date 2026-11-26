/** Calendar arithmetic on ISO dates (YYYY-MM-DD) in UTC, so no time zone can shift a day. */

function parse(iso: string): Date {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year!, month! - 1, day!));
}

function iso(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function addDays(value: string, days: number): string {
  const date = parse(value);
  date.setUTCDate(date.getUTCDate() + days);
  return iso(date);
}

export function startOfMonth(value: string): string {
  return `${value.slice(0, 7)}-01`;
}

export function startOfYear(value: string): string {
  return `${value.slice(0, 4)}-01-01`;
}

/** The last `count` calendar months up to `today`, oldest first; the current month ends today. */
export function lastMonths(
  count: number,
  today: string,
): Array<{ label: string; from: string; to: string }> {
  const label = new Intl.DateTimeFormat("en-ZA", {
    month: "short",
    year: "2-digit",
    timeZone: "UTC",
  });
  const months = [];
  const anchor = parse(startOfMonth(today));
  for (let offset = count - 1; offset >= 0; offset -= 1) {
    const first = new Date(Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() - offset, 1));
    const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0));
    const to = iso(last) > today ? today : iso(last);
    months.push({ label: label.format(first), from: iso(first), to });
  }
  return months;
}
