"""raw -> core. A pure function of raw.release: `rebuild_core` truncates core and rebuilds it.

Per-release parsing (`parse_release`) is plain Python on one payload so it is unit-testable without a
database. Cross-release logic (current value, deltas, display names) is set-based SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import psycopg

from austender.abn import name_norm, valid_abn

CANBERRA = ZoneInfo("Australia/Canberra")
_AMENDMENT_ID = re.compile(r"(.+)-A(\d+)")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        from datetime import UTC

        ts = ts.replace(tzinfo=UTC)
    return ts


def canberra_date(value: str | None) -> date | None:
    """Calendar date in Australia/Canberra of an ISO timestamp (2026-06-30T14:00:00Z -> 2026-07-01)."""
    ts = parse_ts(value)
    return ts.astimezone(CANBERRA).date() if ts else None


def financial_year(d: date) -> str:
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def parse_amount(value: object) -> Decimal | None:
    """Amounts arrive as strings. Parsed straight to Decimal, never via float."""
    if value is None or value == "":
        return None
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return d.quantize(Decimal("0.01")) if d.is_finite() else None


@dataclass
class ParsedSupplier:
    ordinal: int
    name: str | None
    name_norm: str
    abn_raw: str | None
    abn: str | None
    supplier_key: str
    country: str | None
    state: str | None
    is_overseas: bool


@dataclass
class ParsedRelease:
    release_id: str
    ocid: str
    kind: str  # 'contract' | 'amendment'
    notice_id: str
    release_ts: datetime
    status: str | None
    value: Decimal | None
    period_start: date | None
    period_end: date | None
    amendment_seq: int | None = None
    amendment_id: str | None = None
    amends_release_id: str | None = None
    publish_date: date | None = None
    financial_year: str | None = None
    title: str | None = None
    description: str | None = None
    unspsc: str | None = None
    procurement_method: str | None = None
    limited_tender_exempt: bool | None = None
    limited_tender_reason_code: str | None = None
    limited_tender_reason: str | None = None
    agency_key: str | None = None
    agency_name: str | None = None
    agency_abn: str | None = None
    agency_name_norm: str | None = None
    source_url: str | None = None
    suppliers: list[ParsedSupplier] = field(default_factory=list)


def _s(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _abn_raw(party: dict) -> str | None:
    for ident in party.get("additionalIdentifiers") or []:
        if ident.get("scheme") == "AU-ABN" and ident.get("id"):
            return _s(ident["id"])
    return None


def entity_key(abn: str | None, norm: str) -> str:
    return abn if abn else f"name:{norm}"


def _is_overseas(address: dict) -> bool:
    country = (_s(address.get("countryName")) or "").upper()
    region = (_s(address.get("region")) or "").lower()
    return (country not in ("", "AUSTRALIA", "AU")) or region == "outside australia"


def parse_release(payload: dict) -> ParsedRelease | None:
    """Parse one OCDS release. Returns None when it cannot be placed (no contract, no usable id)."""
    contracts = payload.get("contracts") or []
    release_id = _s(payload.get("id"))
    release_ts = parse_ts(payload.get("date"))
    if not contracts or not release_id or release_ts is None:
        return None
    c = contracts[0]
    notice_id = _s(c.get("id"))
    if not notice_id:
        return None
    tags = payload.get("tag") or []
    amendments = c.get("amendments") or []
    period = c.get("period") or {}
    base = dict(
        release_id=release_id,
        ocid=_s(payload.get("ocid")) or "",
        notice_id=notice_id,
        release_ts=release_ts,
        status=(_s(c.get("status")) or "").lower() or None,
        value=parse_amount((c.get("value") or {}).get("amount")),
        period_start=canberra_date(period.get("startDate")),
        period_end=canberra_date(period.get("endDate")),
    )

    if "contractAmendment" in tags or amendments:
        # contracts[0].id is the PARENT notice; the amendment id CN<n>-A<k> is in amendments[0].id.
        m = _AMENDMENT_ID.fullmatch(_s(amendments[0].get("id")) or "") if amendments else None
        if not m:
            return None
        return ParsedRelease(
            kind="amendment",
            amendment_seq=int(m.group(2)),
            amendment_id=m.group(0),
            amends_release_id=_s(amendments[0].get("amendsReleaseID")),
            **base,
        )

    tender = payload.get("tender") or {}
    parties = payload.get("parties") or []
    agency = next((p for p in parties if "procuringEntity" in (p.get("roles") or [])), {})
    agency_name = _s(agency.get("name"))
    agency_norm = name_norm(agency_name)
    agency_abn = valid_abn(_abn_raw(agency))
    exempt = _s(tender.get("limitedTenderExempt"))
    items = c.get("items") or []
    unspsc = _s(((items[0].get("classification") or {}).get("id"))) if items else None
    publish_date = release_ts.astimezone(CANBERRA).date()

    suppliers: list[ParsedSupplier] = []
    for party in parties:
        if "supplier" not in (party.get("roles") or []):
            continue
        name = _s(party.get("name"))
        norm = name_norm(name)
        abn_raw = _abn_raw(party)
        abn = valid_abn(abn_raw)
        addr = party.get("address") or {}
        suppliers.append(
            ParsedSupplier(
                ordinal=len(suppliers) + 1,
                name=name,
                name_norm=norm,
                abn_raw=abn_raw,
                abn=abn,
                supplier_key=entity_key(abn, norm),
                country=_s(addr.get("countryName")),
                state=_s(addr.get("region")),
                is_overseas=_is_overseas(addr),
            )
        )

    return ParsedRelease(
        kind="contract",
        publish_date=publish_date,
        financial_year=financial_year(publish_date),
        title=_s(c.get("title")),
        description=_s(c.get("description")),
        unspsc=unspsc,
        procurement_method=_s(tender.get("procurementMethod")),
        limited_tender_exempt={"1": True, "0": False}.get(exempt or ""),
        limited_tender_reason_code=_s(tender.get("limitedTenderReasonCode")),
        limited_tender_reason=_s(tender.get("limitedTenderReason")),
        agency_key=entity_key(agency_abn, agency_norm),
        agency_name=agency_name or "(unknown agency)",
        agency_abn=agency_abn,
        agency_name_norm=agency_norm,
        source_url=None,
        suppliers=suppliers,
        **base,
    )


_STG_RELEASE = (
    "release_id", "ocid", "kind", "notice_id", "amendment_seq", "amendment_id", "amends_release_id",
    "release_ts", "publish_date", "financial_year", "status", "value", "period_start", "period_end",
    "title", "description", "unspsc", "procurement_method", "limited_tender_exempt",
    "limited_tender_reason_code", "limited_tender_reason", "agency_key", "agency_name", "agency_abn",
    "agency_name_norm", "source_url",
)
_STG_SUPPLIER = (
    "release_id", "ordinal", "name", "name_norm", "abn_raw", "abn", "supplier_key", "country", "state",
    "is_overseas",
)

_CREATE_STAGING = """
CREATE TEMP TABLE stg_release (
    release_id text, ocid text, kind text, notice_id text, amendment_seq int, amendment_id text,
    amends_release_id text, release_ts timestamptz, publish_date date, financial_year text, status text,
    value numeric(18,2), period_start date, period_end date, title text, description text, unspsc text,
    procurement_method text, limited_tender_exempt boolean, limited_tender_reason_code text,
    limited_tender_reason text, agency_key text, agency_name text, agency_abn text, agency_name_norm text,
    source_url text
) ON COMMIT DROP;
CREATE TEMP TABLE stg_supplier (
    release_id text, ordinal int, name text, name_norm text, abn_raw text, abn text, supplier_key text,
    country text, state text, is_overseas boolean
) ON COMMIT DROP;
"""

_BUILD_CORE = """
TRUNCATE core.amendment, core.contract_supplier, core.contract, core.agency, core.supplier;

