"""No database: names (T1), inference (T2), readers (T3, T10, T12), manifest (T9)."""

from __future__ import annotations

import datetime as dt
import sys

import pytest
from openpyxl import Workbook

from dataload.infer import infer, to_copy
from dataload.load import LoadError, plan
from dataload.manifest import ManifestError, Owner, parse_manifest, parse_owner
from dataload.names import column_names, header_text, normalise


# --- T1 ------------------------------------------------------------------------------------------


def test_t1_names_are_snake_case_with_digits_prefixed_and_empties_numbered():
    assert column_names(["Locality", 2015, None, " ", "24-25", "PA", "Locality", "locality", "Prelim (%)"]) == [
        "locality", "c_2015", "column_3", "column_4", "c_24_25", "pa", "locality_2", "locality_3", "prelim"]
    assert normalise("Café Name!") == "caf_name"


def test_t1_names_are_capped_at_63_bytes_even_with_a_suffix():
    long = "x" * 80
    a, b = column_names([long, long])
    assert len(a) == 63 and len(b) == 63 and b.endswith("_2") and a != b


def test_t1_the_original_header_is_kept_for_the_comment():
    assert header_text(" 24-25 ") == "24-25" and header_text(2015) == "2015"
    assert header_text(None) is None and header_text("  ") is None


# --- T2 ------------------------------------------------------------------------------------------

NULLS = frozenset({"", "-", "NA"})


@pytest.mark.parametrize(("values", "expected"), [
    (["true", "No", "YES"], "boolean"),
    (["1", "-3", "+42"], "bigint"),
    (["1", "2.5", "1e3"], "numeric"),
    (["2024-01-31", "2023-12-01"], "date"),
    (["2024-01-31", "2024-01-31 10:00:00"], "timestamp"),
    (["1", "two"], "text"),
    (["1", "2024-01-01"], "text"),
    (["-", "NA", ""], "text"),  # all null
    (["5", "-", "NA", "7"], "bigint"),  # nulls are skipped
    ([str(2**63)], "numeric"),
    ([True, False], "boolean"),
    ([3, 4.5], "numeric"),
    ([dt.datetime(2024, 1, 1)], "date"),
    ([dt.datetime(2024, 1, 1, 9, 30)], "timestamp"),
])
def test_t2_the_first_type_that_fits_every_value(values, expected):
    assert infer([[v] for v in values], 1, NULLS) == [expected]


def test_t2_thousands_separators_only_when_asked():
    assert infer([["1,234"], ["5"]], 1, NULLS) == ["text"]
    assert infer([["1,234"], ["5"]], 1, NULLS, ",") == ["bigint"]
    assert to_copy("1,234", "bigint", NULLS, ",") == "1234"


def test_t2_copy_text_turns_nulls_into_none():
    assert to_copy("NA", "numeric", NULLS, None) is None
    assert to_copy(dt.datetime(2024, 1, 2), "date", NULLS, None) == "2024-01-02"
    assert to_copy(True, "boolean", NULLS, None) == "true"


# --- manifest, T9 --------------------------------------------------------------------------------


def manifest(text: str, tmp_path):
    return parse_manifest(text, tmp_path)


def test_t9_owner_url_env_or_split_fields(tmp_path):
    env = {"U": "postgresql://a:b@h/d", "H": "db", "PW": "sekrit"}
    assert parse_owner({"url_env": "U"}).conninfo(env) == "postgresql://a:b@h/d"
    split = parse_owner({"host_env": "H", "port": 5435, "database": "vicprop", "user": "loader", "password_env": "PW"})
    info = psycopg_dict(split.conninfo(env))
    assert info == {"host": "db", "port": "5435", "dbname": "vicprop", "user": "loader", "password": "sekrit"}
    assert split.describe() == "H, PW"


def psycopg_dict(info):
    from psycopg.conninfo import conninfo_to_dict
    return conninfo_to_dict(info)


@pytest.mark.parametrize(("owner", "reason"), [
    ({"url_env": "U", "host": "h"}, "use url_env or split fields, not both"),
    ({"database": "d", "password": "hunter2"}, "a password never goes in the manifest"),
    ({"database": "postgresql://u:hunter2@h/d"}, "logins go in the environment"),
    ({"host": "h"}, "database (or database_env) is required"),
    ({"database": "d", "nope": 1}, "unknown key nope"),
    ({"database": "d", "host": "h", "host_env": "H"}, "host and host_env can't both be set"),
])
def test_t9_bad_owners_are_refused_without_quoting_secrets(owner, reason):
    with pytest.raises(ManifestError) as e:
        parse_owner(owner)
    assert reason in str(e.value) and "hunter2" not in str(e.value)


