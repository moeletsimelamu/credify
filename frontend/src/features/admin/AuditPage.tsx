import { useDeferredValue, useState } from "react";

import { usePageTitle } from "@/shared/lib/forms";
import { Card, PageHeader, SelectField, TextField } from "@/shared/ui";

import { AuditTable } from "./AuditTrail";

const ENTITY_TYPES = [
  "loan_application",
  "applicant",
  "affordability_assessment",
  "credit_risk_assessment",
  "credit_bureau_report",
  "recommendation",
  "lending_decision",
  "loan_transaction",
  "loan_charge",
  "loan_product",
  "model_version",
  "user",
  "review_note",
  "information_request",
  "document_reference",
  "job",
];

export function AuditPage() {
  usePageTitle("Audit log");
  const [entityType, setEntityType] = useState("");
  const [action, setAction] = useState("");
  const deferredAction = useDeferredValue(action.trim());
  return (
    <>
      <PageHeader
        title="Audit log"
        description="Append-only record of who did what, when. Secrets are never written here."
      />
      <div className="mb-4 grid max-w-2xl gap-3 sm:grid-cols-2">
        <SelectField
          label="Entity"
          value={entityType}
          onChange={(event) => setEntityType(event.target.value)}
          placeholder="All entities"
          options={ENTITY_TYPES.map((value) => ({ value, label: value.replaceAll("_", " ") }))}
        />
        <TextField
          label="Action"
          value={action}
          onChange={(event) => setAction(event.target.value)}
          placeholder="e.g. application.decided"
          hint="Exact action name."
        />
      </div>
      <Card>
        <AuditTable
          key={`${entityType}|${deferredAction}`}
          filters={{ entity_type: entityType || undefined, action: deferredAction || undefined }}
        />
      </Card>
    </>
  );
}