-- One parent row per notice. If a notice has several parent releases, the earliest one wins.
INSERT INTO core.contract (
    notice_id, ocid, release_id, publish_ts, publish_date, financial_year, status, is_cancelled,
    procurement_method, limited_tender_exempt, limited_tender_reason_code, limited_tender_reason, unspsc,
    period_start, period_end, current_period_start, current_period_end, title, description, agency_key,
    original_value, current_value, source_url)
SELECT DISTINCT ON (notice_id)
    notice_id, ocid, release_id, release_ts, publish_date, financial_year, status,
    coalesce(status = 'cancelled', false),
    procurement_method, limited_tender_exempt, limited_tender_reason_code, limited_tender_reason, unspsc,
    period_start, period_end, period_start, period_end, title, description, agency_key,
    value, value, source_url
FROM stg_release
WHERE kind = 'contract'
ORDER BY notice_id, release_ts, release_id;

-- Amendments are full snapshots. Orphans (no parent release in raw) are left out.
INSERT INTO core.amendment (
    notice_id, seq, amendment_id, release_id, amends_release_id, value, period_start, period_end,
    status, is_cancelled)
SELECT DISTINCT ON (r.notice_id, r.amendment_seq)
    r.notice_id, r.amendment_seq, r.amendment_id, r.release_id, r.amends_release_id, r.value,
    r.period_start, r.period_end, r.status, coalesce(r.status = 'cancelled', false)
FROM stg_release r
JOIN core.contract c USING (notice_id)
WHERE r.kind = 'amendment'
ORDER BY r.notice_id, r.amendment_seq, r.release_ts, r.release_id;

