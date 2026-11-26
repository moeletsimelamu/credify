import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import type {
  ApplicationDetail,
  ApplicationDocument,
  DocumentType,
  InformationRequest,
  Product,
  StaffMember,
  StatusHistory,
} from "@/api/types";
import { formatDateTime, humanize } from "@/shared/lib/format";
import { DOCUMENT_TYPES, options, statusLabel } from "@/shared/lib/labels";
import {
  Button,
  DataTable,
  EmptyState,
  FormMessage,
  KeyValueList,
  MoneyText,
  QueryView,
  SelectField,
  StatusBadge,
  TextAreaField,
} from "@/shared/ui";

import {
  useAddDocument,
  useDocuments,
  useInformationRequests,
  useRespondToRequest,
  useReviewDocument,
  useStatusHistory,
} from "./api";

export function ApplicationSummary({
  application,
  product,
}: {
  application: ApplicationDetail;
  product: Product | undefined;
}) {
  const c = application.currency;
  return (
    <KeyValueList
      columns={3}
      items={[
        { label: "Product", value: product?.name ?? "—" },
        {
          label: "Amount requested",
          value: <MoneyText amount={application.requested_amount} currency={c} />,
        },
        { label: "Term", value: `${application.requested_term_months} months`, numeric: true },
        { label: "Purpose", value: humanize(application.purpose) },
        { label: "Submitted", value: formatDateTime(application.submitted_at) },
        { label: "Decided", value: formatDateTime(application.decided_at) },
        ...(application.purpose_description
          ? [{ label: "Details", value: application.purpose_description }]
          : []),
      ]}
    />
  );
}

// --- Documents ---------------------------------------------------------------------------

