import { useState } from "react";

import { errorMessage } from "@/api/client";
import type { ModelVersion } from "@/api/types";
import { useActivateModel, useModels } from "@/features/admin/api";
import { useCan } from "@/features/auth/useAuth";
import { usePageTitle } from "@/shared/lib/forms";
import { formatDateTime, formatPercent, humanize } from "@/shared/lib/format";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  KeyValueList,
  Notice,
  PageHeader,
  QueryView,
  StatusBadge,
} from "@/shared/ui";
import { bandRanges, type Bands } from "./bands";

type Metrics = Record<string, Record<string, number> | undefined>;
interface FeatureSpec {
  name: string;
  description: string;
  kind: string;
}

function metric(value: number | undefined, digits = 3): string {
  return value === undefined ? "—" : value.toFixed(digits);
}

function ModelDetail({ model }: { model: ModelVersion }) {
  const metrics = model.metrics as unknown as Metrics;
  const test = metrics.test ?? {};
  const atThreshold = metrics.test_at_threshold ?? {};
  const ece = metrics.calibration_ece ?? {};
  const features = ((model.feature_schema as { features?: FeatureSpec[] }).features ??
    []) as FeatureSpec[];
  const libraries = model.libraries as Record<string, string>;

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-6 xl:grid-cols-2">
        <Card
          title="Test-set performance"
          description="Scored once, after every choice was made on the validation split."
        >
          <KeyValueList
            columns={2}
            items={[
              { label: "ROC-AUC", value: metric(test.roc_auc), numeric: true },
              { label: "PR-AUC", value: metric(test.pr_auc), numeric: true },
              { label: "Brier score", value: metric(test.brier, 4), numeric: true },
              { label: "Calibration error (ECE)", value: metric(ece.test, 4), numeric: true },
              {
                label: "Observed default rate",
                value: test.observed_rate === undefined ? "—" : formatPercent(test.observed_rate),
                numeric: true,
              },
              {
                label: "Mean predicted",
                value: test.mean_predicted === undefined ? "—" : formatPercent(test.mean_predicted),
                numeric: true,
              },
            ]}
          />
        </Card>
        <Card
          title={`At the review threshold (${formatPercent(model.decision_threshold)})`}
          description="Applications above it are flagged for careful review — never declined automatically."
        >
          <KeyValueList
            columns={2}
            items={[
              {
                label: "Flagged",
                value:
                  atThreshold.flagged_rate === undefined
                    ? "—"
                    : formatPercent(atThreshold.flagged_rate),
                numeric: true,
              },
              {
                label: "Defaulters caught (recall)",
                value: atThreshold.recall === undefined ? "—" : formatPercent(atThreshold.recall),
                numeric: true,
              },
              {
                label: "Precision",
                value:
                  atThreshold.precision === undefined ? "—" : formatPercent(atThreshold.precision),
                numeric: true,
              },
              { label: "F1", value: metric(atThreshold.f1), numeric: true },
            ]}
          />
        </Card>
      </div>
      <Card title="Risk bands">
        <DataTable
          caption="Risk bands"
          rows={bandRanges(model.band_cutoffs as Bands)}
          rowKey={(row) => row.band}
          columns={[
            {
              key: "band",
              header: "Band",
              cell: (r) => <span className="font-semibold">{r.band}</span>,
            },
            { key: "range", header: "Probability of default", numeric: true, cell: (r) => r.range },
            { key: "guidance", header: "Guidance", cell: (r) => r.guidance },
          ]}
        />
      </Card>
      <div className="grid gap-6 xl:grid-cols-2">
        <Card title="Provenance">
          <KeyValueList
            items={[
              {
                label: "Artefact SHA-256",
                value: <span className="figure text-xs break-all">{model.artefact_sha256}</span>,
              },
              {
                label: "Training data SHA-256",
                value: (
                  <span className="figure text-xs break-all">{model.training_dataset_sha256}</span>
                ),
              },
              {
                label: "Data generator",
                value: `${model.dataset_generator_version} · seed ${model.random_seed}`,
              },
              {
                label: "Libraries",
                value: Object.entries(libraries)
                  .map(([name, version]) => `${name} ${version}`)
                  .join(" · "),
              },
              {
                label: "Git commit",
                value: <span className="figure text-xs">{model.git_commit ?? "—"}</span>,
              },
              { label: "Trained", value: formatDateTime(model.trained_at) },
            ]}
          />
        </Card>
        <Card
          title={`Features (${features.length})`}
          description="Protected attributes (sex, age) are never model inputs; they are used only to audit fairness offline."
        >
          <ul className="grid gap-1 text-sm sm:grid-cols-2">
            {features.map((feature) => (
              <li key={feature.name}>
                {feature.description}{" "}
                <span className="text-xs text-muted">· {humanize(feature.kind)}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Notice tone="advisory" title="Experimental and advisory">
        These metrics describe how well the model recovers a documented synthetic process, not
        real-world lending performance. See MODEL_CARD.md and the fairness report in ml/reports for
        limitations, including the age-band disparity disclosed there.
      </Notice>
    </div>
  );
}

export function ModelsPage() {
  usePageTitle("Risk models");
  const models = useModels();
  const activate = useActivateModel();
  const canManage = useCan("model:manage");
  const [selected, setSelected] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<ModelVersion | null>(null);

  return (
    <>
      <PageHeader
        title="Risk models"
        description="Registered model versions. Models are trained offline and registered from the ml/ pipeline; there is no upload."
      />
      <QueryView
        query={models}
        loadingLabel="Loading model versions"
        isEmpty={(rows) => rows.length === 0}
        empty={
          <EmptyState title="No models registered">
            From <code>ml/</code>, run <code>evaluate</code> then <code>register</code>; an
            administrator then activates the version here.
          </EmptyState>
        }
      >
        {(rows) => {
          const current =
            rows.find((row) => row.id === selected) ??
            rows.find((row) => row.status === "ACTIVE") ??
            rows[0]!;
          return (
            <div className="flex flex-col gap-6">
              {!rows.some((row) => row.status === "ACTIVE") && (
                <Notice tone="warning" title="No active model">
                  Applications can't be scored until a version is activated; scoring attempts are
                  recorded as failed and review continues.
                </Notice>
              )}
              <Card>
                <DataTable<ModelVersion>
                  caption="Model versions"
                  rows={rows}
                  rowKey={(row) => row.id}
                  rowClassName={(row) => (row.id === current.id ? "bg-accent-soft/50" : undefined)}
                  columns={[
                    {
                      key: "name",
                      header: "Model",
                      cell: (r) => (
                        <button
                          type="button"
                          className="font-medium text-accent hover:underline"
                          onClick={() => setSelected(r.id)}
                          aria-pressed={r.id === current.id}
                        >
                          {r.name} {r.version}
                        </button>
                      ),
                    },
                    { key: "algorithm", header: "Algorithm", cell: (r) => humanize(r.algorithm) },
                    {
                      key: "status",
                      header: "Status",
                      cell: (r) => <StatusBadge status={r.status} />,
                    },
                    {
                      key: "threshold",
                      header: "Threshold",
                      numeric: true,
                      cell: (r) => formatPercent(r.decision_threshold),
                    },
                    {
                      key: "trained",
                      header: "Trained",
                      cell: (r) => formatDateTime(r.trained_at),
                    },
                    {
                      key: "activated",
                      header: "Activated",
                      cell: (r) => formatDateTime(r.activated_at),
                    },
                    ...(canManage
                      ? [
                          {
                            key: "activate",
                            header: <span className="sr-only">Actions</span>,
                            align: "right" as const,
                            cell: (r: ModelVersion) =>
                              r.status === "CANDIDATE" ? (
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  onClick={() => setConfirming(r)}
                                >
                                  Activate
                                </Button>
                              ) : null,
                          },
                        ]
                      : []),
                  ]}
                />
              </Card>
              <ModelDetail model={current} />
            </div>
          );
        }}
      </QueryView>
      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(open) => {
          if (!open) {
            setConfirming(null);
            activate.reset();
          }
        }}
        title={confirming ? `Activate ${confirming.name} ${confirming.version}?` : "Activate"}
        confirmLabel="Activate model"
        pending={activate.isPending}
        error={
          activate.error ? (
            <Notice tone="critical">{errorMessage(activate.error)}</Notice>
          ) : undefined
        }
        onConfirm={() =>
          confirming && activate.mutate(confirming.id, { onSuccess: () => setConfirming(null) })
        }
      >
        The artefact is verified against its registered SHA-256 first. New scores will use this
        version, the current active version is retired, and the change is written to the audit log.
      </ConfirmDialog>
    </>
  );
}
