/**
 * Accessible form controls: every input has a visible label, hints and errors are linked with
 * aria-describedby, invalid inputs carry aria-invalid, and the error text is announced.
 */
import {
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type Ref,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

import { cx } from "@/shared/lib/cx";

const control =
  "block w-full rounded-md border bg-surface px-3 text-sm text-fg placeholder:text-subtle " +
  "disabled:bg-sunken disabled:text-muted aria-[invalid=true]:border-critical";

interface FieldShellProps {
  id: string;
  label: ReactNode;
  hint?: ReactNode;
  error?: string;
  required?: boolean;
  className?: string;
  children: ReactNode;
}

function FieldShell({ id, label, hint, error, required, className, children }: FieldShellProps) {
  return (
    <div className={cx("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-sm font-medium text-fg">
        {label}
        {required && (
          <span className="text-critical" aria-hidden="true">
            {" "}
            *
          </span>
        )}
      </label>
      {children}
      {hint && !error && (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="text-xs font-medium text-critical" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function describedBy(id: string, hint: ReactNode, error?: string): string | undefined {
  if (error) return `${id}-error`;
  return hint ? `${id}-hint` : undefined;
}

interface BaseProps {
  label: ReactNode;
  hint?: ReactNode;
  error?: string;
  containerClassName?: string;
}

type TextFieldProps = BaseProps &
  InputHTMLAttributes<HTMLInputElement> & { ref?: Ref<HTMLInputElement>; prefix?: string };

export function TextField({
  label,
  hint,
  error,
  containerClassName,
  className,
  required,
  id: givenId,
  prefix,
  ...props
}: TextFieldProps) {
  const generated = useId();
  const id = givenId ?? generated;
  const input = (
    <input
      id={id}
      className={cx(
        control,
        "h-10",
        prefix && "pl-9",
        props.inputMode === "decimal" && "figure",
        className,
      )}
      aria-invalid={error ? true : undefined}
      aria-describedby={describedBy(id, hint, error)}
      aria-required={required || undefined}
      {...props}
    />
  );
  return (
    <FieldShell
      id={id}
      label={label}
      hint={hint}
      error={error}
      required={required}
      className={containerClassName}
    >
      {prefix ? (
        <div className="relative">
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-sm text-muted"
          >
            {prefix}
          </span>
          {input}
        </div>
      ) : (
        input
      )}
    </FieldShell>
  );
}

type SelectFieldProps = BaseProps &
  SelectHTMLAttributes<HTMLSelectElement> & {
    ref?: Ref<HTMLSelectElement>;
    options: ReadonlyArray<{ value: string; label: string }>;
    placeholder?: string;
  };

export function SelectField({
  label,
  hint,
  error,
  containerClassName,
  className,
  required,
  id: givenId,
  options,
  placeholder,
  ...props
}: SelectFieldProps) {
  const generated = useId();
  const id = givenId ?? generated;
  return (
    <FieldShell
      id={id}
      label={label}
      hint={hint}
      error={error}
      required={required}
      className={containerClassName}
    >
      <select
        id={id}
        className={cx(control, "h-10", className)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, hint, error)}
        aria-required={required || undefined}
        {...props}
      >
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </FieldShell>
  );
}

type TextAreaFieldProps = BaseProps &
  TextareaHTMLAttributes<HTMLTextAreaElement> & { ref?: Ref<HTMLTextAreaElement> };

export function TextAreaField({
  label,
  hint,
  error,
  containerClassName,
  className,
  required,
  id: givenId,
  rows = 4,
  ...props
}: TextAreaFieldProps) {
  const generated = useId();
  const id = givenId ?? generated;
  return (
    <FieldShell
      id={id}
      label={label}
      hint={hint}
      error={error}
      required={required}
      className={containerClassName}
    >
      <textarea
        id={id}
        rows={rows}
        className={cx(control, "py-2", className)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, hint, error)}
        aria-required={required || undefined}
        {...props}
      />
    </FieldShell>
  );
}

type CheckboxFieldProps = Omit<BaseProps, "label"> &
  Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
    ref?: Ref<HTMLInputElement>;
    label: ReactNode;
  };

export function CheckboxField({
  label,
  hint,
  error,
  containerClassName,
  id: givenId,
  ...props
}: CheckboxFieldProps) {
  const generated = useId();
  const id = givenId ?? generated;
  return (
    <div className={cx("flex flex-col gap-1", containerClassName)}>
      <div className="flex items-start gap-2.5">
        <input
          id={id}
          type="checkbox"
          className="mt-0.5 size-4 accent-[var(--color-accent)]"
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(id, hint, error)}
          {...props}
        />
        <label htmlFor={id} className="text-sm text-fg">
          {label}
        </label>
      </div>
      {hint && !error && (
        <p id={`${id}-hint`} className="pl-6.5 text-xs text-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="pl-6.5 text-xs font-medium text-critical" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/** A group of checkboxes with one shared legend (e.g. reason codes). */
export function Fieldset({
  legend,
  hint,
  error,
  children,
}: {
  legend: ReactNode;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <fieldset
      className="flex flex-col gap-2"
      aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
      aria-invalid={error ? true : undefined}
    >
      <legend className="mb-1 text-sm font-medium text-fg">{legend}</legend>
      {hint && !error && (
        <p id={`${id}-hint`} className="-mt-1 text-xs text-muted">
          {hint}
        </p>
      )}
      {children}
      {error && (
        <p id={`${id}-error`} className="text-xs font-medium text-critical" role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}
