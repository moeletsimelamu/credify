import { addDays, lastMonths, startOfMonth } from "./dates";
import {
  businessToday,
  formatDate,
  formatMoney,
  formatPercent,
  formatProbability,
  humanize,
} from "./format";

/** Intl uses narrow/non-breaking spaces as group separators; compare on plain spaces. */
const plain = (text: string) => text.replace(/[\u00a0\u202f]/g, " ");

describe("formatMoney", () => {
  it("formats the server's decimal string exactly, without float rounding", () => {
    expect(plain(formatMoney("12500.00", "ZAR"))).toBe("R 12 500,00");
    // 0.1 + 0.2 style artefacts can't appear: the string is formatted as a decimal.
    expect(plain(formatMoney("9007199254740993.05", "ZAR"))).toBe("R 9 007 199 254 740 993,05");
  });

  it("uses the currency from the API", () => {
    expect(plain(formatMoney("10.50", "USD"))).toContain("10,50");
    expect(plain(formatMoney("10.50", "USD"))).toMatch(/US\$|\$/);
  });

  it("shows a dash for missing values", () => {
    expect(formatMoney(null, "ZAR")).toBe("—");
    expect(formatMoney(undefined, "ZAR")).toBe("—");
    expect(formatMoney("", "ZAR")).toBe("—");
  });
});

describe("percentages", () => {
  it("formats ratio strings", () => {
    expect(plain(formatPercent("0.3512"))).toBe("35,1%");
    expect(formatPercent(null)).toBe("—");
    expect(plain(formatProbability("0.172300"))).toBe("17,23%");
  });
});

describe("dates", () => {
  it("formats calendar dates without a time-zone shift and keeps them on one line", () => {
    expect(plain(formatDate("2026-01-01"))).toBe("01 Jan 2026");
    expect(formatDate("2026-01-01")).not.toContain(" ");
    expect(formatDate(null)).toBe("—");
  });

  it("gives the business date in South African time", () => {
    // 23:30 UTC on 31 Dec is already 1 Jan in Johannesburg (UTC+2).
    expect(businessToday(new Date("2025-12-31T23:30:00Z"))).toBe("2026-01-01");
  });

  it("does calendar arithmetic on ISO dates", () => {
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(startOfMonth("2026-11-26")).toBe("2026-11-01");
  });

  it("lists the last months, oldest first, ending today", () => {
    const months = lastMonths(3, "2026-01-15");
    expect(months.map((m) => [m.from, m.to])).toEqual([
      ["2025-11-01", "2025-11-30"],
      ["2025-12-01", "2025-12-31"],
      ["2026-01-01", "2026-01-15"],
    ]);
  });
});

describe("humanize", () => {
  it("turns enum values into sentence case", () => {
    expect(humanize("UNDER_REVIEW")).toBe("Under review");
    expect(humanize("31-60")).toBe("31 60");
    expect(humanize(null)).toBe("—");
  });
});
