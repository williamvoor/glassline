"""`dataload load | file | grant` (SPEC; CLI only, L10). Exit 0 ok, 2 refused or failed.

Logins come only from the environment (and an optional `--env-file`, which never overrides it).
Nothing printed ever holds a login or a password (T8).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import psycopg

from dataload import grants
from dataload.load import LoadError, run
from dataload.manifest import ENV_NAME, Manifest, ManifestError, Owner, Source, load_manifest

CONNECT_TIMEOUT_S = 10


class Fatal(Exception):
    pass


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dataload", description="Load CSV, Excel and Parquet files into Postgres.")
    p.add_argument("--env-file", type=Path, help="KEY=VALUE file read before running; the environment wins")
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("load", help="run a load.toml manifest")
    m.add_argument("manifest", type=Path)

    f = sub.add_parser("file", help="load one file into a staging schema, no manifest")
    f.add_argument("path", type=Path)
    f.add_argument("--schema", default="staging")
    f.add_argument("--url-env", required=True, help="the variable holding the owner login")
    f.add_argument("--table", help="table name (default: the file stem)")
    f.add_argument("--sheet", help="Excel: a sheet name, or * for every sheet")
    hdr = f.add_mutually_exclusive_group()
    hdr.add_argument("--header-row", type=int)
    hdr.add_argument("--header-match")
    f.add_argument("--null", action="append", dest="nulls", help="a value that loads as NULL (repeatable)")

    g = sub.add_parser("grant", help="give a read-only role SELECT on schemas")
    g.add_argument("--manifest", type=Path, help="use its [owner], readonly_role and grant_schemas")
    g.add_argument("--url-env", help="the variable holding the owner login")
    g.add_argument("--role")
    g.add_argument("--schema", action="append", dest="schemas")
    g.add_argument("--create-role", action="store_true")
    g.add_argument("--password-env", help="with --create-role: the variable holding the new role's password")
    g.add_argument("--yes", action="store_true", help="run the SQL (without it, only print it)")
    return p


def read_env_file(path: Path, env: dict[str, str]) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise Fatal(f"cannot read {path}: {e.strerror}") from None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        env.setdefault(key, value.strip().strip("'\""))


def connect(owner: Owner, env: Mapping[str, str]) -> psycopg.Connection:
    try:
        info = owner.conninfo(env)
    except ManifestError as e:
        raise Fatal(str(e)) from None
    try:
        return psycopg.connect(info, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S)
    except psycopg.Error:  # its message can name the user and host: say only which variables
        raise Fatal(f"cannot connect with the owner login (check {owner.describe()} and that the database is running)") from None


def _url_owner(var: str | None) -> Owner:
    if not var or not ENV_NAME.fullmatch(var):
        raise Fatal("--url-env must name an environment variable")
    return Owner(url_env=var)


def cmd_load(manifest: Manifest, owner: Owner, env: Mapping[str, str]) -> int:
    with connect(owner, env) as conn:
        try:
            results = run(conn, manifest)
        except LoadError as e:
            raise Fatal(str(e)) from None
    for r in results:
        print(f"{r.schema}.{r.table}: {r.rows} rows from {r.source}")
    if manifest.transforms:
        print(f"transforms: {len(manifest.transform_files())} file(s) run")
    if manifest.readonly_role and manifest.grant_schemas:
        print(f"granted {manifest.readonly_role} read access to {', '.join(manifest.grant_schemas)}")
    return 0


def cmd_grant(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if args.manifest:
        if args.url_env or args.role or args.schemas:
            raise Fatal("--manifest takes the owner, role and schemas from the manifest; drop the other flags")
        manifest = _manifest(args.manifest)
        if manifest.owner is None or not manifest.readonly_role or not manifest.grant_schemas:
            raise Fatal("the manifest needs [owner], readonly_role and grant_schemas")
        owner, role, schemas = manifest.owner, manifest.readonly_role, list(manifest.grant_schemas)
    else:
        owner = _url_owner(args.url_env)
        if not args.role or not args.schemas:
            raise Fatal("pass --role and at least one --schema (or --manifest)")
        role, schemas = args.role, args.schemas
    password = None
    if args.create_role:
        if not args.password_env:
            raise Fatal("--create-role needs --password-env")
        password = env.get(args.password_env, "")
    elif args.password_env:
        raise Fatal("--password-env is only for --create-role")
    with connect(owner, env) as conn:
        try:
            statements = grants.plan(conn, role, schemas, create_role=args.create_role, password=password)
        except grants.GrantError as e:
            raise Fatal(str(e)) from None
        for s in statements:
            print(f"{s.shown};")
        if not args.yes:
            print("Nothing was run. Rerun with --yes to apply.")
            return 0
        try:
            grants.apply(conn, statements)
        except psycopg.Error as e:
            raise Fatal(f"grant failed: {e.diag.message_primary or type(e).__name__} (nothing was changed)") from None
    print("Applied.")
    return 0


def _manifest(path: Path) -> Manifest:
    try:
        return load_manifest(path)
    except ManifestError as e:
        raise Fatal(f"{path}: {e}") from None


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    args = _parser().parse_args(argv)
    environ: dict[str, str] = dict(os.environ) if env is None else dict(env)
    try:
        if args.env_file:
            read_env_file(args.env_file, environ)
        if args.command == "load":
            manifest = _manifest(args.manifest)
            if manifest.owner is None:
                raise Fatal(f"{args.manifest}: needs an [owner] section")
            return cmd_load(manifest, manifest.owner, environ)
        if args.command == "file":
            path = args.path.resolve()
            if args.header_row is not None and args.header_row < 1:
                raise Fatal("--header-row must be 1 or more")
            # The absolute path is read as is, so [ ] * ? in a file name aren't a glob.
            source = Source(str(path), table=args.table, sheet=args.sheet, header_row=args.header_row,
                            header_match=args.header_match, null_values=tuple(args.nulls or [""]))
            manifest = Manifest(path.parent, (source,), staging_schema=args.schema)
            return cmd_load(manifest, _url_owner(args.url_env), environ)
        return cmd_grant(args, environ)
    except Fatal as e:
        sys.stderr.write(f"error: {e}\n")
        return 2
    except Exception as e:  # never a traceback: it could quote row data
        sys.stderr.write(f"error: unexpected {type(e).__name__}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
