/** Radix primitives give dialogs and tabs correct focus handling and keyboard support. */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import type { ReactNode } from "react";

import { cx } from "@/shared/lib/cx";

import { Button } from "./Button";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  wide = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-ink-950/45" />
        <DialogPrimitive.Content
          className={cx(
            "fixed top-1/2 left-1/2 z-50 max-h-[90vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2",
            "overflow-y-auto rounded-card bg-surface p-6 shadow-xl",
            wide ? "max-w-2xl" : "max-w-lg",
          )}
          {...(description ? {} : { "aria-describedby": undefined })}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <DialogPrimitive.Title className="text-lg font-semibold text-fg">
                {title}
              </DialogPrimitive.Title>
              {description && (
                <DialogPrimitive.Description className="mt-1 text-sm text-muted">
                  {description}
                </DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close
              className="rounded-md px-2 py-1 text-sm text-muted hover:bg-sunken"
              aria-label="Close"
            >
              ✕
            </DialogPrimitive.Close>
          </div>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** Ask before an action that can't be undone from the UI. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  children,
  confirmLabel,
  onConfirm,
  pending = false,
  danger = false,
  error,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  pending?: boolean;
  danger?: boolean;
  error?: ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={title}>
      <div className="text-sm text-fg">{children}</div>
      {error && <div className="mt-3">{error}</div>}
      <div className="mt-6 flex justify-end gap-2">
        <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={pending}>
          Cancel
        </Button>
        <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} loading={pending}>
          {confirmLabel}
        </Button>
      </div>
    </Dialog>
  );
}

export interface TabItem {
  value: string;
  label: ReactNode;
  content: ReactNode;
}

export function Tabs({
  items,
  value,
  onValueChange,
  label,
}: {
  items: TabItem[];
  value: string;
  onValueChange: (value: string) => void;
  label: string;
}) {
  return (
    <TabsPrimitive.Root value={value} onValueChange={onValueChange}>
      <TabsPrimitive.List
        aria-label={label}
        className="mb-5 flex gap-1 overflow-x-auto overflow-y-hidden border-b border-line"
      >
        {items.map((item) => (
          <TabsPrimitive.Trigger
            key={item.value}
            value={item.value}
            className={cx(
              "-mb-px border-b-2 border-transparent px-3 py-2.5 text-sm font-medium whitespace-nowrap text-muted",
              "hover:text-fg data-[state=active]:border-accent data-[state=active]:text-fg",
            )}
          >
            {item.label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {items.map((item) => (
        <TabsPrimitive.Content key={item.value} value={item.value} className="outline-none">
          {item.content}
        </TabsPrimitive.Content>
      ))}
    </TabsPrimitive.Root>
  );
}

export interface Step {
  key: string;
  label: string;
  state: "done" | "current" | "upcoming" | "stopped";
}

/** Horizontal workflow stepper; the current step is exposed with aria-current. */
export function Stepper({ steps, label }: { steps: Step[]; label: string }) {
  return (
    <ol aria-label={label} className="flex flex-wrap items-center gap-x-1 gap-y-2 text-xs">
      {steps.map((step, index) => (
        <li
          key={step.key}
          className="flex items-center gap-1"
          aria-current={step.state === "current" ? "step" : undefined}
        >
          <span
            className={cx(
              "flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium",
              step.state === "done" && "bg-accent-soft text-accent-strong",
              step.state === "current" && "bg-accent text-white",
              step.state === "upcoming" && "bg-sunken text-muted",
              step.state === "stopped" && "bg-critical-soft text-critical",
            )}
          >
            <span aria-hidden="true">{step.state === "done" ? "✓" : index + 1}</span>
            {step.label}
            <span className="sr-only">
              {step.state === "done"
                ? " (complete)"
                : step.state === "current"
                  ? " (current)"
                  : step.state === "stopped"
                    ? " (stopped)"
                    : ""}
            </span>
          </span>
          {index < steps.length - 1 && (
            <span aria-hidden="true" className="h-px w-4 bg-line-strong" />
          )}
        </li>
      ))}
    </ol>
  );
}