const ALLOWED = ["application/pdf", "image/jpeg", "image/png"];
const MAX_BYTES = 10 * 1024 * 1024;

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.ceil(bytes / 1024)} KB`;
}

/**
 * Documents are simulated: only the file's name, type and size are recorded. The file itself
 * never leaves the browser.
 */
export function DocumentsPanel({
  applicationId,
  canAdd,
  canReview,
}: {
  applicationId: string;
  canAdd: boolean;
  canReview: boolean;
}) {
  const documents = useDocuments(applicationId);
  const add = useAddDocument(applicationId);
  const review = useReviewDocument(applicationId);
  const [type, setType] = useState<DocumentType | "">("");
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [inputKey, setInputKey] = useState(0);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setProblem(null);
    if (!type) return setProblem("Choose the document type.");
    if (!file) return setProblem("Choose a file.");
    if (!ALLOWED.includes(file.type)) return setProblem("Use a PDF, JPEG or PNG file.");
    if (file.size > MAX_BYTES) return setProblem("Files can be at most 10 MB.");
    add.mutate(
      { document_type: type, file_name: file.name, content_type: file.type, size_bytes: file.size },
      {
        onSuccess: () => {
          setFile(null);
          setType("");
          setInputKey((key) => key + 1);
        },
      },
    );
  };

  return (
    <div className="flex flex-col gap-4">
      <QueryView
        query={documents}
        isEmpty={(rows) => rows.length === 0}
        empty={
          <EmptyState title="No documents yet">
            {canAdd
              ? "Add a payslip or bank statement to support your application."
              : "The applicant hasn't added any documents."}
          </EmptyState>
        }
      >
        {(rows) => (
          <DataTable<ApplicationDocument>
            caption="Supporting documents"
            rows={rows}
            rowKey={(row) => row.id}
            columns={[
              { key: "type", header: "Type", cell: (r) => humanize(r.document_type) },
              {
                key: "name",
                header: "File",
                cell: (r) => <span className="break-all">{r.file_name}</span>,
              },
              { key: "size", header: "Size", numeric: true, cell: (r) => formatSize(r.size_bytes) },
              { key: "at", header: "Added", cell: (r) => formatDateTime(r.uploaded_at) },
              { key: "status", header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
              ...(canReview
                ? [
                    {
                      key: "review",
                      header: <span className="sr-only">Review</span>,
                      align: "right" as const,
                      cell: (r: ApplicationDocument) =>
                        r.status === "RECEIVED" ? (
                          <div className="flex justify-end gap-1">
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={review.isPending}
                              onClick={() =>
                                review.mutate({ documentId: r.id, status: "ACCEPTED" })
                              }
                              aria-label={`Accept ${r.file_name}`}
                            >
                              Accept
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={review.isPending}
                              onClick={() =>
                                review.mutate({ documentId: r.id, status: "REJECTED" })
                              }
                              aria-label={`Reject ${r.file_name}`}
                            >
                              Reject
                            </Button>
                          </div>
                        ) : null,
                    },
                  ]
                : []),
            ]}
          />
        )}
      </QueryView>
      <FormMessage error={review.error} />
      {canAdd && (
        <form
          onSubmit={submit}
          noValidate
          className="flex flex-col gap-3 rounded-card border border-line bg-sunken/50 p-4"
        >
          <p className="text-sm font-medium">Add a document</p>
          <p className="text-xs text-muted">
            Simulation: only the file name, type and size are recorded. The file itself is not
            uploaded.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <SelectField
              label="Document type"
              placeholder="Choose…"
              options={options(DOCUMENT_TYPES)}
              value={type}
              onChange={(e) => setType(e.target.value as DocumentType)}
            />
            <div className="flex flex-col gap-1.5">
              <label htmlFor={`doc-file-${applicationId}`} className="text-sm font-medium">
                File (PDF, JPEG or PNG, up to 10 MB)
              </label>
              <input
                key={inputKey}
                id={`doc-file-${applicationId}`}
                type="file"
                accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
                className="text-sm"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </div>
          </div>
          {problem && (
            <p role="alert" className="text-xs font-medium text-critical">
              {problem}
            </p>
          )}
          <FormMessage
            error={add.error}
            success={add.isSuccess ? "Document recorded." : undefined}
          />
          <div>
            <Button type="submit" variant="secondary" loading={add.isPending}>
              Add document
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

// --- Information requests ----------------------------------------------------------------

const responseSchema = z.object({
  response: z
    .string()
    .trim()
    .min(1, "Write a response")
    .max(2000, "Keep it under 2,000 characters"),
});

function RespondForm({
  applicationId,
  request,
}: {
  applicationId: string;
  request: InformationRequest;
}) {
  const respond = useRespondToRequest(applicationId);
  const form = useForm({ resolver: zodResolver(responseSchema), defaultValues: { response: "" } });
  const submit = form.handleSubmit((values) =>
    respond
      .mutateAsync({ requestId: request.id, response: values.response })
      .then(() => form.reset()),
  );
  return (
    <form onSubmit={submit} noValidate className="mt-3 flex flex-col gap-3">
      <TextAreaField
        label="Your response"
        rows={3}
        required
        error={form.formState.errors.response?.message}
        {...form.register("response")}
      />
      <FormMessage error={respond.error} />
      <div>
        <Button type="submit" size="sm" loading={form.formState.isSubmitting}>
          Send response
        </Button>
      </div>
    </form>
  );
}

export function InformationRequestsPanel({
  applicationId,
  canRespond,
  emptyText,
}: {
  applicationId: string;
  canRespond: boolean;
  emptyText: string;
}) {
  const requests = useInformationRequests(applicationId);
  return (
    <QueryView
      query={requests}
      isEmpty={(rows) => rows.length === 0}
      empty={<EmptyState title="No information requests">{emptyText}</EmptyState>}
    >
      {(rows) => (
        <ul className="flex flex-col gap-3">
          {rows.map((request) => (
            <li key={request.id} className="rounded-card border border-line p-4">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm whitespace-pre-line">{request.message}</p>
                <StatusBadge status={request.status} />
              </div>
              <p className="mt-1 text-xs text-muted">
                Requested {formatDateTime(request.created_at)}
              </p>
              {request.response_text && (
                <div className="mt-3 border-l-2 border-accent pl-3">
                  <p className="text-xs font-medium text-muted">
                    Response · {formatDateTime(request.responded_at)}
                  </p>
                  <p className="text-sm whitespace-pre-line">{request.response_text}</p>
                </div>
              )}
              {canRespond && request.status === "OPEN" && (
                <RespondForm applicationId={applicationId} request={request} />
              )}
            </li>
          ))}
        </ul>
      )}
    </QueryView>
  );
}

// --- Status timeline ---------------------------------------------------------------------

export function StatusTimeline({
  applicationId,
  staff,
}: {
  applicationId: string;
  staff?: StaffMember[];
}) {
  const history = useStatusHistory(applicationId);
  const name = (id: string | null) =>
    id ? (staff?.find((member) => member.id === id)?.full_name ?? "Applicant") : "System";
  return (
    <QueryView
      query={history}
      isEmpty={(rows) => rows.length === 0}
      empty={<EmptyState title="No status changes yet" />}
    >
      {(rows) => (
        <ol className="relative flex flex-col gap-4 border-l border-line-strong pl-5">
          {[...rows].reverse().map((entry: StatusHistory) => (
            <li key={entry.id} className="relative">
              <span
                aria-hidden="true"
                className="absolute top-1.5 -left-[1.6rem] size-2.5 rounded-full border-2 border-surface bg-accent"
              />
              <p className="text-sm">
                {entry.from_status ? `${statusLabel(entry.from_status)} → ` : ""}
                <span className="font-medium">{statusLabel(entry.to_status)}</span>
              </p>
              <p className="text-xs text-muted">
                {formatDateTime(entry.created_at)}
                {staff && ` · ${name(entry.actor_user_id)}`}
              </p>
              {entry.reason && <p className="mt-1 text-sm text-fg/90">{entry.reason}</p>}
            </li>
          ))}
        </ol>
      )}
    </QueryView>
  );
}
