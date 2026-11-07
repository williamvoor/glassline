"""`dataload grant`: read-only access for one role on some schemas (SPEC "Grants", L7, T6).

It grants CONNECT, USAGE, SELECT and default SELECT on future tables, and nothing else. It refuses
a role that is a superuser or can create roles. The SQL is shown without the password and runs
only when asked.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg import sql

HIDDEN = "'********'"


class GrantError(ValueError):
    """A grant that dataload refuses. The message never holds a password."""


@dataclass(frozen=True)
class Statement:
    query: sql.Composed
    shown: str  # what is printed: the password replaced


def plan(conn: psycopg.Connection, role: str, schemas: list[str], *, create_role: bool = False,
         password: str | None = None) -> list[Statement]:
    """The statements to run, checked against the database as it is now. Changes nothing."""
    if not schemas:
        raise GrantError("name at least one schema")
    row = conn.execute("SELECT rolsuper, rolcreaterole FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    me = conn.execute("SELECT current_user, current_database()").fetchone()
    assert me is not None
    owner, database = me
    if role == owner:
        raise GrantError(f"{role} is the login running this; grant to a separate read-only role")
    out: list[Statement] = []

    def add(query: sql.Composed, shown: sql.Composed | None = None) -> None:
        out.append(Statement(query, (shown or query).as_string(conn)))

    if row is None:
        if not create_role:
            raise GrantError(f"role {role} doesn't exist; pass --create-role (with --password-env) to create it")
        if not password:
            raise GrantError("--create-role needs --password-env naming a set variable")
        create = sql.SQL("CREATE ROLE {} LOGIN PASSWORD ").format(sql.Identifier(role))
        add(create + sql.Literal(password), create + sql.SQL(HIDDEN))
        add(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(role)))
    else:
        if create_role:
            raise GrantError(f"role {role} already exists; drop --create-role")
        if row[0] or row[1]:
            raise GrantError(f"{role} is a superuser or can create roles; the explorer needs a plain read-only role")
    add(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(role)))
    for s in schemas:
        exists = conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (s,)).fetchone()
        if exists is None:
            raise GrantError(f"schema {s} doesn't exist")
        ident = sql.Identifier(s)
        add(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ident, sql.Identifier(role)))
        add(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(ident, sql.Identifier(role)))
        add(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(ident, sql.Identifier(role)))
    return out


def apply(conn: psycopg.Connection, statements: list[Statement]) -> None:
    with conn.transaction():
        for s in statements:
            conn.execute(s.query)
