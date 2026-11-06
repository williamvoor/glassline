"""Scanner-aware SQL text operations shared by query execution and Drill queries."""

from pglast.parser import ParseError, scan

COMMENT_TOKENS = ("SQL_COMMENT", "C_COMMENT")


def normalise(sql: str) -> str:
    """Remove terminal semicolons, including ones before trailing comments."""
    body = sql.strip()
    try:
        tokens = [t for t in scan(body) if t.name not in COMMENT_TOKENS]
    except ParseError:
        return body.rstrip(";").rstrip()  # the guard reports invalid SQL
    while tokens and tokens[-1].name == "ASCII_59":
        last = tokens.pop()
        body = body[:last.start] + body[last.end + 1:]
    return body.strip()


def wrap(sql: str, alias: str, suffix: str = "") -> str:
    """Wrap a query on its own lines so trailing line comments stay inside."""
    return f"SELECT * FROM (\n{normalise(sql)}\n) AS {alias}{suffix}"
