-- mart: the explorer's whole world. Plain tables, rebuilt from core by `austender rebuild`.
-- The comments below are the agent's only data dictionary: keep them accurate and concise.

CREATE TABLE mart.contract (
    notice_id                  text PRIMARY KEY,
    agency_key                 text           NOT NULL,
    agency_name                text           NOT NULL,
    publish_date               date           NOT NULL,
    financial_year             text           NOT NULL,
    procurement_method         text,
    limited_tender_reason_code text,
    limited_tender_reason      text,
    unspsc                     text,
    unspsc_segment             text,
    unspsc_family              text,
    unspsc_class               text,
    period_start               date,
    period_end                 date,
    original_value             numeric(18,2),
    current_value              numeric(18,2),
    value_change               numeric(18,2),
    amendment_count            integer        NOT NULL,
    cancelled_amendment_count  integer        NOT NULL,
    supplier_count             integer        NOT NULL,
    is_cancelled               boolean        NOT NULL,
    description                text,
    source_url                 text
);
CREATE INDEX contract_fy_idx        ON mart.contract (financial_year);
CREATE INDEX contract_agency_idx    ON mart.contract (agency_key, financial_year);
CREATE INDEX contract_publish_idx   ON mart.contract (publish_date);
CREATE INDEX contract_segment_idx   ON mart.contract (unspsc_segment);
CREATE INDEX contract_family_idx    ON mart.contract (unspsc_family);

CREATE TABLE mart.contract_supplier (
    notice_id                  text           NOT NULL,
    ordinal                    integer        NOT NULL,
    supplier_key               text           NOT NULL,
    supplier_name              text           NOT NULL,
    supplier_abn               text,
    is_overseas                boolean        NOT NULL,
    value_share                numeric        NOT NULL,
    contract_value             numeric(18,2),
    supplier_count             integer        NOT NULL,
    financial_year             text           NOT NULL,
    publish_date               date           NOT NULL,
    agency_key                 text           NOT NULL,
    agency_name                text           NOT NULL,
    procurement_method         text,
    unspsc                     text,
    unspsc_segment             text,
    unspsc_family              text,
    unspsc_class               text,
    PRIMARY KEY (notice_id, ordinal)
);
CREATE INDEX cs_supplier_fy_idx   ON mart.contract_supplier (supplier_key, financial_year);
CREATE INDEX cs_agency_fy_idx     ON mart.contract_supplier (agency_key, financial_year);
CREATE INDEX cs_fy_idx            ON mart.contract_supplier (financial_year);
CREATE INDEX cs_publish_idx       ON mart.contract_supplier (publish_date);
CREATE INDEX cs_segment_idx       ON mart.contract_supplier (unspsc_segment, financial_year);
CREATE INDEX cs_family_idx        ON mart.contract_supplier (unspsc_family);

CREATE TABLE mart.supplier (
    supplier_key               text PRIMARY KEY,
    supplier_name              text           NOT NULL,
    supplier_abn               text,
    is_overseas                boolean        NOT NULL,
    contract_count             integer        NOT NULL,
    total_value                numeric(20,2)  NOT NULL,
    first_publish_date         date,
    last_publish_date          date
);
CREATE INDEX supplier_name_idx ON mart.supplier (lower(supplier_name) text_pattern_ops);

CREATE TABLE mart.agency (
    agency_key                 text PRIMARY KEY,
    agency_name                text           NOT NULL,
    agency_abn                 text,
    contract_count             integer        NOT NULL,
    total_value                numeric(20,2)  NOT NULL,
    first_publish_date         date,
    last_publish_date          date
);
CREATE INDEX agency_name_idx ON mart.agency (lower(agency_name) text_pattern_ops);

CREATE TABLE mart.supplier_fy (
    supplier_key               text           NOT NULL,
    supplier_name              text           NOT NULL,
    financial_year             text           NOT NULL,
    contract_count             integer        NOT NULL,
    total_value                numeric(20,2)  NOT NULL,
    PRIMARY KEY (supplier_key, financial_year)
);
CREATE INDEX supplier_fy_fy_idx ON mart.supplier_fy (financial_year, total_value DESC);

CREATE TABLE mart.agency_fy (
    agency_key                 text           NOT NULL,
    agency_name                text           NOT NULL,
    financial_year             text           NOT NULL,
    contract_count             integer        NOT NULL,
    total_value                numeric(20,2)  NOT NULL,
    PRIMARY KEY (agency_key, financial_year)
);
CREATE INDEX agency_fy_fy_idx ON mart.agency_fy (financial_year, total_value DESC);

