-- Suppliers published as "Name Withheld" without an ABN get one key per notice, and the agent should answer
-- by financial year, which is what the summary tables offer.
COMMENT ON COLUMN mart.contract_supplier.supplier_key IS 'Supplier identity: its valid ABN, else ''name:'' plus normalised name; ''withheld:<notice_id>'' when the name was withheld (each such supplier is unknown, never one entity). Group by this, not by name. Joins to mart.supplier.';
COMMENT ON COLUMN mart.supplier.supplier_key IS 'Valid ABN, else ''name:'' plus normalised name; ''withheld:<notice_id>'' for a withheld name (one row per notice, each named "Name Withheld"). Primary key.';
COMMENT ON COLUMN mart.supplier_fy.supplier_key IS 'Supplier identity (valid ABN, else ''name:'' + normalised name, or ''withheld:<notice_id>'').';
COMMENT ON COLUMN mart.supplier_agency_fy.supplier_key IS 'Supplier identity (valid ABN, else ''name:'' + normalised name, or ''withheld:<notice_id>'').';
COMMENT ON COLUMN mart.contract.financial_year IS 'Australian financial year of publish_date, July to June, text like ''2024-25'' (1 Jul 2024 to 30 Jun 2025). Prefer financial years: the *_fy summary tables use them. If a question names a plain year ("in 2024"), answer by financial year and say which one; filter on publish_date only when a calendar year is explicitly asked for.';
