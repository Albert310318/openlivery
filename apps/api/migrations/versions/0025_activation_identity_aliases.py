"""Allow a trusted Baileys phone alternate to classify a QR LID as test."""

from alembic import op
import sqlalchemy as sa


revision = "0025_activation_identity_aliases"
down_revision = "0024_activation_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "subscription_activation_deliveries",
        sa.Column("source_message_timestamp", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("""
CREATE OR REPLACE FUNCTION vendiq_activation_delivery_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    source record;
    destination text;
    identity_match uuid;
    identity_type text;
    identity_value text;
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
            RAISE EXCEPTION 'Conflicting ACCEPTED confirmation: transport evidence is immutable'
                USING ERRCODE = '23514';
        END IF;
        IF OLD.trial_state <> 'PENDING' AND ROW(NEW.trial_state, NEW.trial_reason, NEW.processed_at)
            IS DISTINCT FROM ROW(OLD.trial_state, OLD.trial_reason, OLD.processed_at) THEN
            RAISE EXCEPTION 'Trial evidence processing result is immutable' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

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
        SELECT sti.identity_type, sti.identity_value INTO identity_type, identity_value
          FROM subscription_test_identities AS sti
         WHERE sti.id = NEW.test_identity_id AND sti.client_id = NEW.client_id AND sti.is_active;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Test identity must belong to the active client' USING ERRCODE = '23514';
        END IF;
        IF identity_value <> NEW.recipient_identity
           AND NOT (NEW.channel = 'whatsapp_qr'
                    AND NEW.recipient_identity ~ '^[1-9][0-9]*@lid$'
                    AND identity_type = 'whatsapp_phone') THEN
            RAISE EXCEPTION 'Test identity does not match the trusted recipient' USING ERRCODE = '23514';
        END IF;
    END IF;
    SELECT sti.id INTO identity_match FROM subscription_test_identities AS sti
      WHERE sti.client_id = NEW.client_id AND sti.identity_value = NEW.recipient_identity AND sti.is_active;
    IF FOUND AND (NEW.environment <> 'test' OR (NEW.test_identity_id IS DISTINCT FROM identity_match)) THEN
        RAISE EXCEPTION 'Registered test recipient requires test classification and identity' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
    """)


def downgrade():
    raise RuntimeError("Cannot downgrade 0025 while activation evidence history exists")
