import type { ReactNode } from "react";

import { errorMessage } from "@/api/client";
import { cx } from "@/shared/lib/cx";

import { Button } from "./Button";

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cx("animate-pulse rounded bg-sunken", className)} />;
}

/** A labelled loading placeholder: sighted users see skeleton rows, screen readers hear it. */
export function LoadingState({ label = "Loading", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-2.5 py-2">
      <span className="sr-only">{label}…</span>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className={cx("h-4", index % 2 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-card border border-dashed border-line-strong px-6 py-8 text-center">
      <p className="font-medium text-fg">{title}</p>
      {children && <div className="max-w-md text-sm text-muted">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({
  error,
  title = "This couldn't be loaded",
  onRetry,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-2 rounded-card border border-critical/30 bg-critical-soft px-4 py-4"
    >
      <p className="font-medium text-critical">{title}</p>
      <p className="text-sm text-fg">{errorMessage(error)}</p>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

type Tone = "info" | "positive" | "warning" | "critical" | "advisory";

const tones: Record<Tone, string> = {
  info: "border-info/25 bg-info-soft text-fg",
  positive: "border-positive/25 bg-positive-soft text-fg",
  warning: "border-warning/30 bg-warning-soft text-fg",
  critical: "border-critical/30 bg-critical-soft text-fg",
  advisory: "border-dashed border-advisory/40 bg-advisory-soft text-fg",
};

export function Notice({
  tone = "info",
  title,
  children,
  role,
  className,
}: {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  role?: "alert" | "status";
  className?: string;
}) {
  return (
    <div
      role={role}
      className={cx("rounded-card border px-4 py-3 text-sm", tones[tone], className)}
    >
      {title && <p className="font-medium">{title}</p>}
      {children && <div className={cx(title ? "mt-1" : undefined, "text-fg/90")}>{children}</div>}
    </div>
  );
}

/** Inline outcome of a mutation, announced politely. */
export function FormMessage({ error, success }: { error?: unknown; success?: ReactNode }) {
  if (error) {
    return (
      <Notice tone="critical" role="alert">
        {errorMessage(error)}
      </Notice>
    );
  }
  if (success) {
    return (
      <Notice tone="positive" role="status">
        {success}
      </Notice>
    );
  }
  return null;
}
