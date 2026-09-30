"""Small, online-only helpers for the production-schema migration bridge.

The bridge may encounter objects that were created by the production line
before Alembic reached the corresponding revision.  These helpers make the
DDL conditional without treating an incompatible object as migrated.
"""

from collections.abc import Iterable

from alembic import op
from sqlalchemy import inspect


def _inspector():
    return inspect(op.get_bind())


def table_exists(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def column_names(table_name: str) -> set[str]:
    if not table_exists(table_name):
        return set()
    return {column["name"] for column in _inspector().get_columns(table_name)}


def has_column(table_name: str, column_name: str) -> bool:
    return column_name in column_names(table_name)


def column_is_nullable(table_name: str, column_name: str) -> bool:
    for column in _inspector().get_columns(table_name):
        if column["name"] == column_name:
            return bool(column["nullable"])
    raise RuntimeError(f"{table_name}.{column_name} does not exist")


def require_columns(table_name: str, required: Iterable[str]) -> None:
    missing = sorted(set(required) - column_names(table_name))
    if missing:
        raise RuntimeError(
            f"{table_name} exists but is incompatible with the migration bridge; "
            f"missing columns: {', '.join(missing)}"
        )


def index_names(table_name: str) -> set[str]:
    return {index["name"] for index in _inspector().get_indexes(table_name)}


def has_index(table_name: str, index_name: str) -> bool:
    return index_name in index_names(table_name)


def constraint_names(table_name: str, kind: str) -> set[str]:
    inspector = _inspector()
    if kind == "foreignkey":
        rows = inspector.get_foreign_keys(table_name)
    elif kind == "unique":
        rows = inspector.get_unique_constraints(table_name)
    elif kind == "check":
        rows = inspector.get_check_constraints(table_name)
    else:
        raise ValueError(f"Unsupported constraint kind: {kind}")
    return {row["name"] for row in rows if row.get("name")}


def has_constraint(table_name: str, constraint_name: str, kind: str) -> bool:
    return constraint_name in constraint_names(table_name, kind)


def check_constraint_sql(table_name: str, constraint_name: str) -> str | None:
    for constraint in _inspector().get_check_constraints(table_name):
        if constraint.get("name") == constraint_name:
            return constraint.get("sqltext")
    return None


def unique_constraint_columns(table_name: str) -> set[tuple[str, ...]]:
    return {
        tuple(row["column_names"])
        for row in _inspector().get_unique_constraints(table_name)
        if row.get("column_names")
    }


def has_unique_columns(table_name: str, columns: Iterable[str]) -> bool:
    return tuple(columns) in unique_constraint_columns(table_name)


def require_unique_columns(table_name: str, columns: Iterable[str]) -> None:
    if not has_unique_columns(table_name, columns):
        joined = ", ".join(columns)
        raise RuntimeError(f"{table_name} is missing a unique constraint on ({joined})")


def has_foreign_key(
    table_name: str,
    local_columns: Iterable[str],
    remote_table: str,
    remote_columns: Iterable[str],
) -> bool:
    local = tuple(local_columns)
    remote = tuple(remote_columns)
    return any(
        tuple(foreign_key.get("constrained_columns") or ()) == local
        and foreign_key.get("referred_table") == remote_table
        and tuple(foreign_key.get("referred_columns") or ()) == remote
        for foreign_key in _inspector().get_foreign_keys(table_name)
    )


def add_column_if_missing(table_name: str, column) -> bool:
    if not table_exists(table_name):
        raise RuntimeError(
            f"{table_name} does not exist; cannot add column {column.name}"
        )
    if has_column(table_name, column.name):
        return False
    op.add_column(table_name, column)
    return True


def create_index_if_missing(index_name: str, table_name: str, columns: list[str], **kwargs) -> bool:
    if has_index(table_name, index_name):
        return False
    op.create_index(index_name, table_name, columns, **kwargs)
    return True


def create_foreign_key_if_missing(
    constraint_name: str,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
    **kwargs,
) -> bool:
    if has_constraint(source_table, constraint_name, "foreignkey") or has_foreign_key(
        source_table,
        local_cols,
        referent_table,
        remote_cols,
    ):
        return False
    op.create_foreign_key(
        constraint_name,
        source_table,
        referent_table,
        local_cols,
        remote_cols,
        **kwargs,
    )
    return True


def create_unique_constraint_if_missing(
    constraint_name: str,
    table_name: str,
    columns: list[str],
) -> bool:
    if has_constraint(table_name, constraint_name, "unique") or has_unique_columns(table_name, columns):
        return False
    op.create_unique_constraint(constraint_name, table_name, columns)
    return True


def create_check_constraint_if_missing(constraint_name: str, table_name: str, condition: str) -> bool:
    if has_constraint(table_name, constraint_name, "check"):
        return False
    op.create_check_constraint(constraint_name, table_name, condition)
    return True
