"""Make payment-operation deduplication independent of provider method."""

from alembic import op
import sqlalchemy as sa

from migrations.bridge_helpers import (
    create_unique_constraint_if_missing,
    has_constraint,
    has_unique_columns,
)


revision = "0034_payment_identity"
down_revision = "0033_payment_mailboxes"
branch_labels = None
depends_on = None


def _normalize_historical_operation_ids():
    """Keep all historical rows while making operation identity unambiguous.

    ``external_operation_id`` was previously unique only together with the
    payment method.  Older parser output can therefore contain the same
    operation for several methods, as well as the known false-positive value
    ``Datos``.  Keep one deterministic representative for each real operation
    and archive the original value on rows whose identity must be cleared.
    PostgreSQL permits multiple NULLs in the new unique constraint, so no
    notification row needs to be deleted.
    """
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY client_id, btrim(external_operation_id)
                        ORDER BY
                            (matched_order_id IS NOT NULL) DESC,
                            (status = 'matched') DESC,
                            (processed_at IS NOT NULL) DESC,
                            received_at ASC NULLS LAST,
                            occurred_at ASC NULLS LAST,
                            id ASC
                    ) AS operation_rank
                FROM payment_notifications
                WHERE external_operation_id IS NOT NULL
                  AND btrim(external_operation_id) <> ''
                  AND lower(btrim(external_operation_id)) <> 'datos'
            ),
            rows_to_clear AS (
                SELECT
                    notification.id,
                    notification.external_operation_id,
                    'duplicate_external_operation_id' AS reason
                FROM payment_notifications AS notification
                JOIN ranked
                  ON ranked.id = notification.id
                WHERE ranked.operation_rank > 1

                UNION ALL

                SELECT
                    notification.id,
                    notification.external_operation_id,
                    CASE
                        WHEN lower(btrim(notification.external_operation_id)) = 'datos'
                            THEN 'known_invalid_external_operation_id'
                        ELSE 'blank_external_operation_id'
                    END AS reason
                FROM payment_notifications AS notification
                WHERE notification.external_operation_id IS NOT NULL
                  AND (
                      btrim(notification.external_operation_id) = ''
                      OR lower(btrim(notification.external_operation_id)) = 'datos'
                  )
            )
            UPDATE payment_notifications AS notification
            SET
                external_operation_id = NULL,
                metadata = CAST(
                    CASE
                        WHEN jsonb_typeof(COALESCE(CAST(notification.metadata AS jsonb), CAST('{}' AS jsonb))) = 'object'
                            THEN COALESCE(CAST(notification.metadata AS jsonb), CAST('{}' AS jsonb))
                                || jsonb_build_object(
                                    'migration_0034_original_external_operation_id',
                                    rows_to_clear.external_operation_id,
                                    'migration_0034_external_operation_id_reason',
                                    rows_to_clear.reason
                                )
                        ELSE jsonb_build_object(
                            'migration_0034_original_metadata', CAST(notification.metadata AS jsonb),
                            'migration_0034_original_external_operation_id', rows_to_clear.external_operation_id,
                            'migration_0034_external_operation_id_reason', rows_to_clear.reason
                        )
                    END AS json
                )
            FROM rows_to_clear
            WHERE notification.id = rows_to_clear.id;
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    external_operation_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY client_id, btrim(external_operation_id)
                        ORDER BY
                            (matched_order_id IS NOT NULL) DESC,
                            (status = 'matched') DESC,
                            (processed_at IS NOT NULL) DESC,
                            received_at ASC NULLS LAST,
                            occurred_at ASC NULLS LAST,
                            id ASC
                    ) AS operation_rank
                FROM payment_notifications
                WHERE external_operation_id IS NOT NULL
                  AND btrim(external_operation_id) <> ''
                  AND lower(btrim(external_operation_id)) <> 'datos'
            )
            UPDATE payment_notifications AS notification
            SET
                external_operation_id = btrim(ranked.external_operation_id),
                metadata = CAST(
                    CASE
                        WHEN jsonb_typeof(COALESCE(CAST(notification.metadata AS jsonb), CAST('{}' AS jsonb))) = 'object'
                            THEN COALESCE(CAST(notification.metadata AS jsonb), CAST('{}' AS jsonb))
                                || jsonb_build_object(
                                    'migration_0034_original_external_operation_id', ranked.external_operation_id,
                                    'migration_0034_external_operation_id_reason', 'trimmed_external_operation_id'
                                )
                        ELSE jsonb_build_object(
                            'migration_0034_original_metadata', CAST(notification.metadata AS jsonb),
                            'migration_0034_original_external_operation_id', ranked.external_operation_id,
                            'migration_0034_external_operation_id_reason', 'trimmed_external_operation_id'
                        )
                    END AS json
                )
            FROM ranked
            WHERE notification.id = ranked.id
              AND ranked.operation_rank = 1
              AND btrim(ranked.external_operation_id) <> ranked.external_operation_id;
            """
        )
    )


def upgrade():
    already_current = has_constraint(
        "payment_notifications",
        "uq_payment_notifications_client_operation",
        "unique",
    ) or has_unique_columns("payment_notifications", ["client_id", "external_operation_id"])
    if already_current:
        return

    _normalize_historical_operation_ids()
    # A provider can be resolved differently as parsers evolve, but one
    # operation must remain unique inside the receiving client's mailbox.
    if has_constraint(
        "payment_notifications",
        "uq_payment_notifications_receiver_operation",
        "unique",
    ):
        op.drop_constraint(
            "uq_payment_notifications_receiver_operation",
            "payment_notifications",
            type_="unique",
        )
    create_unique_constraint_if_missing(
        "uq_payment_notifications_client_operation",
        "payment_notifications",
        ["client_id", "external_operation_id"],
    )


def downgrade():
    op.drop_constraint(
        "uq_payment_notifications_client_operation",
        "payment_notifications",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_payment_notifications_receiver_operation",
        "payment_notifications",
        ["client_id", "payment_method_id", "external_operation_id"],
    )
