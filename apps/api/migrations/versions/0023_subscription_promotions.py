"""Subscription promotions and immutable economics; no enrollment or billing."""
from alembic import op
import sqlalchemy as sa

revision = "0023_subscription_promotions"
down_revision = "0022_client_subscriptions"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("is_vendiq_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("""
CREATE TABLE subscription_promotions (
	id UUID NOT NULL, 
	code VARCHAR(64) NOT NULL, 
	discount_type VARCHAR(10) NOT NULL, 
	value NUMERIC(12, 2) NOT NULL, 
	currency VARCHAR(3), 
	plan_scope VARCHAR(8) NOT NULL, 
	duration VARCHAR(20) NOT NULL, 
	cycles INTEGER NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE, 
	ends_at TIMESTAMP WITH TIME ZONE, 
	max_total_uses INTEGER, 
	max_uses_per_client INTEGER DEFAULT '1', 
	restricted_client_id UUID, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_sp_finite_value CHECK (value <= 9999999999.99),
	CONSTRAINT ck_sp_code CHECK (code = upper(trim(code)) AND length(code) > 0), 
	CONSTRAINT ck_sp_discount CHECK ((discount_type = 'PERCENTAGE' AND value > 0 AND value <= 100) OR (discount_type = 'FIXED' AND value > 0 AND currency IS NOT NULL)), 
	CONSTRAINT ck_sp_currency CHECK (currency IS NULL OR (length(currency) = 3 AND currency = upper(currency))), 
	CONSTRAINT ck_sp_scope CHECK (plan_scope IN ('ALL', 'SELECTED')), 
	CONSTRAINT ck_sp_duration CHECK ((duration = 'FIRST_PAID_MONTH' AND cycles = 1) OR (duration = 'NEXT_N_RENEWALS' AND cycles > 0)), 
	CONSTRAINT ck_sp_dates CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at), 
	CONSTRAINT ck_sp_total_limit CHECK (max_total_uses IS NULL OR max_total_uses > 0), 
	CONSTRAINT ck_sp_client_limit CHECK (max_uses_per_client IS NULL OR max_uses_per_client > 0), 
	UNIQUE (code), 
	FOREIGN KEY(restricted_client_id) REFERENCES clients (id) ON DELETE RESTRICT
)

    """)
    op.execute("""
CREATE TABLE subscription_promotion_plans (
	promotion_id UUID NOT NULL, 
	plan_id UUID NOT NULL, 
	PRIMARY KEY (promotion_id, plan_id), 
	FOREIGN KEY(promotion_id) REFERENCES subscription_promotions (id) ON DELETE CASCADE, 
	FOREIGN KEY(plan_id) REFERENCES plans (id) ON DELETE RESTRICT
)

    """)
    op.execute("""
CREATE TABLE subscription_promotion_redemptions (
	id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	subscription_id UUID NOT NULL, 
	plan_id UUID NOT NULL, 
	promotion_id UUID NOT NULL, 
	snapshot JSON NOT NULL, 
	cycles_granted INTEGER NOT NULL, 
	cycles_consumed INTEGER DEFAULT '0' NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	idempotency_key VARCHAR(180) NOT NULL, 
	confirmed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_spr_identity UNIQUE (id, client_id, subscription_id, plan_id, promotion_id), 
	CONSTRAINT ck_spr_cycles CHECK (cycles_granted > 0 AND cycles_consumed >= 0 AND cycles_consumed <= cycles_granted), 
	FOREIGN KEY(client_id) REFERENCES clients (id) ON DELETE RESTRICT, 
	FOREIGN KEY(subscription_id) REFERENCES client_subscriptions (id) ON DELETE RESTRICT, 
	FOREIGN KEY(plan_id) REFERENCES plans (id) ON DELETE RESTRICT, 
	FOREIGN KEY(promotion_id) REFERENCES subscription_promotions (id) ON DELETE RESTRICT, 
	UNIQUE (idempotency_key)
)

    """)
    op.execute('CREATE INDEX ix_subscription_promotion_redemptions_promotion_id ON subscription_promotion_redemptions (promotion_id)')
    op.execute('CREATE UNIQUE INDEX uq_spr_active_subscription ON subscription_promotion_redemptions (subscription_id) WHERE is_active')
    op.execute("""