CREATE TABLE mart.supplier_agency_fy (
    supplier_key               text           NOT NULL,
    supplier_name              text           NOT NULL,
    agency_key                 text           NOT NULL,
    agency_name                text           NOT NULL,
    financial_year             text           NOT NULL,
    contract_count             integer        NOT NULL,
    total_value                numeric(20,2)  NOT NULL,
    PRIMARY KEY (supplier_key, agency_key, financial_year)
);
CREATE INDEX saf_agency_idx ON mart.supplier_agency_fy (agency_key, financial_year);
CREATE INDEX saf_fy_idx     ON mart.supplier_agency_fy (financial_year);

-- ---------------------------------------------------------------------------------------------
-- Comments
-- ---------------------------------------------------------------------------------------------

COMMENT ON TABLE mart.contract IS
'One row per Australian Commonwealth contract notice (AusTender), INCLUDING cancelled ones. Values are AUD as published. Use for contract-level questions (list, count, period, amendments, description). WARNING: cancelled contracts keep their full value here, so for any spend total add "WHERE NOT is_cancelled" or prefer mart.contract_supplier or the *_fy summary tables, which already exclude cancelled contracts.';
COMMENT ON COLUMN mart.contract.notice_id IS 'AusTender contract notice number, e.g. CN4273249. Primary key.';
COMMENT ON COLUMN mart.contract.agency_key IS 'Procuring agency key (agency ABN, else normalised name). Joins to mart.agency.agency_key.';
COMMENT ON COLUMN mart.contract.agency_name IS 'Agency display name (its latest published name; agencies get renamed).';
COMMENT ON COLUMN mart.contract.publish_date IS 'Date the contract notice was published, in Australia/Canberra time. Amendments have no date in the source.';
COMMENT ON COLUMN mart.contract.financial_year IS 'Australian financial year of publish_date, July to June, text like ''2024-25'' (1 Jul 2024 to 30 Jun 2025). "In 2024" is ambiguous: say which year type you used.';
COMMENT ON COLUMN mart.contract.procurement_method IS 'open, limited or selective (AusTender procurement method). NULL if not published.';
COMMENT ON COLUMN mart.contract.limited_tender_reason_code IS 'Short code for why a limited tender was allowed (e.g. AD, TR, ER, VM, EU, CT, CP, EV). NULL for non-limited or older notices.';
COMMENT ON COLUMN mart.contract.limited_tender_reason IS 'Free text of the limited tender reason, including procurement rule clause numbers. NULL when not published.';
COMMENT ON COLUMN mart.contract.unspsc IS 'UNSPSC product/service classification code (8 digits, code only; titles are not in this database). NULL if missing.';
COMMENT ON COLUMN mart.contract.unspsc_segment IS 'First 2 digits of unspsc (broadest UNSPSC level).';
COMMENT ON COLUMN mart.contract.unspsc_family IS 'First 4 digits of unspsc.';
COMMENT ON COLUMN mart.contract.unspsc_class IS 'First 6 digits of unspsc.';
COMMENT ON COLUMN mart.contract.period_start IS 'Contract start date (Canberra time) of the latest non-cancelled version. NULL if not published.';
COMMENT ON COLUMN mart.contract.period_end IS 'Contract end date (Canberra time) of the latest non-cancelled version. NULL if not published.';
COMMENT ON COLUMN mart.contract.original_value IS 'AUD value when first published. Cancelled contracts still show their value.';
COMMENT ON COLUMN mart.contract.current_value IS 'AUD value now: the highest-numbered non-cancelled amendment''s value, else original_value. Total contract value; if several suppliers, it is NOT split here (see mart.contract_supplier.value_share).';
COMMENT ON COLUMN mart.contract.value_change IS 'current_value minus original_value (negative if amendments lowered it).';
COMMENT ON COLUMN mart.contract.amendment_count IS 'Number of amendment notices (-A1, -A2, ...), including cancelled ones.';
COMMENT ON COLUMN mart.contract.cancelled_amendment_count IS 'How many of amendment_count were cancelled (a subset, not additional). Effective amendments = amendment_count - cancelled_amendment_count.';
COMMENT ON COLUMN mart.contract.supplier_count IS 'Number of suppliers on the contract (0 if none were published).';
COMMENT ON COLUMN mart.contract.is_cancelled IS 'True if the contract notice itself was cancelled. Exclude these from spend totals.';
COMMENT ON COLUMN mart.contract.description IS 'Agency''s free-text description of what is bought. Use ILIKE for keyword search.';
COMMENT ON COLUMN mart.contract.source_url IS 'Link to the notice if the source supplied one; usually NULL.';