def test_t9_a_missing_variable_is_named_never_its_value():
    with pytest.raises(ManifestError, match="^PW is not set$"):
        parse_owner({"database": "d", "password_env": "PW"}).conninfo({})


@pytest.mark.parametrize(("text", "reason"), [
    ('nope = 1\n[[source]]\nfile = "a.csv"\n', "unknown key nope"),
    ("", "at least one [[source]]"),
    ('[[source]]\nfile = "a.csv"\nheader_row = 2\nheader_match = "x"\n', "header_row and header_match can't both be set"),
    ('[[source]]\nfile = "a.csv"\ntypes = { a = "money" }\n', "must be one of"),
    ('[[source]]\nfile = "a.csv"\ntable = "Bad Name"\n', "lower snake_case"),
    ('grant_schemas = ["mart"]\n[[source]]\nfile = "a.csv"\n', "grant_schemas needs readonly_role"),
    ('[[source]]\nfile = "postgres://x"\n', "logins go in the environment"),
])
def test_manifest_refusals(tmp_path, text, reason):
    with pytest.raises(ManifestError, match=reason.replace("[", r"\[").replace("]", r"\]")):
        manifest(text, tmp_path)


# --- readers, T3, T10, T12 -----------------------------------------------------------------------


def test_t3_csv_sniffs_the_delimiter_strips_a_bom_and_infers(tmp_path):
    (tmp_path / "a.csv").write_bytes("﻿Name;Count;When\nx;1;2024-01-01\n;;\ny; 2 ;2024-02-01\n".encode())
    [p] = plan(manifest('[[source]]\nfile = "a.csv"\n', tmp_path))
    assert p.name == "a" and [(c.name, c.type) for c in p.columns] == [("name", "text"), ("count", "bigint"), ("when", "date")]
    assert list(p.table.rows()) == [["x", "1", "2024-01-01"], ["y", "2", "2024-02-01"]]  # trimmed; blank row skipped


def test_t3_a_row_wider_than_the_header_is_refused(tmp_path):
    (tmp_path / "a.csv").write_text("a,b\n1,2,3\n")
    with pytest.raises(LoadError, match="row 2 has more cells than the header"):
        plan(manifest('[[source]]\nfile = "a.csv"\n', tmp_path))


def workbook(path, sheets: dict[str, list[list]]):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(path)


def test_t3_excel_header_row_and_every_sheet(tmp_path):
    workbook(tmp_path / "w.xlsx", {"First Sheet": [["title"], ["id", "v"], [1, 2.5]], "Two": [["note"], ["id"], [3]]})
    [p] = plan(manifest('[[source]]\nfile = "w.xlsx"\nheader_row = 2\n', tmp_path))
    assert p.name == "w" and [(c.name, c.type) for c in p.columns] == [("id", "bigint"), ("v", "numeric")]
    both = plan(manifest('[[source]]\nfile = "w.xlsx"\nsheet = "*"\nheader_row = 2\n', tmp_path))
    assert [b.name for b in both] == ["first_sheet", "two"]
    assert both[0].table.label == "w.xlsx, sheet First Sheet"


def test_t12_header_match_finds_a_header_that_moved(tmp_path):
    for i, lead in enumerate((0, 3)):
        workbook(tmp_path / f"w{i}.xlsx", {"S": [[None]] * lead + [["Locality", 2015], ["ABBOTSFORD", "925000"]]})
        [p] = plan(manifest(f'[[source]]\nfile = "w{i}.xlsx"\nheader_match = " locality "\n', tmp_path))
        assert [c.name for c in p.columns] == ["locality", "c_2015"]
        assert list(p.table.rows()) == [["ABBOTSFORD", "925000"]]


def test_t12_no_match_names_what_it_looked_for(tmp_path):
    workbook(tmp_path / "w.xlsx", {"S": [["Suburb"], ["x"]]})
    with pytest.raises(LoadError, match="no row starts with 'Locality'"):
        plan(manifest('[[source]]\nfile = "w.xlsx"\nheader_match = "Locality"\n', tmp_path))