CREATE TABLE subscription_charges (
	id UUID NOT NULL, 
	client_id UUID NOT NULL, 
	subscription_id UUID NOT NULL, 
	plan_id UUID NOT NULL, 
	promotion_id UUID, 
	redemption_id UUID, 
	cycle_number INTEGER NOT NULL, 
	period_start TIMESTAMP WITH TIME ZONE NOT NULL, 
	period_end TIMESTAMP WITH TIME ZONE NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	original_price NUMERIC(12, 2) NOT NULL, 
	discount NUMERIC(12, 2) NOT NULL, 
	total_final NUMERIC(12, 2) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	issued_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	paid_at TIMESTAMP WITH TIME ZONE, 
	idempotency_key VARCHAR(180) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_sc_cycle UNIQUE (subscription_id, cycle_number), 
	CONSTRAINT fk_sc_redemption_identity FOREIGN KEY(redemption_id, client_id, subscription_id, plan_id, promotion_id) REFERENCES subscription_promotion_redemptions (id, client_id, subscription_id, plan_id, promotion_id) ON DELETE RESTRICT, 
	CONSTRAINT ck_sc_period CHECK (cycle_number > 0 AND period_end > period_start), 
	CONSTRAINT ck_sc_finite_price CHECK (original_price <= 9999999999.99),
	CONSTRAINT ck_sc_amounts CHECK (original_price >= 0 AND discount >= 0 AND discount <= original_price AND total_final = original_price - discount AND total_final >= 0), 
	CONSTRAINT ck_sc_currency CHECK (length(currency) = 3 AND currency = upper(currency)), 
	CONSTRAINT ck_sc_promotion CHECK ((redemption_id IS NULL AND promotion_id IS NULL AND discount = 0) OR (redemption_id IS NOT NULL AND promotion_id IS NOT NULL)), 
	CONSTRAINT ck_sc_status CHECK (status IN ('ISSUED', 'PAID', 'VOID')), 
	CONSTRAINT ck_sc_paid CHECK ((status = 'PAID' AND paid_at IS NOT NULL AND paid_at >= issued_at) OR (status <> 'PAID' AND paid_at IS NULL)), 
	FOREIGN KEY(client_id) REFERENCES clients (id) ON DELETE RESTRICT, 
	FOREIGN KEY(subscription_id) REFERENCES client_subscriptions (id) ON DELETE RESTRICT, 
	FOREIGN KEY(plan_id) REFERENCES plans (id) ON DELETE RESTRICT, 
	FOREIGN KEY(promotion_id) REFERENCES subscription_promotions (id) ON DELETE RESTRICT, 
	FOREIGN KEY(redemption_id) REFERENCES subscription_promotion_redemptions (id) ON DELETE RESTRICT, 
	UNIQUE (idempotency_key)
)

    """)
    # Deferred checks allow replacing a SELECTED plan set atomically.
    op.execute("""
        CREATE FUNCTION vendiq_check_promotion_plans() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target uuid; previous_target uuid;
        BEGIN
            IF TG_TABLE_NAME = 'subscription_promotions' THEN
                target := NEW.id;
            ELSE
                IF TG_OP = 'DELETE' THEN target := OLD.promotion_id;
                ELSE target := NEW.promotion_id; END IF;
                IF TG_OP = 'UPDATE' THEN previous_target := OLD.promotion_id; END IF;
            END IF;
            PERFORM 1 FROM subscription_promotions WHERE id = target OR id = previous_target ORDER BY id FOR UPDATE;
            IF EXISTS (SELECT 1 FROM subscription_promotions p WHERE (p.id = target OR p.id = previous_target) AND
                ((p.plan_scope = 'SELECTED' AND NOT EXISTS
                    (SELECT 1 FROM subscription_promotion_plans pp WHERE pp.promotion_id = p.id)) OR
                 (p.plan_scope = 'ALL' AND EXISTS
                    (SELECT 1 FROM subscription_promotion_plans pp WHERE pp.promotion_id = p.id)))) THEN
                RAISE EXCEPTION 'promotion plan scope is inconsistent';
            END IF;
            RETURN NULL;
        END $$;
        CREATE CONSTRAINT TRIGGER vendiq_promotion_scope
        AFTER INSERT OR UPDATE ON subscription_promotions DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION vendiq_check_promotion_plans();
        CREATE CONSTRAINT TRIGGER vendiq_promotion_plan_scope
        AFTER INSERT OR UPDATE OR DELETE ON subscription_promotion_plans DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION vendiq_check_promotion_plans();
    """)
    op.execute("""
        CREATE FUNCTION vendiq_preserve_economic_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'economic history cannot be deleted'; END IF;
            IF TG_TABLE_NAME = 'subscription_promotion_redemptions' THEN
                IF (to_jsonb(NEW) - 'cycles_consumed' - 'is_active') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'cycles_consumed' - 'is_active') THEN
                    RAISE EXCEPTION 'redemption conditions are immutable';
                END IF;
                IF NEW.cycles_consumed < OLD.cycles_consumed OR (NOT OLD.is_active AND NEW.is_active) THEN
                    RAISE EXCEPTION 'redemption cannot be reset';
                END IF;
            ELSE
                IF (to_jsonb(NEW) - 'status' - 'paid_at') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'status' - 'paid_at') THEN
                    RAISE EXCEPTION 'charge economics are immutable';
                END IF;
                IF OLD.status <> 'ISSUED' AND NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'final charge is immutable';
                END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER vendiq_redemption_history BEFORE UPDATE OR DELETE ON subscription_promotion_redemptions
        FOR EACH ROW EXECUTE FUNCTION vendiq_preserve_economic_history();
        CREATE TRIGGER vendiq_charge_history BEFORE UPDATE OR DELETE ON subscription_charges
        FOR EACH ROW EXECUTE FUNCTION vendiq_preserve_economic_history();
    """)
    op.execute("""
        CREATE FUNCTION vendiq_check_economic_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE owner uuid; trial_end timestamptz;
        BEGIN
            SELECT client_id, trial_ends_at INTO owner, trial_end FROM client_subscriptions
                WHERE id = NEW.subscription_id FOR UPDATE;
            IF owner IS DISTINCT FROM NEW.client_id THEN
                RAISE EXCEPTION 'subscription belongs to another client';
            END IF;
            IF TG_TABLE_NAME = 'subscription_charges' AND trial_end IS NOT NULL THEN
                IF NEW.period_start < trial_end THEN RAISE EXCEPTION 'paid cycles cannot include trial'; END IF;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER vendiq_redemption_identity BEFORE INSERT ON subscription_promotion_redemptions
        FOR EACH ROW EXECUTE FUNCTION vendiq_check_economic_identity();
        CREATE TRIGGER vendiq_charge_identity BEFORE INSERT ON subscription_charges
        FOR EACH ROW EXECUTE FUNCTION vendiq_check_economic_identity();
    """)


def downgrade():
    # Historical deletion requires a separately approved archival procedure.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM subscription_charges) OR
               EXISTS (SELECT 1 FROM subscription_promotion_redemptions) THEN
                RAISE EXCEPTION 'cannot downgrade with economic history';
            END IF;
        END $$;
    """)
    op.drop_table("subscription_charges")
    op.drop_table("subscription_promotion_redemptions")
    op.drop_table("subscription_promotion_plans")
    op.drop_table("subscription_promotions")
    op.execute("DROP FUNCTION vendiq_check_economic_identity()")
    op.execute("DROP FUNCTION vendiq_preserve_economic_history()")
    op.execute("DROP FUNCTION vendiq_check_promotion_plans()")
    op.drop_column("users", "is_vendiq_admin")
