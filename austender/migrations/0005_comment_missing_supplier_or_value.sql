-- Say exactly which contracts each table counts: a contract with no published supplier is in the agency
-- tables but not the supplier tables, and one with no published value is only in mart.contract.
COMMENT ON TABLE mart.contract_supplier IS
'One row per contract x supplier, EXCLUDING cancelled contracts and contracts with no published value. Best table for spend by supplier, year or category: SUM(value_share) never double counts, because a multi-supplier contract''s current value is split equally across its suppliers (AusTender does not publish the true split). Contracts with no supplier listed are absent, so for agency totals prefer mart.agency_fy. Contract dimensions are copied in, so most questions need no join. AUD as published.';
COMMENT ON COLUMN mart.supplier.contract_count IS 'Number of non-cancelled contracts with a published value (a multi-supplier contract counts once for each supplier). 0 if none.';
COMMENT ON COLUMN mart.agency.contract_count IS 'Number of non-cancelled contracts with a published value, including ones with no supplier listed.';
COMMENT ON COLUMN mart.agency_fy.contract_count IS 'Non-cancelled contracts with a published value, published by the agency in that year, including ones with no supplier listed.';
