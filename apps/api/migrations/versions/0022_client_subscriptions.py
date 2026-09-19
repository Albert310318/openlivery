"""Add VENDIQ plans and subscriptions without enrolling existing clients."""
import sqlalchemy as sa
from alembic import op

revision = "0022_client_subscriptions"
down_revision = "0021_portal_email_verification"
branch_labels = None
depends_on = None

# Snapshot owned by this migration, independent of future application changes.
INITIAL_MODULES = [
    ("ai_agent", "Agente IA", True),
    ("leads", "Leads", True),
    ("advisor_handoff", "Derivación al asesor", True),
    ("catalog", "Catálogo", False),
    ("orders", "Pedidos", False),
    ("appointments", "Citas", False),
    ("reservations", "Reservas", False),
    ("payments", "Pagos de clientes de la empresa", False),
    ("operations", "Operaciones", False),
    ("delivery", "Entregas", False),
    ("automations", "Automatizaciones", False),
    ("post_sale", "Posventa", False),
    ("analytics", "Analítica", False),
]


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("monthly_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("available_for_new_subscriptions", sa.Boolean(), nullable=False, server_default="false"),
        sa.CheckConstraint("monthly_price IS NULL OR monthly_price >= 0", name="ck_plans_monthly_price"),
        sa.CheckConstraint("length(currency) = 3 AND currency = upper(currency)", name="ck_plans_currency"),
    )
    op.create_table(
        "modules",
        sa.Column("code", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "plan_modules",
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("module_code", sa.String(64), sa.ForeignKey("modules.code", ondelete="RESTRICT"), primary_key=True),
    )
    op.create_table(
        "client_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.Uuid(), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("first_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_renewal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("client_id", name="uq_client_subscriptions_client_id"),
        sa.CheckConstraint(
            "status IN ('TRIAL', 'ACTIVE', 'PAYMENT_PENDING', 'SUSPENDED', 'CANCELLED')",
            name="ck_client_subscriptions_status",
        ),
        sa.CheckConstraint(
            "(first_activated_at IS NULL AND trial_started_at IS NULL AND trial_ends_at IS NULL) OR "
            "(first_activated_at IS NOT NULL AND trial_started_at IS NOT NULL AND trial_ends_at IS NOT NULL "
            "AND trial_started_at = first_activated_at AND trial_ends_at > trial_started_at)",
            name="ck_client_subscriptions_trial_dates",
        ),
        sa.CheckConstraint(
            "trial_ends_at IS NULL OR trial_ends_at = trial_started_at + interval '72 hours'",
            name="ck_client_subscriptions_trial_72h",
        ),
    )
    op.create_index("ix_client_subscriptions_plan_id", "client_subscriptions", ["plan_id"])
    # Integrity guard only: this never starts a trial or assigns any date.
    op.execute("""
        CREATE FUNCTION vendiq_preserve_first_activation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.first_activated_at IS NOT NULL
               AND NEW.first_activated_at IS DISTINCT FROM OLD.first_activated_at THEN
                RAISE EXCEPTION 'first_activated_at is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER vendiq_preserve_first_activation
        BEFORE UPDATE ON client_subscriptions
        FOR EACH ROW EXECUTE FUNCTION vendiq_preserve_first_activation()
    """)
    op.bulk_insert(
        sa.table("modules", sa.column("code", sa.String()), sa.column("name", sa.String()),
                 sa.column("is_available", sa.Boolean())),
        [{"code": code, "name": name, "is_available": available} for code, name, available in INITIAL_MODULES],
    )
    # No default plans/prices, client subscriptions, retroactive trials or client updates.


def downgrade() -> None:
    op.drop_table("client_subscriptions")
    op.execute("DROP FUNCTION vendiq_preserve_first_activation()")
    op.drop_table("plan_modules")
    op.drop_table("modules")
    op.drop_table("plans")
