import type { ReactNode } from "react";

import { cx } from "@/shared/lib/cx";
import { formatMoney, humanize } from "@/shared/lib/format";

/** An amount exactly as the server sent it, formatted for display. */
export function MoneyText({
  amount,
  currency,
  className,
}: {
  amount: string | null | undefined;
  currency: string;
  className?: string;
}) {
  return <span className={cx("figure", className)}>{formatMoney(amount, currency)}</span>;
}

type BadgeTone = "neutral" | "info" | "positive" | "warning" | "critical" | "accent";

const badgeTones: Record<BadgeTone, string> = {
  neutral: "bg-sunken text-muted ring-line-strong",
  info: "bg-info-soft text-info ring-info/25",
  positive: "bg-positive-soft text-positive ring-positive/25",
  warning: "bg-warning-soft text-warning ring-warning/30",
  critical: "bg-critical-soft text-critical ring-critical/25",
  accent: "bg-accent-soft text-accent-strong ring-accent/25",
};

const STATUS_TONES: Record<string, BadgeTone> = {
  // Applications
  DRAFT: "neutral",
  SUBMITTED: "info",
  PENDING_REVIEW: "info",
  UNDER_REVIEW: "accent",
  ADDITIONAL_INFO_REQUESTED: "warning",
  RECOMMENDED_APPROVAL: "accent",
  RECOMMENDED_REJECTION: "warning",
  APPROVED: "positive",
  DECLINED: "critical",
  WITHDRAWN: "neutral",
  // Loans and instalments
  PENDING_DISBURSEMENT: "info",
  ACTIVE: "positive",
  IN_ARREARS: "critical",
  SETTLED: "neutral",
  CLOSED: "neutral",
  SCHEDULED: "neutral",
  DUE: "info",
  PARTIALLY_PAID: "warning",
  PAID: "positive",
  OVERDUE: "critical",
  CANCELLED: "neutral",
  // Affordability outcomes
  AFFORDABLE: "positive",
  MARGINAL: "warning",
  UNAFFORDABLE: "critical",
  INSUFFICIENT_DATA: "neutral",
  // Risk assessments, models, documents, requests
  SCORED: "info",
  FAILED: "critical",
  CANDIDATE: "info",
  RETIRED: "neutral",
  RECEIVED: "info",
  ACCEPTED: "positive",
  REJECTED: "critical",
  OPEN: "warning",
  RESPONDED: "positive",
  DECLARED: "neutral",
  VERIFIED: "positive",
};

export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset",
        badgeTones[tone],
      )}
    >
      {children}
    </span>
  );
}

/** Status shown as text plus colour — colour is never the only signal. */
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return <Badge tone={STATUS_TONES[status] ?? "neutral"}>{label ?? humanize(status)}</Badge>;
}

const BAND_TONES: Record<string, BadgeTone> = {
  A: "positive",
  B: "positive",
  C: "info",
  D: "warning",
  E: "critical",
};

export function RiskBandBadge({ band }: { band: string | null | undefined }) {
  if (!band) return <Badge>No band</Badge>;
  return <Badge tone={BAND_TONES[band] ?? "neutral"}>Band {band}</Badge>;
}

export function Card({
  title,
  description,
  actions,
  children,
  className,
  as: Tag = "section",
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "aside";
}) {
  return (
    <Tag className={cx("rounded-card border border-line bg-surface shadow-card", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-3.5">
          <div>
            {title && <h2 className="text-base font-semibold text-fg">{title}</h2>}
            {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </Tag>
  );
}

export function PageHeader({
  title,
  eyebrow,
  description,
  actions,
}: {
  title: ReactNode;
  eyebrow?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow && <p className="mb-1 text-sm font-medium text-muted">{eyebrow}</p>}
        {/* Focused after navigation so screen-reader users land on the new page. */}
        <h1 tabIndex={-1} data-page-heading className="text-2xl font-semibold text-fg outline-none">
          {title}
        </h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export interface KeyValue {
  label: ReactNode;
  value: ReactNode;
  numeric?: boolean;
}

export function KeyValueList({ items, columns = 1 }: { items: KeyValue[]; columns?: 1 | 2 | 3 }) {
  return (
    <dl
      className={cx(
        "grid gap-x-6 gap-y-3",
        columns === 2 && "sm:grid-cols-2",
        columns === 3 && "sm:grid-cols-2 lg:grid-cols-3",
      )}
    >
      {items.map((item, index) => (
        <div key={index} className="flex flex-col gap-0.5">
          <dt className="text-xs font-medium tracking-wide text-muted uppercase">{item.label}</dt>
          <dd className={cx("text-sm text-fg", item.numeric && "figure")}>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Stat({
  label,
  value,
  detail,
  tone,
}: {
  label: ReactNode;
  value: ReactNode;
  detail?: ReactNode;
  tone?: "critical" | "warning" | "positive";
}) {
  return (
    <div className="rounded-card border border-line bg-surface px-4 py-3.5 shadow-card">
      <p className="text-xs font-medium tracking-wide text-muted uppercase">{label}</p>
      <p
        className={cx(
          "figure mt-1 text-xl font-semibold",
          tone === "critical" && "text-critical",
          tone === "warning" && "text-warning",
          tone === "positive" && "text-positive",
        )}
      >
        {value}
      </p>
      {detail && <div className="mt-1 text-xs text-muted">{detail}</div>}
    </div>
  );
}
