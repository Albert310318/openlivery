"""Durable activation evidence only; no enrollment, backfill or emitter hooks.

DDL snapshot is independent of application metadata. Acceptance timestamps may
come from a different clock than creation: no ordering constraint is imposed.
"""
from alembic import op
import sqlalchemy as sa

# Keep the Alembic identifier within its existing VARCHAR(32) version column.
revision = "0024_activation_evidence"
down_revision = "0023_subscription_promotions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE subscription_test_identities (
	id UUID NOT NULL,
	client_id UUID NOT NULL,
	identity_type VARCHAR(24) NOT NULL,
	identity_value VARCHAR(255) NOT NULL,
	label VARCHAR(180) DEFAULT '' NOT NULL,
	is_active BOOLEAN DEFAULT 'true' NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_sti_identity UNIQUE (client_id, identity_type, identity_value),
	CONSTRAINT ck_sti_type CHECK (identity_type IN ('whatsapp_phone', 'whatsapp_lid')),
	CONSTRAINT ck_sti_canonical CHECK ((identity_type = 'whatsapp_phone' AND identity_value ~ '^[+][1-9][0-9]{1,14}$') OR (identity_type = 'whatsapp_lid' AND identity_value ~ '^[1-9][0-9]*@lid$')),
	FOREIGN KEY(client_id) REFERENCES clients (id) ON DELETE RESTRICT
)
    """)
    op.execute("""
CREATE TABLE subscription_activation_deliveries (
	id UUID NOT NULL,
	client_id UUID NOT NULL,
	message_id UUID NOT NULL,
	channel VARCHAR(24) NOT NULL,
	whatsapp_channel_id UUID,
	whatsapp_cloud_channel_id UUID,
	origin VARCHAR(24) NOT NULL,
	recipient_identity VARCHAR(255) NOT NULL,
	environment VARCHAR(12) NOT NULL,
	test_identity_id UUID,
	transport_state VARCHAR(16) NOT NULL,
	external_message_id VARCHAR(255),
	confirmation_type VARCHAR(32),
	accepted_at TIMESTAMP WITH TIME ZONE,
	recorded_at TIMESTAMP WITH TIME ZONE,
	trial_state VARCHAR(16) NOT NULL,
	trial_reason VARCHAR(40),
	processed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_sad_message UNIQUE (message_id),
	CONSTRAINT ck_sad_channel CHECK (channel IN ('whatsapp_qr', 'whatsapp_cloud')),
	CONSTRAINT ck_sad_channel_fk CHECK ((channel = 'whatsapp_qr' AND whatsapp_channel_id IS NOT NULL AND whatsapp_cloud_channel_id IS NULL) OR (channel = 'whatsapp_cloud' AND whatsapp_cloud_channel_id IS NOT NULL AND whatsapp_channel_id IS NULL)),
	CONSTRAINT ck_sad_origin CHECK (origin = 'automatic_ai_reply'),
	CONSTRAINT ck_sad_environment CHECK (environment IN ('production', 'test')),
	CONSTRAINT ck_sad_recipient CHECK (recipient_identity ~ '^[+][1-9][0-9]{1,14}$' OR (channel = 'whatsapp_qr' AND recipient_identity ~ '^[1-9][0-9]*@lid$')),
	CONSTRAINT ck_sad_test CHECK (test_identity_id IS NULL OR environment = 'test'),
	CONSTRAINT ck_sad_transport CHECK (transport_state IN ('PREPARED', 'ACCEPTED', 'FAILED', 'UNKNOWN')),
	CONSTRAINT ck_sad_external_id CHECK (external_message_id IS NULL OR (length(trim(external_message_id)) > 0 AND external_message_id = trim(external_message_id))),
	CONSTRAINT ck_sad_external_whitespace CHECK (external_message_id IS NULL OR external_message_id !~ '[[:space:]]'),
	CONSTRAINT ck_sad_confirmation CHECK ((transport_state = 'ACCEPTED' AND external_message_id IS NOT NULL AND confirmation_type IS NOT NULL AND accepted_at IS NOT NULL AND recorded_at IS NOT NULL) OR (transport_state <> 'ACCEPTED' AND external_message_id IS NULL AND confirmation_type IS NULL AND accepted_at IS NULL AND recorded_at IS NULL)),
	CONSTRAINT ck_sad_confirmation_type CHECK (confirmation_type IS NULL OR (channel = 'whatsapp_qr' AND confirmation_type = 'BAILEYS_SEND_RESULT') OR (channel = 'whatsapp_cloud' AND confirmation_type = 'META_MESSAGES_ID')),
	CONSTRAINT ck_sad_trial CHECK (trial_state IN ('PENDING', 'STARTED', 'IGNORED')),
	CONSTRAINT ck_sad_trial_result CHECK ((trial_state = 'PENDING' AND trial_reason IS NULL AND processed_at IS NULL) OR (trial_state = 'STARTED' AND trial_reason IS NOT NULL AND trial_reason = 'FIRST_ACCEPTED_REPLY' AND processed_at IS NOT NULL AND transport_state = 'ACCEPTED' AND environment = 'production' AND test_identity_id IS NULL) OR (trial_state = 'IGNORED' AND processed_at IS NOT NULL AND trial_reason IS NOT NULL AND trial_reason IN ('TEST_IDENTITY', 'TEST_ENVIRONMENT', 'ALREADY_STARTED', 'EXISTING_SUBSCRIPTION', 'TRANSPORT_FAILED'))),
	CONSTRAINT ck_sad_test_reason CHECK (trial_reason IS NULL OR (trial_reason <> 'TEST_IDENTITY' OR (environment = 'test' AND test_identity_id IS NOT NULL))),
	CONSTRAINT ck_sad_environment_reason CHECK (trial_reason IS NULL OR trial_reason <> 'TEST_ENVIRONMENT' OR environment = 'test'),
	CONSTRAINT ck_sad_failed_reason CHECK (trial_reason IS NULL OR trial_reason <> 'TRANSPORT_FAILED' OR transport_state = 'FAILED'),
	FOREIGN KEY(client_id) REFERENCES clients (id) ON DELETE RESTRICT,
	FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE RESTRICT,
	FOREIGN KEY(whatsapp_channel_id) REFERENCES whatsapp_channels (id) ON DELETE RESTRICT,
	FOREIGN KEY(whatsapp_cloud_channel_id) REFERENCES whatsapp_cloud_channels (id) ON DELETE RESTRICT,
	FOREIGN KEY(test_identity_id) REFERENCES subscription_test_identities (id) ON DELETE RESTRICT
)
    """)
    op.execute("""