COMMENT ON TABLE mart.contract_supplier IS
'One row per contract x supplier, EXCLUDING cancelled contracts. Best table for spend by supplier, agency, year or category: SUM(value_share) never double counts, because a multi-supplier contract''s current value is split equally across its suppliers (AusTender does not publish the true split). Contract dimensions are copied in, so most questions need no join. AUD as published.';
COMMENT ON COLUMN mart.contract_supplier.notice_id IS 'Contract notice number; joins to mart.contract.notice_id for description or amendment details.';
COMMENT ON COLUMN mart.contract_supplier.ordinal IS 'Position of the supplier within the notice (1, 2, ...). With notice_id it is the primary key.';
COMMENT ON COLUMN mart.contract_supplier.supplier_key IS 'Supplier identity: its valid ABN, else ''name:'' plus normalised name. Group by this, not by name. Joins to mart.supplier.';
COMMENT ON COLUMN mart.contract_supplier.supplier_name IS 'Supplier display name (most frequently published spelling for the supplier_key).';
COMMENT ON COLUMN mart.contract_supplier.supplier_abn IS 'Supplier ABN (11 digits) if published and valid; NULL otherwise (about 7.5% of suppliers, normal).';
COMMENT ON COLUMN mart.contract_supplier.is_overseas IS 'True if the published supplier country is outside Australia.';
COMMENT ON COLUMN mart.contract_supplier.value_share IS 'AUD: contract current_value divided by supplier_count, unrounded. Sum this for supplier or agency spend.';
COMMENT ON COLUMN mart.contract_supplier.contract_value IS 'AUD total current value of the whole contract (same on every supplier row). Do NOT sum this across suppliers; it double counts.';
COMMENT ON COLUMN mart.contract_supplier.supplier_count IS 'Number of suppliers on the contract.';
COMMENT ON COLUMN mart.contract_supplier.financial_year IS 'Financial year of publish_date, July to June, text like ''2024-25''.';
COMMENT ON COLUMN mart.contract_supplier.publish_date IS 'Notice publish date in Australia/Canberra time.';
COMMENT ON COLUMN mart.contract_supplier.agency_key IS 'Procuring agency key; joins to mart.agency.';
COMMENT ON COLUMN mart.contract_supplier.agency_name IS 'Agency display name (latest published name).';
COMMENT ON COLUMN mart.contract_supplier.procurement_method IS 'open, limited or selective; NULL if not published.';
COMMENT ON COLUMN mart.contract_supplier.unspsc IS 'UNSPSC code (8 digits, no titles available); NULL if missing.';
COMMENT ON COLUMN mart.contract_supplier.unspsc_segment IS 'First 2 digits of unspsc.';
COMMENT ON COLUMN mart.contract_supplier.unspsc_family IS 'First 4 digits of unspsc.';
COMMENT ON COLUMN mart.contract_supplier.unspsc_class IS 'First 6 digits of unspsc.';

COMMENT ON TABLE mart.supplier IS
'One row per supplier (supplier_key): display name, ABN, and lifetime totals over non-cancelled contracts. Use to look up a supplier by name (ILIKE) or rank suppliers over all years; use supplier_fy for per-year numbers. AUD as published.';
COMMENT ON COLUMN mart.supplier.supplier_key IS 'Valid ABN, else ''name:'' plus normalised name. Primary key.';
COMMENT ON COLUMN mart.supplier.supplier_name IS 'Most frequently published spelling of the supplier name.';
COMMENT ON COLUMN mart.supplier.supplier_abn IS 'ABN if published and valid, else NULL.';
COMMENT ON COLUMN mart.supplier.is_overseas IS 'True if any of its notices gave a country outside Australia.';
COMMENT ON COLUMN mart.supplier.contract_count IS 'Number of non-cancelled contracts (a multi-supplier contract counts once for each supplier). 0 if all its contracts were cancelled.';
COMMENT ON COLUMN mart.supplier.total_value IS 'AUD sum of value_share over non-cancelled contracts, rounded to cents.';
COMMENT ON COLUMN mart.supplier.first_publish_date IS 'Earliest notice publish date among non-cancelled contracts; NULL if none.';
COMMENT ON COLUMN mart.supplier.last_publish_date IS 'Latest notice publish date among non-cancelled contracts; NULL if none.';

