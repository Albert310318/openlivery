import ast
from pathlib import Path

from alembic.script import ScriptDirectory


API_ROOT = Path(__file__).parents[1]
VERSIONS = API_ROOT / "migrations" / "versions"


def script_directory() -> ScriptDirectory:
    return ScriptDirectory(str(API_ROOT / "migrations"))


def test_production_revision_is_a_known_parent_of_head():
    scripts = script_directory()
    assert scripts.get_heads() == ["0044_user_schema_bridge"]
    assert scripts.get_revision("0031_restaurant_staff").down_revision == "0030_user_email_verification"
    assert scripts.get_revision("0031_waiter_item_cancellation").down_revision == "0031_restaurant_staff"

    revision = scripts.get_revision("0044_user_schema_bridge")
    seen = set()
    while revision is not None:
        assert revision.revision not in seen
        seen.add(revision.revision)
        revision = scripts.get_revision(revision.down_revision) if revision.down_revision else None

    assert "0031_restaurant_staff" in seen
    assert "0031_waiter_item_cancellation" in seen


def _upgrade_tree(path: Path) -> ast.AST:
    tree = ast.parse(path.read_text())
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )


def test_bridge_upgrades_do_not_delete_schema_or_rows():
    migration_names = (
        "0031_restaurant_staff.py",
        "0031_waiter_item_cancellation.py",
        "0032_payment_notifications.py",
        "0033_payment_mailboxes.py",
        "0034_payment_notification_identity.py",
        "0035_order_reported_payment_amount.py",
        "0036_manual_payment_review.py",
        "0037_delivery_whatsapp.py",
        "0038_payment_confirmed_by.py",
        "0039_restaurant_welcome_flyer.py",
        "0040_message_media.py",
        "0041_delivery_notifications.py",
        "0042_lead_advisor_notification.py",
        "0043_google_calendar.py",
        "0044_user_schema_bridge.py",
    )
    forbidden = {"DropTable", "DropColumn", "Delete", "TruncateTable"}

    for name in migration_names:
        upgrade = _upgrade_tree(VERSIONS / name)
        assert not {
            node.func.id
            for node in ast.walk(upgrade)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
            and node.func.attr in {"drop_table", "drop_column"}
        }
        assert not any(isinstance(node, ast.Delete) for node in ast.walk(upgrade))
        assert forbidden.isdisjoint(
            {
                node.__class__.__name__
                for node in ast.walk(upgrade)
            }
        )


def test_preexisting_calendar_and_advisor_objects_are_checked_conditionally():
    calendar_source = (VERSIONS / "0043_google_calendar.py").read_text()
    advisor_source = (VERSIONS / "0042_lead_advisor_notification.py").read_text()

    assert "add_column_if_missing" in calendar_source
    assert "table_exists(\"calendar_appointments\")" in calendar_source
    assert "require_columns" in calendar_source
    assert advisor_source.count("add_column_if_missing") >= 3


def test_payment_notifications_repairs_missing_0027_parent_table():
    source = (VERSIONS / "0032_payment_notifications.py").read_text()

    assert "def _ensure_payment_methods_table" in source
    assert 'table_exists("restaurant_payment_methods")' in source
    assert 'op.create_table(\n            "restaurant_payment_methods"' in source
    assert "PAYMENT_METHOD_COLUMNS" in source
    assert source.index("_ensure_payment_methods_table()") < source.index(
        'add_column_if_missing(\n        "restaurant_payment_methods"'
    )


def test_bridge_helpers_do_not_issue_add_column_against_missing_table():
    source = (API_ROOT / "migrations" / "bridge_helpers.py").read_text()

    assert "if not table_exists(table_name):" in source
    assert "return set()" in source
    assert "cannot add column" in source


def test_payment_identity_preserves_original_values_when_normalizing():
    source = (VERSIONS / "0034_payment_notification_identity.py").read_text()

    assert "migration_0034_original_external_operation_id" in source
    assert "migration_0034_external_operation_id_reason" in source
    assert "trimmed_external_operation_id" in source
    assert "already_current" in source


def test_historical_restaurant_bridge_covers_all_0027_and_0029_objects():
    source = (API_ROOT / "migrations" / "restaurant_schema_bridge.py").read_text()

    for table_name in (
        "restaurant_profiles",
        "restaurant_menu_categories",
        "restaurant_menu_products",
        "restaurant_menu_product_variants",
        "restaurant_menu_product_extras",
        "restaurant_modalities",
        "restaurant_delivery_zones",
        "restaurant_staff",
        "restaurant_table_accounts",
    ):
        assert f'"{table_name}"' in source
    assert "_ensure_user_phone" in source
    assert "add_column_if_missing" in source
    assert "create_foreign_key_if_missing" in source
    assert "create_unique_constraint_if_missing" in source
    assert "manager_cashier" in source
    assert "op.drop_constraint(\"ck_restaurant_staff_role\"" in source


def test_delivery_whatsapp_repairs_modalities_before_adding_column():
    source = (VERSIONS / "0037_delivery_whatsapp.py").read_text()
    assert source.index("ensure_restaurant_modalities_table()") < source.index(
        'add_column_if_missing(\n        "restaurant_modalities"'
    )


def test_user_schema_bridge_repairs_all_additive_user_columns():
    source = (VERSIONS / "0044_user_schema_bridge.py").read_text()

    for column_name in (
        "is_vendiq_admin",
        "password_recovery_code_hash",
        "password_recovery_expires_at",
        "password_recovery_last_sent_at",
        "password_recovery_send_window_started_at",
        "password_recovery_attempts",
        "password_recovery_send_count",
        "password_recovery_credentials_version",
        "phone",
        "email_verification_pending",
    ):
        assert f'"{column_name}"' in source
    assert 'create_index_if_missing("ix_users_phone"' in source
    assert "require_columns" in source