CREATE INDEX ix_sad_client ON subscription_activation_deliveries (client_id)
    """)
    op.execute("""
CREATE INDEX ix_sad_cloud_channel ON subscription_activation_deliveries (whatsapp_cloud_channel_id)
    """)
    op.execute("""
CREATE INDEX ix_sad_pending ON subscription_activation_deliveries (client_id, accepted_at, id) WHERE transport_state = 'ACCEPTED' AND trial_state = 'PENDING'
    """)
    op.execute("""
CREATE INDEX ix_sad_qr_channel ON subscription_activation_deliveries (whatsapp_channel_id)
    """)
    op.execute("""
CREATE INDEX ix_sad_test_identity ON subscription_activation_deliveries (test_identity_id)
    """)
    op.execute("""
CREATE INDEX ix_sad_unresolved ON subscription_activation_deliveries (created_at, id) WHERE transport_state IN ('PREPARED', 'UNKNOWN')
    """)
    op.execute("""
CREATE UNIQUE INDEX uq_sad_cloud_external ON subscription_activation_deliveries (whatsapp_cloud_channel_id, external_message_id) WHERE external_message_id IS NOT NULL
    """)
    op.execute("""
CREATE UNIQUE INDEX uq_sad_qr_external ON subscription_activation_deliveries (whatsapp_channel_id, external_message_id) WHERE external_message_id IS NOT NULL
    """)
    op.execute("""
CREATE UNIQUE INDEX uq_sad_started_client ON subscription_activation_deliveries (client_id) WHERE trial_state = 'STARTED'
    """)
    op.execute("""
CREATE FUNCTION vendiq_activation_identity_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM clients WHERE id = NEW.client_id FOR UPDATE;
    IF TG_OP = 'UPDATE' AND ROW(NEW.id, NEW.client_id, NEW.identity_type, NEW.identity_value, NEW.created_at)
        IS DISTINCT FROM ROW(OLD.id, OLD.client_id, OLD.identity_type, OLD.identity_value, OLD.created_at) THEN
        RAISE EXCEPTION 'Test identity ownership and value are immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER vendiq_activation_identity_guard
BEFORE INSERT OR UPDATE ON subscription_test_identities
FOR EACH ROW EXECUTE FUNCTION vendiq_activation_identity_guard();

