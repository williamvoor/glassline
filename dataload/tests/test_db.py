"""Against Postgres 18: loading (T3, T4, T5, T11), grant (T6), owner logins (T9), no secrets out (T8)."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from dataload.cli import main
from dataload.load import LoadError, run
from dataload.manifest import parse_manifest

from conftest import OWNER_PW, RO_PW

pytestmark = pytest.mark.db


def write(tmp_path, csv="Name,Count\nalpha,1\nbeta,2\n", transform="CREATE SCHEMA IF NOT EXISTS mart;\n"
          "DROP TABLE IF EXISTS mart.counts;\nCREATE TABLE mart.counts AS SELECT name, count FROM staging.things;\n"):
    (tmp_path / "things.csv").write_text(csv)
    (tmp_path / "t").mkdir(exist_ok=True)
    (tmp_path / "t" / "10_mart.sql").write_text(transform)
    (tmp_path / "load.toml").write_text(
        'readonly_role = "explorer_ro"\ngrant_schemas = ["mart"]\ntransforms = ["t/*.sql"]\n'
        '[owner]\nurl_env = "OWNER_URL"\n[[source]]\nfile = "things.csv"\n')
    return parse_manifest((tmp_path / "load.toml").read_text(), tmp_path)


def rows(dsn, q):
    with psycopg.connect(dsn) as c:
        return c.execute(q).fetchall()


def test_t3_t5_a_load_creates_comments_transforms_and_grants(clean, tmp_path):
    pg = clean
    m = write(tmp_path)
    with psycopg.connect(pg.owner, autocommit=True) as conn:
        [r] = run(conn, m)
    assert (r.schema, r.table, r.rows) == ("staging", "things", 2)
    assert rows(pg.owner, "SELECT name, count FROM staging.things ORDER BY 1") == [("alpha", 1), ("beta", 2)]
    assert rows(pg.owner, "SELECT obj_description('staging.things'::regclass)") == [("Loaded from things.csv by dataload",)]
    assert rows(pg.owner, "SELECT col_description('staging.things'::regclass, 1)") == [("Name",)]
    # T5: the read-only role reads mart, can't write, can't see staging.
    assert rows(pg.ro, "SELECT count(*) FROM mart.counts") == [(2,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rows(pg.ro, "SELECT * FROM staging.things")
    with psycopg.connect(pg.ro) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("INSERT INTO mart.counts VALUES ('x', 1)")


def test_t4_a_failing_transform_rolls_back_everything(clean, tmp_path):
    pg = clean
    m = write(tmp_path)
    with psycopg.connect(pg.owner, autocommit=True) as conn:
        run(conn, m)
    m2 = write(tmp_path, csv="Name,Count\ngamma,3\n", transform="SELECT * FROM nowhere;\n")
    with psycopg.connect(pg.owner, autocommit=True) as conn, pytest.raises(LoadError) as e:
        run(conn, m2)
    assert "transform 10_mart.sql" in str(e.value) and "nothing was changed" in str(e.value)
    assert rows(pg.owner, "SELECT name FROM staging.things ORDER BY 1") == [("alpha",), ("beta",)]
    assert rows(pg.ro, "SELECT count(*) FROM mart.counts") == [(2,)]


def test_t11_staging_survives_and_the_next_load_replaces_it(clean, tmp_path):
    pg = clean
    with psycopg.connect(pg.owner, autocommit=True) as conn:
        run(conn, write(tmp_path))
        run(conn, write(tmp_path, csv="Name,Count,Extra\nzeta,9,x\n"))
    assert rows(pg.owner, "SELECT * FROM staging.things") == [("zeta", 9, "x")]


def test_t3_a_bad_value_for_an_overridden_type_fails_without_change(clean, tmp_path):
    pg = clean
    (tmp_path / "a.csv").write_text("n\nabc\n")
    m = parse_manifest('[[source]]\nfile = "a.csv"\ntypes = { n = "bigint" }\n', tmp_path)
    with psycopg.connect(pg.owner, autocommit=True) as conn, pytest.raises(LoadError, match="loading a.csv"):
        run(conn, m)
    assert rows(pg.owner, "SELECT count(*) FROM pg_namespace WHERE nspname = 'staging'") == [(0,)]


# --- CLI: T6, T8, T9 -----------------------------------------------------------------------------


def cli(capsys, env, *argv):
    code = main(list(argv), env)
    out, err = capsys.readouterr()
    for secret in (OWNER_PW, RO_PW, "postgres:postgres"):
        assert secret not in out + err  # T8
    return code, out, err


def test_t9_split_owner_fields_connect_and_load(clean, tmp_path, capsys):
    pg = clean
    write(tmp_path)
    info = conninfo_to_dict(pg.owner)
    (tmp_path / "load.toml").write_text(
        f'[owner]\nhost = "{info["host"]}"\nport_env = "P"\ndatabase = "data"\nuser = "loader"\npassword_env = "PW"\n'
        '[[source]]\nfile = "things.csv"\n')
    code, out, err = cli(capsys, {"P": str(info["port"]), "PW": OWNER_PW}, "load", str(tmp_path / "load.toml"))
    assert code == 0, err
    assert "staging.things: 2 rows from things.csv" in out


def test_t8_a_wrong_password_names_variables_only(clean, tmp_path, capsys):
    write(tmp_path)
    code, _, err = cli(capsys, {"OWNER_URL": clean.owner.replace(OWNER_PW, "wrong-pw-xyz")}, "load", str(tmp_path / "load.toml"))
    assert code == 2 and "check OWNER_URL" in err and "wrong-pw-xyz" not in err


def test_file_command_loads_one_file(clean, tmp_path, capsys):
    (tmp_path / "Sales 2024.csv").write_text("a\n1\n")
    code, out, err = cli(capsys, {"OWNER_URL": clean.owner}, "file", str(tmp_path / "Sales 2024.csv"),
                         "--url-env", "OWNER_URL", "--schema", "raw")
    assert code == 0, err
    assert "raw.sales_2024: 1 rows" in out


def test_t6_grant_prints_then_applies_only_with_yes(clean, tmp_path, capsys):
    pg = clean
    with psycopg.connect(pg.owner, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA mart; CREATE TABLE mart.x (a int); INSERT INTO mart.x VALUES (1)")
    env = {"OWNER_URL": pg.owner}
    code, out, _ = cli(capsys, env, "grant", "--url-env", "OWNER_URL", "--role", "explorer_ro", "--schema", "mart")
    assert code == 0 and 'GRANT SELECT ON ALL TABLES IN SCHEMA "mart" TO "explorer_ro";' in out
    assert "Nothing was run" in out
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rows(pg.ro, "SELECT * FROM mart.x")
    code, out, _ = cli(capsys, env, "grant", "--url-env", "OWNER_URL", "--role", "explorer_ro", "--schema", "mart", "--yes")
    assert code == 0 and "Applied." in out
    assert rows(pg.ro, "SELECT * FROM mart.x") == [(1,)]
    with psycopg.connect(pg.owner, autocommit=True) as conn:
        conn.execute("CREATE TABLE mart.later (b int)")  # default privileges cover new tables
    assert rows(pg.ro, "SELECT * FROM mart.later") == []
    with psycopg.connect(pg.ro) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("INSERT INTO mart.x VALUES (2)")  # never a write right
    privs = rows(pg.superuser, "SELECT DISTINCT privilege_type FROM information_schema.role_table_grants "
                               "WHERE grantee = 'explorer_ro'")
    assert privs == [("SELECT",)]


def test_t6_grant_refuses_superusers_and_role_creators(clean, tmp_path, capsys):
    pg = clean
    with psycopg.connect(pg.superuser, autocommit=True) as conn:
        conn.execute("CREATE ROLE boss SUPERUSER")
        conn.execute("CREATE ROLE maker CREATEROLE")
    with psycopg.connect(pg.owner, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA mart")
    try:
        for role in ("boss", "maker"):
            code, _, err = cli(capsys, {"OWNER_URL": pg.owner}, "grant", "--url-env", "OWNER_URL", "--role", role,
                               "--schema", "mart", "--yes")
            assert code == 2 and "superuser or can create roles" in err
        code, _, err = cli(capsys, {"OWNER_URL": pg.owner}, "grant", "--url-env", "OWNER_URL", "--role", "nobody",
                           "--schema", "mart")
        assert code == 2 and "--create-role" in err
    finally:
        with psycopg.connect(pg.superuser, autocommit=True) as conn:
            conn.execute("DROP ROLE boss; DROP ROLE maker")


def test_t6_t8_create_role_hides_the_password(clean, capsys):
    pg = clean
    with psycopg.connect(pg.superuser, autocommit=True) as conn:
        conn.execute("ALTER ROLE loader CREATEROLE")
    with psycopg.connect(pg.owner, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA mart")
    try:
        code, out, err = cli(capsys, {"OWNER_URL": pg.owner, "NEW_PW": "new-role-pw-1"}, "grant", "--url-env", "OWNER_URL",
                             "--role", "reader", "--schema", "mart", "--create-role", "--password-env", "NEW_PW", "--yes")
        assert code == 0, err
        assert "new-role-pw-1" not in out and "'********'" in out
        assert rows(pg.superuser, "SELECT rolconfig FROM pg_roles WHERE rolname = 'reader'") == [
            (["default_transaction_read_only=on"],)]
    finally:
        with psycopg.connect(pg.superuser, autocommit=True) as conn:
            conn.execute("DROP OWNED BY reader; DROP ROLE reader; ALTER ROLE loader NOCREATEROLE")


# --- refuter regressions ------------------------------------------------------------------------


def test_t3_excel_and_parquet_rows_reach_postgres(clean, tmp_path):
    import datetime as dt
    from decimal import Decimal

    import pyarrow as pa
    import pyarrow.parquet as pq
    from openpyxl import Workbook

    pg = clean
    wb = Workbook()
    wb.active.title = "One"
    wb.active.append(["id", "when", "ok"])
    wb.active.append([1, dt.datetime(2024, 1, 2), True])
    two = wb.create_sheet("Two")
    two.append(["v"])
    two.append([2.5])
    wb.save(tmp_path / "w.xlsx")
    pq.write_table(pa.table({
        "n": pa.array([1, None], pa.uint64()), "d": pa.array([Decimal("1.25"), Decimal("2.50")], pa.decimal128(5, 2)),
        "f": [float("nan"), 1.5], "at": pa.array([dt.datetime(2024, 1, 1, 9)] * 2, pa.timestamp("us", tz="UTC")),
    }), tmp_path / "p.parquet")
    m = parse_manifest('[[source]]\nfile = "w.xlsx"\nsheet = "*"\n[[source]]\nfile = "p.parquet"\n', tmp_path)
    with psycopg.connect(pg.owner, autocommit=True) as conn:
        assert [(r.table, r.rows) for r in run(conn, m)] == [("one", 1), ("two", 1), ("p", 2)]
    assert rows(pg.owner, "SELECT id, \"when\", ok FROM staging.one") == [(1, dt.date(2024, 1, 2), True)]
    assert rows(pg.owner, "SELECT v FROM staging.two") == [(Decimal("2.5"),)]
    got = rows(pg.owner, "SELECT n, d, f::text, at FROM staging.p ORDER BY d")
    assert got == [(1, Decimal("1.25"), "NaN", dt.datetime(2024, 1, 1, 9, tzinfo=dt.UTC)),
                   (None, Decimal("2.50"), "1.5", dt.datetime(2024, 1, 1, 9, tzinfo=dt.UTC))]


def test_a_missing_transform_names_the_pattern(clean, tmp_path, capsys):
    write(tmp_path)
    (tmp_path / "load.toml").write_text('transforms = ["nope/*.sql"]\n[owner]\nurl_env = "OWNER_URL"\n'
                                        '[[source]]\nfile = "things.csv"\n')
    code, _, err = cli(capsys, {"OWNER_URL": clean.owner}, "load", str(tmp_path / "load.toml"))
    assert code == 2 and "nothing matches nope/*.sql" in err


def test_file_command_takes_names_with_glob_characters_and_checks_header_row(clean, tmp_path, capsys):
    (tmp_path / "bad[1].csv").write_text("a\n1\n")
    code, out, err = cli(capsys, {"OWNER_URL": clean.owner}, "file", str(tmp_path / "bad[1].csv"),
                         "--url-env", "OWNER_URL", "--table", "bad_1")
    assert code == 0, err
    code, _, err = cli(capsys, {"OWNER_URL": clean.owner}, "file", str(tmp_path / "bad[1].csv"),
                       "--url-env", "OWNER_URL", "--header-row", "0")
    assert code == 2 and "--header-row must be 1 or more" in err