def test_t3_parquet_keeps_its_schema_and_refuses_unmapped_types(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.table({"Id": pa.array([1, 2], pa.int32()), "Price": [1.5, None], "Day": [dt.date(2024, 1, 1)] * 2,
                             "At": pa.array([dt.datetime(2024, 1, 1)] * 2, pa.timestamp("us", tz="UTC"))}), tmp_path / "p.parquet")
    [p] = plan(manifest('[[source]]\nfile = "p.parquet"\n', tmp_path))
    assert [(c.name, c.type) for c in p.columns] == [
        ("id", "bigint"), ("price", "double precision"), ("day", "date"), ("at", "timestamptz")]
    pq.write_table(pa.table({"tags": [[1, 2]]}), tmp_path / "bad.parquet")
    with pytest.raises(LoadError, match="column 'tags' has type list<.*which dataload can't load"):
        plan(manifest('[[source]]\nfile = "bad.parquet"\n', tmp_path))


def test_t10_parquet_without_the_extra_says_how_to_install_it(tmp_path, monkeypatch):
    (tmp_path / "p.parquet").write_bytes(b"PAR1")
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    with pytest.raises(LoadError, match=r"install dataload\[parquet\]"):
        plan(manifest('[[source]]\nfile = "p.parquet"\n', tmp_path))
    (tmp_path / "a.csv").write_text("a\n1\n")
    assert plan(manifest('[[source]]\nfile = "a.csv"\n', tmp_path))[0].columns[0].type == "bigint"


def test_types_override_and_unknown_columns(tmp_path):
    (tmp_path / "a.csv").write_text("zip,n\n3000,1\n")
    [p] = plan(manifest('[[source]]\nfile = "a.csv"\ntypes = { zip = "text" }\n', tmp_path))
    assert [c.type for c in p.columns] == ["text", "bigint"]
    with pytest.raises(LoadError, match="types names no column nope"):
        plan(manifest('[[source]]\nfile = "a.csv"\ntypes = { nope = "text" }\n', tmp_path))


# --- refuter regressions ------------------------------------------------------------------------


@pytest.mark.parametrize(("values", "expected"), [
    (["0800", "2000"], "text"),  # postcodes: a leading zero would be lost as a number
    (["007"], "text"),
    (["0", "-0", "0.5", "-0.25", "10"], "numeric"),  # a lone zero or 0.x is still a number
    (["0", "12"], "bigint"),
    (["\u0663"], "text"),  # Arabic-Indic three: not an ASCII digit
    (["2024-01-01 10:00+10"], "text"),  # an offset would be dropped by a timestamp column
    (["1" * 5000], "text"),  # too long for int(): not a crash
])
def test_t2_values_that_must_stay_text(values, expected):
    assert infer([[v] for v in values], 1, NULLS) == [expected]


def test_file_problems_are_load_errors_naming_the_file(tmp_path):
    (tmp_path / "a.csv").write_bytes(b"a\n" + b"x\n" * 40000 + b"\xff\xfe\n")  # bad UTF-8 after the sniff
    with pytest.raises(LoadError, match="a.csv"):
        plan(manifest('[[source]]\nfile = "a.csv"\n', tmp_path))
    (tmp_path / "b.csv").write_text("a\n1\n")
    with pytest.raises(LoadError, match="b.csv"):
        plan(manifest('[[source]]\nfile = "b.csv"\nencoding = "no-such-codec"\n', tmp_path))


def test_a_table_name_for_a_source_with_many_tables_is_refused(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    (tmp_path / "b.csv").write_text("x\n1\n")
    with pytest.raises(LoadError, match="table applies to one file or sheet"):
        plan(manifest('[[source]]\nfile = "*.csv"\ntable = "both"\n', tmp_path))


def test_types_are_checked_on_the_library_path_too(tmp_path):
    import dataclasses
    (tmp_path / "a.csv").write_text("x\n1\n")
    m = manifest('[[source]]\nfile = "a.csv"\n', tmp_path)
    bad = dataclasses.replace(m, sources=(dataclasses.replace(m.sources[0], types={"x": "int); DROP TABLE y; --"}),))
    with pytest.raises(LoadError, match="must be one of"):
        plan(bad)
