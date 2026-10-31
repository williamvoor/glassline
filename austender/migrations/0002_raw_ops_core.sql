-- ops: ingest bookkeeping
CREATE TABLE ops.ingest_run (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind             text        NOT NULL,            -- fetch-month | backfill | seed
    month            text,                            -- YYYY-MM for fetch-month
    status           text        NOT NULL DEFAULT 'running',  -- running | complete | failed
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    pages            integer     NOT NULL DEFAULT 0,
    releases_fetched integer     NOT NULL DEFAULT 0,
    releases_new     integer     NOT NULL DEFAULT 0,
    releases_dup     integer     NOT NULL DEFAULT 0,
    releases_rejected integer    NOT NULL DEFAULT 0,
    error            text
);

-- A month is complete only when all its pages are stored.
CREATE TABLE ops.checkpoint (
    month            text PRIMARY KEY,                -- YYYY-MM (UTC calendar month)
    ingest_run_id    bigint      NOT NULL REFERENCES ops.ingest_run (id),
    pages            integer     NOT NULL,
    releases_fetched integer     NOT NULL,
    completed_at     timestamptz NOT NULL DEFAULT now()
);

-- A known release_id seen again with a different payload hash. The stored raw row is unchanged.
CREATE TABLE ops.rejected_release (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_id        text        NOT NULL,
    ocid              text,
    existing_sha256   text        NOT NULL,
    new_sha256        text        NOT NULL,
    payload           jsonb       NOT NULL,
    ingest_run_id     bigint REFERENCES ops.ingest_run (id),
    rejected_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (release_id, new_sha256)
);

-- raw: releases as received, append-only
CREATE TABLE raw.release (
    release_id     text PRIMARY KEY,
    ocid           text        NOT NULL,
    payload        jsonb       NOT NULL,
    payload_sha256 text        NOT NULL,
    ingest_run_id  bigint REFERENCES ops.ingest_run (id),
    first_seen_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX release_ocid_idx ON raw.release (ocid);

-- core: normalised. A pure function of raw.release (rebuilt by `austender rebuild`).
CREATE TABLE core.contract (
    notice_id        text PRIMARY KEY,                -- CN number of the parent notice
    ocid             text        NOT NULL,
    release_id       text        NOT NULL,            -- the parent release this row came from
    publish_ts       timestamptz NOT NULL,
    publish_date     date        NOT NULL,            -- Australia/Canberra calendar date
    financial_year   text        NOT NULL,            -- 2024-25 (July-June)
    status           text,
    is_cancelled     boolean     NOT NULL,
    procurement_method text,
    limited_tender_exempt boolean,
    limited_tender_reason_code text,
    limited_tender_reason text,
    unspsc           text,
    period_start     date,
    period_end       date,
    current_period_start date,                         -- period of the latest non-cancelled version
    current_period_end   date,
    title            text,
    description      text,
    agency_key       text        NOT NULL,
    original_value   numeric(18,2),
    current_value    numeric(18,2),
    amendment_count  integer     NOT NULL DEFAULT 0,
    cancelled_amendment_count integer NOT NULL DEFAULT 0,
    source_url       text
);

CREATE TABLE core.amendment (
    notice_id        text        NOT NULL REFERENCES core.contract (notice_id),
    seq              integer     NOT NULL,            -- k in CN<n>-A<k>
    amendment_id     text        NOT NULL,
    release_id       text        NOT NULL,
    amends_release_id text,
    value            numeric(18,2),                   -- full snapshot, not a delta
    period_start     date,
    period_end       date,
    status           text,
    is_cancelled     boolean     NOT NULL,
    value_delta      numeric(18,2),                   -- vs previous non-cancelled snapshot; NULL if cancelled
    -- No amendment date: the API gives none (the release date is a re-publication stamp).
    PRIMARY KEY (notice_id, seq)
);

CREATE TABLE core.contract_supplier (
    notice_id        text        NOT NULL REFERENCES core.contract (notice_id),
    ordinal          integer     NOT NULL,
    name             text,
    name_norm        text        NOT NULL,
    abn_raw          text,
    abn              text,                            -- only when the checksum passes
    supplier_key     text        NOT NULL,
    country          text,
    state            text,
    is_overseas      boolean     NOT NULL,
    PRIMARY KEY (notice_id, ordinal)
);
CREATE INDEX contract_supplier_key_idx ON core.contract_supplier (supplier_key);

CREATE TABLE core.agency (
    agency_key       text PRIMARY KEY,
    abn              text,
    name             text        NOT NULL,            -- from the latest parent notice
    name_norm        text        NOT NULL
);

CREATE TABLE core.supplier (
    supplier_key     text PRIMARY KEY,
    name             text        NOT NULL,            -- most frequent published name, ties: latest notice
    abn              text,
    is_overseas      boolean     NOT NULL
);
