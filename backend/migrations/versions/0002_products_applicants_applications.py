"""Loan products, applicant financial profiles, loan applications and their workflow tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Uses forbid_update_delete() from 0001.
APPEND_ONLY_TABLES = ("application_status_history", "review_notes")


def upgrade() -> None:
    op.execute("CREATE SEQUENCE loan_application_reference_seq START WITH 1 INCREMENT BY 1")
    op.create_table('loan_products',
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('currency', sa.Enum('ZAR', 'USD', 'EUR', 'GBP', name='currency', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('min_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('max_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('min_term_months', sa.SmallInteger(), nullable=False),
    sa.Column('max_term_months', sa.SmallInteger(), nullable=False),
    sa.Column('annual_interest_rate', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('initiation_fee', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('monthly_service_fee', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('late_fee', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('grace_period_days', sa.SmallInteger(), nullable=False),
    sa.Column('early_settlement_fee_rate', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('max_dti', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('warn_dti', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('min_residual_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('min_residual_rate', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('annual_interest_rate >= 0 AND annual_interest_rate < 1', name=op.f('ck_loan_products_interest_rate_range')),
    sa.CheckConstraint('early_settlement_fee_rate >= 0 AND early_settlement_fee_rate < 1', name=op.f('ck_loan_products_early_settlement_fee_rate_range')),
    sa.CheckConstraint('grace_period_days >= 0', name=op.f('ck_loan_products_grace_period_non_negative')),
    sa.CheckConstraint('initiation_fee >= 0 AND monthly_service_fee >= 0 AND late_fee >= 0', name=op.f('ck_loan_products_fees_non_negative')),
    sa.CheckConstraint('min_amount > 0 AND min_amount <= max_amount', name=op.f('ck_loan_products_amount_range')),
    sa.CheckConstraint('min_residual_amount >= 0 AND min_residual_rate >= 0 AND min_residual_rate < 1', name=op.f('ck_loan_products_residual_buffer_range')),
    sa.CheckConstraint('min_term_months >= 1 AND min_term_months <= max_term_months AND max_term_months <= 120', name=op.f('ck_loan_products_term_range')),
    sa.CheckConstraint('warn_dti > 0 AND warn_dti <= max_dti AND max_dti <= 1', name=op.f('ck_loan_products_dti_range')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_loan_products')),
    sa.UniqueConstraint('code', name=op.f('uq_loan_products_code'))
    )
    op.create_table('applicants',
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('first_name', sa.String(length=100), nullable=False),
    sa.Column('last_name', sa.String(length=100), nullable=False),
    sa.Column('date_of_birth', sa.Date(), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('address_line1', sa.String(length=200), nullable=False),
    sa.Column('address_line2', sa.String(length=200), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=False),
    sa.Column('postal_code', sa.String(length=10), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('synthetic_reference', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_applicants_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_applicants_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_applicants')),
    sa.UniqueConstraint('synthetic_reference', name=op.f('uq_applicants_synthetic_reference')),
    sa.UniqueConstraint('user_id', name=op.f('uq_applicants_user_id'))
    )
    op.create_table('credit_commitments',
    sa.Column('commitment_type', sa.Enum('HOME_LOAN', 'VEHICLE_FINANCE', 'CREDIT_CARD', 'PERSONAL_LOAN', 'STORE_ACCOUNT', 'STUDENT_LOAN', 'OTHER', name='commitment_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('lender_name', sa.String(length=200), nullable=False),
    sa.Column('monthly_instalment', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('outstanding_balance', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('credit_limit', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('currency', sa.Enum('ZAR', 'USD', 'EUR', 'GBP', name='currency', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('applicant_id', sa.Uuid(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('monthly_instalment >= 0 AND outstanding_balance >= 0 AND (credit_limit IS NULL OR credit_limit >= 0)', name=op.f('ck_credit_commitments_amounts_non_negative')),
    sa.ForeignKeyConstraint(['applicant_id'], ['applicants.id'], name=op.f('fk_credit_commitments_applicant_id_applicants'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_credit_commitments'))
    )
    op.create_index(op.f('ix_credit_commitments_applicant_id'), 'credit_commitments', ['applicant_id'], unique=False)
    op.create_table('employment_records',
    sa.Column('employer_name', sa.String(length=200), nullable=True),
    sa.Column('job_title', sa.String(length=120), nullable=True),
    sa.Column('employment_type', sa.Enum('PERMANENT', 'CONTRACT', 'SELF_EMPLOYED', 'PART_TIME', 'UNEMPLOYED', 'RETIRED', name='employment_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('applicant_id', sa.Uuid(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('NOT (is_current AND end_date IS NOT NULL)', name=op.f('ck_employment_records_current_has_no_end')),
    sa.CheckConstraint('end_date IS NULL OR end_date >= start_date', name=op.f('ck_employment_records_end_after_start')),
    sa.ForeignKeyConstraint(['applicant_id'], ['applicants.id'], name=op.f('fk_employment_records_applicant_id_applicants'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employment_records'))
    )
    op.create_index(op.f('ix_employment_records_applicant_id'), 'employment_records', ['applicant_id'], unique=False)
    op.create_table('expense_records',
    sa.Column('category', sa.Enum('HOUSING', 'TRANSPORT', 'FOOD', 'UTILITIES', 'EDUCATION', 'INSURANCE', 'MEDICAL', 'DEPENDANTS', 'OTHER', name='expense_category', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('monthly_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('currency', sa.Enum('ZAR', 'USD', 'EUR', 'GBP', name='currency', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('applicant_id', sa.Uuid(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('monthly_amount >= 0', name=op.f('ck_expense_records_amount_non_negative')),
    sa.ForeignKeyConstraint(['applicant_id'], ['applicants.id'], name=op.f('fk_expense_records_applicant_id_applicants'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_expense_records'))
    )
    op.create_index(op.f('ix_expense_records_applicant_id'), 'expense_records', ['applicant_id'], unique=False)
    op.create_table('income_records',
    sa.Column('source_type', sa.Enum('SALARY', 'SELF_EMPLOYMENT', 'RENTAL', 'PENSION', 'GRANT', 'OTHER', name='income_source_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('frequency', sa.Enum('WEEKLY', 'FORTNIGHTLY', 'MONTHLY', 'ANNUALLY', name='income_frequency', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('gross_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('net_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('currency', sa.Enum('ZAR', 'USD', 'EUR', 'GBP', name='currency', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('verification_status', sa.Enum('DECLARED', 'VERIFIED', 'REJECTED', name='verification_status', native_enum=False, create_constraint=True, length=32), server_default='DECLARED', nullable=False),
    sa.Column('applicant_id', sa.Uuid(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('gross_amount > 0 AND net_amount > 0 AND net_amount <= gross_amount', name=op.f('ck_income_records_amounts_valid')),
    sa.ForeignKeyConstraint(['applicant_id'], ['applicants.id'], name=op.f('fk_income_records_applicant_id_applicants'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_income_records'))
    )
    op.create_index(op.f('ix_income_records_applicant_id'), 'income_records', ['applicant_id'], unique=False)
    op.create_table('loan_applications',
    sa.Column('reference', sa.String(length=20), nullable=False),
    sa.Column('applicant_id', sa.Uuid(), nullable=False),
    sa.Column('loan_product_id', sa.Uuid(), nullable=False),
    sa.Column('requested_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('currency', sa.Enum('ZAR', 'USD', 'EUR', 'GBP', name='currency', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('requested_term_months', sa.SmallInteger(), nullable=False),
    sa.Column('purpose', sa.Enum('DEBT_CONSOLIDATION', 'HOME_IMPROVEMENT', 'EDUCATION', 'MEDICAL', 'VEHICLE', 'BUSINESS', 'PERSONAL', 'OTHER', name='loan_purpose', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('purpose_description', sa.String(length=500), nullable=True),
    sa.Column('status', sa.Enum('DRAFT', 'SUBMITTED', 'PENDING_REVIEW', 'UNDER_REVIEW', 'ADDITIONAL_INFO_REQUESTED', 'RECOMMENDED_APPROVAL', 'RECOMMENDED_REJECTION', 'APPROVED', 'DECLINED', 'WITHDRAWN', name='application_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('product_terms_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('assigned_officer_id', sa.Uuid(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version_id', sa.Integer(), server_default='1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('requested_amount > 0', name=op.f('ck_loan_applications_amount_positive')),
    sa.CheckConstraint('requested_term_months BETWEEN 1 AND 120', name=op.f('ck_loan_applications_term_range')),
    sa.ForeignKeyConstraint(['applicant_id'], ['applicants.id'], name=op.f('fk_loan_applications_applicant_id_applicants'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['assigned_officer_id'], ['users.id'], name=op.f('fk_loan_applications_assigned_officer_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['loan_product_id'], ['loan_products.id'], name=op.f('fk_loan_applications_loan_product_id_loan_products'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_loan_applications')),
    sa.UniqueConstraint('reference', name=op.f('uq_loan_applications_reference'))
    )
    op.create_index(op.f('ix_loan_applications_applicant_id'), 'loan_applications', ['applicant_id'], unique=False)
    op.create_index('ix_loan_applications_assigned_officer_status', 'loan_applications', ['assigned_officer_id', 'status'], unique=False)
    op.create_index('ix_loan_applications_status_submitted_at', 'loan_applications', ['status', 'submitted_at'], unique=False)
    op.create_index('uq_loan_applications_one_open_per_applicant', 'loan_applications', ['applicant_id'], unique=True, postgresql_where=sa.text("status NOT IN ('APPROVED', 'DECLINED', 'WITHDRAWN')"))
    op.create_table('application_status_history',
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('from_status', sa.Enum('DRAFT', 'SUBMITTED', 'PENDING_REVIEW', 'UNDER_REVIEW', 'ADDITIONAL_INFO_REQUESTED', 'RECOMMENDED_APPROVAL', 'RECOMMENDED_REJECTION', 'APPROVED', 'DECLINED', 'WITHDRAWN', name='from_status', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('to_status', sa.Enum('DRAFT', 'SUBMITTED', 'PENDING_REVIEW', 'UNDER_REVIEW', 'ADDITIONAL_INFO_REQUESTED', 'RECOMMENDED_APPROVAL', 'RECOMMENDED_REJECTION', 'APPROVED', 'DECLINED', 'WITHDRAWN', name='to_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_application_status_history_actor_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['application_id'], ['loan_applications.id'], name=op.f('fk_application_status_history_application_id_loan_applications'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_application_status_history'))
    )
    op.create_index(op.f('ix_application_status_history_application_id'), 'application_status_history', ['application_id'], unique=False)
    op.create_table('document_references',
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('document_type', sa.Enum('PAYSLIP', 'BANK_STATEMENT', 'ID_DOCUMENT', 'PROOF_OF_ADDRESS', 'OTHER', name='document_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('file_name', sa.String(length=200), nullable=False),
    sa.Column('storage_key', sa.String(length=200), nullable=False),
    sa.Column('content_type', sa.String(length=100), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('uploaded_by', sa.Uuid(), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Enum('RECEIVED', 'ACCEPTED', 'REJECTED', name='document_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('size_bytes > 0', name=op.f('ck_document_references_size_positive')),
    sa.ForeignKeyConstraint(['application_id'], ['loan_applications.id'], name=op.f('fk_document_references_application_id_loan_applications'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_document_references_uploaded_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_references')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_document_references_storage_key'))
    )
    op.create_index(op.f('ix_document_references_application_id'), 'document_references', ['application_id'], unique=False)
    op.create_table('information_requests',
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('requested_by', sa.Uuid(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'RESPONDED', 'CANCELLED', name='information_request_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('response_text', sa.Text(), nullable=True),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'RESPONDED') = (response_text IS NOT NULL AND responded_at IS NOT NULL)", name=op.f('ck_information_requests_response_matches_status')),
    sa.ForeignKeyConstraint(['application_id'], ['loan_applications.id'], name=op.f('fk_information_requests_application_id_loan_applications'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_information_requests_requested_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_information_requests'))
    )
    op.create_index(op.f('ix_information_requests_application_id'), 'information_requests', ['application_id'], unique=False)
    op.create_table('review_notes',
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('author_id', sa.Uuid(), nullable=True),
    sa.Column('note_type', sa.Enum('OFFICER_NOTE', 'ANALYST_ASSESSMENT', 'SYSTEM', name='note_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['loan_applications.id'], name=op.f('fk_review_notes_application_id_loan_applications'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_review_notes_author_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_review_notes'))
    )
    op.create_index(op.f('ix_review_notes_application_id'), 'review_notes', ['application_id'], unique=False)
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_update_delete()"
        )


def downgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.drop_index(op.f('ix_review_notes_application_id'), table_name='review_notes')
    op.drop_table('review_notes')
    op.drop_index(op.f('ix_information_requests_application_id'), table_name='information_requests')
    op.drop_table('information_requests')
    op.drop_index(op.f('ix_document_references_application_id'), table_name='document_references')
    op.drop_table('document_references')
    op.drop_index(op.f('ix_application_status_history_application_id'), table_name='application_status_history')
    op.drop_table('application_status_history')
    op.drop_index('uq_loan_applications_one_open_per_applicant', table_name='loan_applications', postgresql_where=sa.text("status NOT IN ('APPROVED', 'DECLINED', 'WITHDRAWN')"))
    op.drop_index('ix_loan_applications_status_submitted_at', table_name='loan_applications')
    op.drop_index('ix_loan_applications_assigned_officer_status', table_name='loan_applications')
    op.drop_index(op.f('ix_loan_applications_applicant_id'), table_name='loan_applications')
    op.drop_table('loan_applications')
    op.drop_index(op.f('ix_income_records_applicant_id'), table_name='income_records')
    op.drop_table('income_records')
    op.drop_index(op.f('ix_expense_records_applicant_id'), table_name='expense_records')
    op.drop_table('expense_records')
    op.drop_index(op.f('ix_employment_records_applicant_id'), table_name='employment_records')
    op.drop_table('employment_records')
    op.drop_index(op.f('ix_credit_commitments_applicant_id'), table_name='credit_commitments')
    op.drop_table('credit_commitments')
    op.drop_table('applicants')
    op.drop_table('loan_products')
    op.execute("DROP SEQUENCE IF EXISTS loan_application_reference_seq")
