import ast
from pathlib import Path

from alembic.script import ScriptDirectory


API_ROOT = Path(__file__).parents[1]
VERSIONS = API_ROOT / "migrations" / "versions"


def script_directory() -> ScriptDirectory:
    return ScriptDirectory(str(API_ROOT / "migrations"))


def test_production_revision_is_a_known_parent_of_head():
    scripts = script_directory()
    assert scripts.get_heads() == ["0043_google_calendar"]
    assert scripts.get_revision("0031_restaurant_staff").down_revision == "0030_user_email_verification"
    assert scripts.get_revision("0031_waiter_item_cancellation").down_revision == "0031_restaurant_staff"

    revision = scripts.get_revision("0043_google_calendar")
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


def test_payment_identity_preserves_original_values_when_normalizing():
    source = (VERSIONS / "0034_payment_notification_identity.py").read_text()

    assert "migration_0034_original_external_operation_id" in source
    assert "migration_0034_external_operation_id_reason" in source
    assert "trimmed_external_operation_id" in source
    assert "already_current" in source
