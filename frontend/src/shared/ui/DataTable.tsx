import type { ReactNode } from "react";

import { cx } from "@/shared/lib/cx";

export interface Column<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  align?: "left" | "right" | "center";
  /** Figures use tabular numerals so columns align. */
  numeric?: boolean;
  className?: string;
}

interface DataTableProps<T> {
  caption: ReactNode;
  /** Hide the caption visually but keep it for screen readers. */
  captionHidden?: boolean;
  columns: ReadonlyArray<Column<T>>;
  rows: ReadonlyArray<T>;
  rowKey: (row: T) => string;
  rowClassName?: (row: T) => string | undefined;
  footer?: ReactNode;
  dense?: boolean;
}

/** A dense, legible table. Loading/empty/error states come from QueryView around it. */
export function DataTable<T>({
  caption,
  captionHidden = true,
  columns,
  rows,
  rowKey,
  rowClassName,
  footer,
  dense = false,
}: DataTableProps<T>) {
  const pad = dense ? "px-3 py-1.5" : "px-3 py-2.5";
  return (
    <div className="overflow-x-auto rounded-card border border-line bg-surface">
      <table className="w-full border-collapse text-sm">
        <caption
          className={cx(
            captionHidden ? "sr-only" : "px-3 py-2 text-left text-sm font-medium text-muted",
          )}
        >
          {caption}
        </caption>
        <thead>
          <tr className="border-b border-line bg-sunken/60">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cx(
                  pad,
                  "text-xs font-semibold tracking-wide whitespace-nowrap text-muted uppercase",
                  alignClass(column),
                )}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={cx("border-b border-line last:border-b-0", rowClassName?.(row))}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={cx(
                    pad,
                    alignClass(column),
                    column.numeric && "figure whitespace-nowrap",
                    column.className,
                  )}
                >
                  {column.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {footer && <tfoot className="border-t border-line-strong bg-sunken/40">{footer}</tfoot>}
      </table>
    </div>
  );
}

function alignClass<T>(column: Column<T>): string {
  const align = column.align ?? (column.numeric ? "right" : "left");
  return align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left";
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return null;
  return (
    <nav aria-label="Pagination" className="mt-3 flex items-center justify-between text-sm">
      <p className="text-muted">
        Page {page} of {pages} · {total} records
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          className="rounded-md border border-line-strong bg-surface px-3 py-1.5 disabled:opacity-50"
          onClick={() => onPage(page - 1)}
          disabled={page <= 1}
        >
          Previous
        </button>
        <button
          type="button"
          className="rounded-md border border-line-strong bg-surface px-3 py-1.5 disabled:opacity-50"
          onClick={() => onPage(page + 1)}
          disabled={page >= pages}
        >
          Next
        </button>
      </div>
    </nav>
  );
}
