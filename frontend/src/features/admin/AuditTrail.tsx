import { useState } from "react";

import type { AuditEvent } from "@/api/types";
import { formatDateTime, humanize } from "@/shared/lib/format";
import { Card, DataTable, EmptyState, Pagination, QueryView } from "@/shared/ui";

import { useAuditEvents, type AuditFilters } from "./api";

function Changes({ event }: { event: AuditEvent }) {
  const changes = event.changes ?? event.context;
  if (!changes || Object.keys(changes).length === 0) return <span className="text-muted">—</span>;
  return (
    <details>
      <summary className="cursor-pointer text-xs text-accent">
        {Object.keys(changes).length} field{Object.keys(changes).length === 1 ? "" : "s"}
      </summary>
      <pre className="figure mt-1 max-w-md overflow-x-auto rounded bg-sunken p-2 text-xs whitespace-pre-wrap">
        {JSON.stringify(changes, null, 2)}
      </pre>
    </details>
  );
}

/** Append-only audit events, newest first (administrators). */
export function AuditTable({ filters }: { filters: AuditFilters }) {
  const [page, setPage] = useState(1);
  const events = useAuditEvents({ ...filters, page, page_size: 25 });
  return (
    <QueryView
      query={events}
      loadingLabel="Loading audit events"
      isEmpty={(data) => data.items.length === 0}
      empty={<EmptyState title="No audit events match" />}
    >
      {(data) => (
        <>
          <DataTable<AuditEvent>
            caption="Audit events"
            dense
            rows={data.items}
            rowKey={(row) => row.id}
            columns={[
              {
                key: "at",
                header: "When",
                cell: (r) => (
                  <span className="whitespace-nowrap">{formatDateTime(r.occurred_at)}</span>
                ),
              },
              {
                key: "action",
                header: "Action",
                cell: (r) => <span className="figure text-xs">{r.action}</span>,
              },
              { key: "entity", header: "Entity", cell: (r) => humanize(r.entity_type) },
              { key: "role", header: "Actor role", cell: (r) => humanize(r.actor_role) },
              { key: "changes", header: "Detail", cell: (r) => <Changes event={r} /> },
              {
                key: "request",
                header: "Request",
                cell: (r) => (
                  <span className="figure text-xs text-muted">
                    {r.request_id?.slice(0, 8) ?? "—"}
                  </span>
                ),
              },
            ]}
          />
          <Pagination
            page={data.page}
            pageSize={data.page_size}
            total={data.total}
            onPage={setPage}
          />
        </>
      )}
    </QueryView>
  );
}

export function AuditTrail({ entityType, entityId }: { entityType: string; entityId: string }) {
  return (
    <Card
      title="Audit log"
      description="Every change to this record, as written to the append-only audit table."
    >
      <AuditTable filters={{ entity_type: entityType, entity_id: entityId }} />
    </Card>
  );
}
