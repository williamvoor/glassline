"""Create or update the AusTender roles on the deployed database, with austender's own code.

Run it in the agent image (deploy/README.md, "Restore the database"). It is idempotent: run it once
before `pg_restore` (so the dump's GRANTs to explorer_ro apply) and once after (so database-level
settings the dump doesn't carry are set). Reads LOADER_DSN and EXPLORER_RO_PASSWORD from the
environment and never prints either.
"""

from __future__ import annotations

import os
import sys

from austender.db import connect, ensure_roles


def main() -> int:
    dsn = os.environ.get("LOADER_DSN", "").strip()
    ro_password = os.environ.get("EXPLORER_RO_PASSWORD", "").strip()
    if not dsn or not ro_password:
        print("ensure_roles: LOADER_DSN and EXPLORER_RO_PASSWORD are required", file=sys.stderr)
        return 1
    with connect(conninfo=dsn, autocommit=True) as conn:
        ensure_roles(conn, loader_password=None, explorer_ro_password=ro_password)
    print("ensure_roles: austender_loader and explorer_ro are set up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
