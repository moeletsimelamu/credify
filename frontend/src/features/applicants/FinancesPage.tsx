import { usePageTitle } from "@/shared/lib/forms";
import { ButtonLink, EmptyState, PageHeader, QueryView } from "@/shared/ui";

import { useFinancialProfile, useMyApplicant } from "./api";
import { FinancialRecords } from "./FinancialRecords";

export function FinancesPage() {
  usePageTitle("My finances");
  const me = useMyApplicant();
  const profile = useFinancialProfile(me.data?.id);

  return (
    <>
      <PageHeader
        title="My finances"
        description="Your employment, income, expenses and existing credit. They are used to check whether a new loan is affordable."
      />
      <QueryView
        query={me}
        isEmpty={(applicant) => applicant === null}
        empty={
          <EmptyState
            title="Create your profile first"
            action={
              <ButtonLink to="/portal/profile" variant="primary">
                Add my details
              </ButtonLink>
            }
          >
            Your financial information belongs to your applicant profile.
          </EmptyState>
        }
      >
        {(applicant) => (
          <QueryView query={profile} loadingLabel="Loading your finances">
            {(data) => <FinancialRecords applicantId={applicant!.id} profile={data} editable />}
          </QueryView>
        )}
      </QueryView>
    </>
  );
}
