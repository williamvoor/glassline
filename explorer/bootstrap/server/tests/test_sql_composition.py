"""SQL composition at export, sample-key and min/max boundaries."""

from types import SimpleNamespace

import psycopg
import pytest
from pglast import parse_sql
from psycopg import sql

from explorer_agent.sql import SqlOk
from explorer_bootstrap.boards import sample_key
from explorer_bootstrap.export import render_comments_sql
from explorer_bootstrap.stats import column_ranges


async def test_range_and_sample_queries_preserve_identifier_names():
    schema, table, column = 'odd" schema', 'ta.ble', 'co"l'
    queries = []
    class Runner:
        async def run(self, query):
            statement = parse_sql(query)[0].stmt
            relation = statement.fromClause[0]
            assert (relation.schemaname, relation.relname) == (schema, table)
            queries.append(query)
            return SqlOk(["mn_0", "mx_0", "key"], ["integer"] * 3,
                         [{"mn_0": 1, "mx_0": 9, "key": 3}], False, 1)
    result = await column_ranges(Runner(), schema, table, [column])
    assert result.status == "ok" and result.values == {column: (1, 9)}
    key = await sample_key(SimpleNamespace(key_columns=[column]),
                           {"objects": [{"schema": schema, "name": table, "columns": [{"name": column}]}]},
                           SimpleNamespace(allowed_schemas=[schema]), Runner())
    assert key == {"value": 3, "source": f"{schema}.{table}.{column}"}
    for query in queries:
        assert sql.Identifier(column).as_string() in query


@pytest.mark.db
@pytest.mark.parametrize("standard_strings", ["on", "off"])
def test_export_backslashes_and_quoted_identifiers_execute(pg, standard_strings):
    schema, table, column = 'odd" schema', 'ta.ble', 'co"l'
    text = "Back\\slash 'quotes'\nCafé 😀"
    draft = {"report": {"objects": [{"schema": schema, "name": table, "kind": "table",
                                    "columns": [{"name": column}]}]},
             "comments": [{"object": f"{schema}.{table}", "column": "", "comment": text, "accepted": True},
                          {"object": f"{schema}.{table}", "column": column, "comment": text[::-1], "accepted": True}]}
    script = render_comments_sql(draft)
    body = script.removeprefix("BEGIN;\n").removesuffix("COMMIT;\n")
    with psycopg.connect(pg.superuser_dsn) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("CREATE TABLE {} ({} integer)").format(sql.Identifier(schema, table), sql.Identifier(column)))
        conn.execute(sql.SQL("SET LOCAL standard_conforming_strings = {}").format(sql.SQL(standard_strings)))
        conn.execute(body)
        oid = conn.execute("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                           "WHERE n.nspname=%s AND c.relname=%s", (schema, table)).fetchone()[0]
        assert conn.execute("SELECT obj_description(%s, 'pg_class'), col_description(%s, 1)", (oid, oid)).fetchone() == (text, text[::-1])
        conn.rollback()