CREATE FUNCTION vendiq_activation_delivery_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    source record;
    destination text;
    identity_match uuid;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Activation evidence cannot be deleted' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF (to_jsonb(NEW) - ARRAY['transport_state','external_message_id','confirmation_type','accepted_at','recorded_at','trial_state','trial_reason','processed_at'])
            IS DISTINCT FROM
           (to_jsonb(OLD) - ARRAY['transport_state','external_message_id','confirmation_type','accepted_at','recorded_at','trial_state','trial_reason','processed_at']) THEN
            RAISE EXCEPTION 'Activation evidence identity and classification are immutable' USING ERRCODE = '23514';
        END IF;
        IF OLD.transport_state = 'ACCEPTED' AND
            ROW(NEW.transport_state, NEW.external_message_id, NEW.confirmation_type, NEW.accepted_at, NEW.recorded_at)
            IS DISTINCT FROM ROW(OLD.transport_state, OLD.external_message_id, OLD.confirmation_type, OLD.accepted_at, OLD.recorded_at) THEN
            RAISE EXCEPTION 'Conflicting ACCEPTED confirmation: transport evidence is immutable' USING ERRCODE = '23514';
        END IF;
        IF OLD.trial_state <> 'PENDING' AND ROW(NEW.trial_state, NEW.trial_reason, NEW.processed_at)
            IS DISTINCT FROM ROW(OLD.trial_state, OLD.trial_reason, OLD.processed_at) THEN
            RAISE EXCEPTION 'Trial evidence processing result is immutable' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    -- Serialize registration and classification; this never activates a trial.
    PERFORM 1 FROM clients WHERE id = NEW.client_id FOR UPDATE;
    SELECT m.role, m.sender_type, c.client_id, c.agency_id, c.channel,
           c.external_chat_id, c.whatsapp_channel_id, c.whatsapp_cloud_channel_id
      INTO source FROM messages m JOIN conversations c ON c.id = m.conversation_id
      WHERE m.id = NEW.message_id FOR SHARE OF m, c;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Activation message not found' USING ERRCODE = '23503';
    END IF;
    IF source.client_id IS DISTINCT FROM NEW.client_id
        OR source.agency_id IS DISTINCT FROM (SELECT agency_id FROM clients WHERE id = NEW.client_id)
        OR source.role <> 'assistant' OR source.sender_type <> 'ai' THEN
        RAISE EXCEPTION 'Activation requires an AI assistant message owned by the client' USING ERRCODE = '23514';
    END IF;
    IF NEW.channel = 'whatsapp_qr' THEN
        IF source.channel <> 'whatsapp' OR source.whatsapp_channel_id IS DISTINCT FROM NEW.whatsapp_channel_id THEN
            RAISE EXCEPTION 'Activation message QR channel mismatch' USING ERRCODE = '23514';
        END IF;
        PERFORM 1 FROM whatsapp_channels WHERE id = NEW.whatsapp_channel_id
            AND client_id = NEW.client_id AND agency_id = source.agency_id FOR SHARE;
    ELSIF NEW.channel = 'whatsapp_cloud' THEN
        IF source.channel <> 'whatsapp_cloud' OR source.whatsapp_cloud_channel_id IS DISTINCT FROM NEW.whatsapp_cloud_channel_id THEN
            RAISE EXCEPTION 'Activation message Cloud channel mismatch' USING ERRCODE = '23514';
        END IF;
        PERFORM 1 FROM whatsapp_cloud_channels WHERE id = NEW.whatsapp_cloud_channel_id
            AND client_id = NEW.client_id AND agency_id = source.agency_id FOR SHARE;
    ELSE
        RAISE EXCEPTION 'Ineligible activation channel' USING ERRCODE = '23514';
    END IF;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Activation channel ownership mismatch' USING ERRCODE = '23514';
    END IF;
    destination := source.external_chat_id;
    IF NEW.channel = 'whatsapp_qr' AND destination ~ '^[1-9][0-9]{1,14}@s[.]whatsapp[.]net$' THEN
        destination := '+' || split_part(destination, '@', 1);
    ELSIF NEW.channel = 'whatsapp_cloud' AND destination ~ '^[1-9][0-9]{1,14}$' THEN
        destination := '+' || destination;
    END IF;
    IF destination IS DISTINCT FROM NEW.recipient_identity THEN
        RAISE EXCEPTION 'Activation recipient mismatch' USING ERRCODE = '23514';
    END IF;
    IF NEW.test_identity_id IS NOT NULL THEN
        PERFORM 1 FROM subscription_test_identities
          WHERE id = NEW.test_identity_id AND client_id = NEW.client_id
            AND identity_value = NEW.recipient_identity AND is_active;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Test identity must match an active client recipient' USING ERRCODE = '23514';
        END IF;
    END IF;
    SELECT id INTO identity_match FROM subscription_test_identities
      WHERE client_id = NEW.client_id AND identity_value = NEW.recipient_identity AND is_active;
    IF FOUND AND (NEW.environment <> 'test' OR NEW.test_identity_id IS DISTINCT FROM identity_match) THEN
        RAISE EXCEPTION 'Registered test recipient requires test classification and identity' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER vendiq_activation_delivery_guard
BEFORE INSERT OR UPDATE OR DELETE ON subscription_activation_deliveries
FOR EACH ROW EXECUTE FUNCTION vendiq_activation_delivery_guard();

CREATE FUNCTION vendiq_activation_no_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Activation evidence cannot be truncated' USING ERRCODE = '23514';
END;
$$;
CREATE TRIGGER vendiq_activation_no_truncate
BEFORE TRUNCATE ON subscription_activation_deliveries
FOR EACH STATEMENT EXECUTE FUNCTION vendiq_activation_no_truncate();
    """)


def downgrade():
    # Never silently erase evidence or configured test identities.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM subscription_activation_deliveries) OR EXISTS (SELECT 1 FROM subscription_test_identities)")):
        raise RuntimeError("Cannot downgrade 0024 while activation evidence or test identities exist")
    op.drop_table("subscription_activation_deliveries")
    op.drop_table("subscription_test_identities")
    op.execute("DROP FUNCTION vendiq_activation_delivery_guard()")
    op.execute("DROP FUNCTION vendiq_activation_identity_guard()")
    op.execute("DROP FUNCTION vendiq_activation_no_truncate()")