COMMENT ON TABLE mart.agency IS
'One row per procuring agency (agency_key): display name, ABN and lifetime totals over non-cancelled contracts. Use to look up an agency by name (ILIKE); use agency_fy for per-year numbers. AUD as published.';
COMMENT ON COLUMN mart.agency.agency_key IS 'Agency ABN if valid, else ''name:'' plus normalised name. Primary key.';
COMMENT ON COLUMN mart.agency.agency_name IS 'Latest published name (agencies get renamed).';
COMMENT ON COLUMN mart.agency.agency_abn IS 'Agency ABN if published and valid, else NULL.';
COMMENT ON COLUMN mart.agency.contract_count IS 'Number of non-cancelled contracts.';
COMMENT ON COLUMN mart.agency.total_value IS 'AUD sum of current contract values over non-cancelled contracts, rounded to cents.';
COMMENT ON COLUMN mart.agency.first_publish_date IS 'Earliest publish date among non-cancelled contracts; NULL if none.';
COMMENT ON COLUMN mart.agency.last_publish_date IS 'Latest publish date among non-cancelled contracts; NULL if none.';

COMMENT ON TABLE mart.supplier_fy IS
'Precomputed spend per supplier per financial year, excluding cancelled contracts (multi-supplier contracts split equally). Fastest table for "top suppliers in year X". Name included, so no join is needed. AUD as published.';
COMMENT ON COLUMN mart.supplier_fy.supplier_key IS 'Supplier identity (valid ABN, else ''name:'' + normalised name).';
COMMENT ON COLUMN mart.supplier_fy.supplier_name IS 'Supplier display name.';
COMMENT ON COLUMN mart.supplier_fy.financial_year IS 'Financial year July to June, text like ''2024-25''.';
COMMENT ON COLUMN mart.supplier_fy.contract_count IS 'Distinct non-cancelled contracts the supplier was on in that year.';
COMMENT ON COLUMN mart.supplier_fy.total_value IS 'AUD sum of value_share, rounded to cents.';

COMMENT ON TABLE mart.agency_fy IS
'Precomputed spend per agency per financial year, excluding cancelled contracts. Fastest table for "which agencies spent most in year X". AUD as published.';
COMMENT ON COLUMN mart.agency_fy.agency_key IS 'Agency key (ABN, else ''name:'' + normalised name).';
COMMENT ON COLUMN mart.agency_fy.agency_name IS 'Agency display name (latest published).';
COMMENT ON COLUMN mart.agency_fy.financial_year IS 'Financial year July to June, text like ''2024-25''.';
COMMENT ON COLUMN mart.agency_fy.contract_count IS 'Non-cancelled contracts published by the agency in that year.';
COMMENT ON COLUMN mart.agency_fy.total_value IS 'AUD sum of current contract values, rounded to cents.';

COMMENT ON TABLE mart.supplier_agency_fy IS
'Precomputed spend per supplier x agency x financial year, excluding cancelled contracts (multi-supplier contracts split equally). Use for "how is a supplier''s value split across agencies" and the reverse. Names included, so no join is needed. AUD as published.';
COMMENT ON COLUMN mart.supplier_agency_fy.supplier_key IS 'Supplier identity (valid ABN, else ''name:'' + normalised name).';
COMMENT ON COLUMN mart.supplier_agency_fy.supplier_name IS 'Supplier display name.';
COMMENT ON COLUMN mart.supplier_agency_fy.agency_key IS 'Agency key (ABN, else ''name:'' + normalised name).';
COMMENT ON COLUMN mart.supplier_agency_fy.agency_name IS 'Agency display name (latest published).';
COMMENT ON COLUMN mart.supplier_agency_fy.financial_year IS 'Financial year July to June, text like ''2024-25''.';
COMMENT ON COLUMN mart.supplier_agency_fy.contract_count IS 'Distinct non-cancelled contracts for this supplier and agency in that year.';
COMMENT ON COLUMN mart.supplier_agency_fy.total_value IS 'AUD sum of value_share, rounded to cents.';