-- value_delta: against the previous NON-cancelled snapshot (the parent if there is none).
UPDATE core.amendment a
SET value_delta = d.delta
FROM (
    SELECT a2.notice_id, a2.seq,
           a2.value - coalesce(lag(a2.value) OVER (PARTITION BY a2.notice_id ORDER BY a2.seq), c.original_value) AS delta
    FROM core.amendment a2
    JOIN core.contract c USING (notice_id)
    WHERE NOT a2.is_cancelled
) d
WHERE a.notice_id = d.notice_id AND a.seq = d.seq;

-- Counts, and the current state from the highest-k NON-cancelled amendment.
UPDATE core.contract c
SET amendment_count = s.n,
    cancelled_amendment_count = s.n_cancelled,
    current_value = coalesce(s.cur_value, c.original_value),
    current_period_start = CASE WHEN s.has_current THEN s.cur_start ELSE c.period_start END,
    current_period_end   = CASE WHEN s.has_current THEN s.cur_end   ELSE c.period_end   END
FROM (
    SELECT notice_id,
           count(*) AS n,
           count(*) FILTER (WHERE is_cancelled) AS n_cancelled,
           bool_or(NOT is_cancelled) AS has_current,
           (array_agg(value ORDER BY seq DESC) FILTER (WHERE NOT is_cancelled))[1] AS cur_value,
           (array_agg(period_start ORDER BY seq DESC) FILTER (WHERE NOT is_cancelled))[1] AS cur_start,
           (array_agg(period_end ORDER BY seq DESC) FILTER (WHERE NOT is_cancelled))[1] AS cur_end
    FROM core.amendment GROUP BY notice_id
) s
WHERE c.notice_id = s.notice_id;

INSERT INTO core.contract_supplier (
    notice_id, ordinal, name, name_norm, abn_raw, abn, supplier_key, country, state, is_overseas)
SELECT c.notice_id, s.ordinal, s.name, s.name_norm, s.abn_raw, s.abn, s.supplier_key, s.country, s.state,
       s.is_overseas
FROM stg_supplier s
JOIN core.contract c ON c.release_id = s.release_id;

-- Agency name from the latest parent notice.
INSERT INTO core.agency (agency_key, abn, name, name_norm)
SELECT DISTINCT ON (r.agency_key) r.agency_key, r.agency_abn, r.agency_name, r.agency_name_norm
FROM stg_release r
JOIN core.contract c ON c.release_id = r.release_id
ORDER BY r.agency_key, c.publish_ts DESC, c.notice_id DESC;

-- Supplier name: most frequent published name, ties broken by latest notice, then by name.
INSERT INTO core.supplier (supplier_key, name, abn, is_overseas)
SELECT a.supplier_key, coalesce(n.name, a.supplier_key), a.abn, a.is_overseas
FROM (
    SELECT supplier_key, max(abn) AS abn, bool_or(is_overseas) AS is_overseas
    FROM core.contract_supplier GROUP BY supplier_key
) a
LEFT JOIN (
    SELECT DISTINCT ON (supplier_key) supplier_key, name
    FROM (
        SELECT cs.supplier_key, cs.name, count(*) AS n, max(c.publish_ts) AS latest
        FROM core.contract_supplier cs
        JOIN core.contract c USING (notice_id)
        WHERE cs.name IS NOT NULL
        GROUP BY cs.supplier_key, cs.name
    ) t
    ORDER BY supplier_key, n DESC, latest DESC, name
) n USING (supplier_key);
"""


_BATCH = 5000


def _flush(conn: psycopg.Connection, releases: list[tuple], suppliers: list[tuple]) -> None:
    # One COPY at a time per connection, so the two staging tables are filled in turn.
    for table, cols, rows in (("stg_release", _STG_RELEASE, releases), ("stg_supplier", _STG_SUPPLIER, suppliers)):
        if rows:
            with conn.cursor() as cur, cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
                for row in rows:
                    cp.write_row(row)
    releases.clear()
    suppliers.clear()


def rebuild_core(conn: psycopg.Connection) -> dict[str, int]:
    """Rebuild core from raw inside the caller's transaction. Returns counters."""
    conn.execute(_CREATE_STAGING)  # type: ignore[arg-type]
    parsed = unparsed = 0
    releases: list[tuple] = []
    suppliers: list[tuple] = []
    with conn.cursor(name="raw_scan") as scan:
        scan.itersize = 2000
        scan.execute("SELECT payload FROM raw.release")
        for (payload,) in scan:
            p = parse_release(payload)
            if p is None:
                unparsed += 1
                continue
            parsed += 1
            releases.append(tuple(getattr(p, col) for col in _STG_RELEASE))
            suppliers.extend((p.release_id, *(getattr(s, col) for col in _STG_SUPPLIER[1:])) for s in p.suppliers)
            if len(releases) >= _BATCH:
                _flush(conn, releases, suppliers)
    _flush(conn, releases, suppliers)
    conn.execute(_BUILD_CORE)  # type: ignore[arg-type]
    return {"parsed": parsed, "unparsed": unparsed}
