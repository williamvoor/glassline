-- staging.houses_raw (the workbook as it is) -> mart. Runs as vicprop_loader, in the load's
-- transaction. mart's tables, comments and indexes come from migrations; this only refills them.
--
-- Column names follow the header: c_2015 … c_2025 are the yearly medians, each followed by its
-- flag column (column_3, column_5, … column_23: an unnamed header, so named by position).
-- prelim is the preliminary 2026 median with its flag in column_25; c_24_25, c_15_25 and pa are
-- the published change figures. Rows without a locality are the sub-header rows. Every column is
-- cast, because a column with no values in some workbook loads as text.

-- Layout guards, as the pre-dataload parser had: stop (and roll back) instead of loading a
-- shifted workbook. The yearly columns are found by their labels, so a missing one fails anyway.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM staging.houses_raw WHERE locality IS NULL AND prelim::numeric = 2026) THEN
        RAISE EXCEPTION 'workbook layout changed: no 2026 under Prelim';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM staging.houses_raw AS h
        CROSS JOIN LATERAL (VALUES (h.column_3::text), (h.column_5::text), (h.column_7::text), (h.column_9::text),
            (h.column_11::text), (h.column_13::text), (h.column_15::text), (h.column_17::text), (h.column_19::text),
            (h.column_21::text), (h.column_23::text), (h.column_25::text)) AS f (v)
        WHERE h.locality IS NOT NULL AND f.v IS NOT NULL AND f.v NOT IN ('^', '*')
    ) THEN
        RAISE EXCEPTION 'workbook layout changed: the flag columns hold something other than ^ or *';
    END IF;
END
$$;

DELETE FROM mart.suburb_price;
DELETE FROM mart.suburb_trend;

INSERT INTO mart.suburb_price (locality, year, median_price, is_prelim, flagged)
SELECT h.locality, v.year, v.price, v.is_prelim, coalesce(v.flag IN ('^', '*'), false)
FROM staging.houses_raw AS h
CROSS JOIN LATERAL (VALUES
    (2015, h.c_2015::numeric, h.column_3::text, false),
    (2016, h.c_2016::numeric, h.column_5::text, false),
    (2017, h.c_2017::numeric, h.column_7::text, false),
    (2018, h.c_2018::numeric, h.column_9::text, false),
    (2019, h.c_2019::numeric, h.column_11::text, false),
    (2020, h.c_2020::numeric, h.column_13::text, false),
    (2021, h.c_2021::numeric, h.column_15::text, false),
    (2022, h.c_2022::numeric, h.column_17::text, false),
    (2023, h.c_2023::numeric, h.column_19::text, false),
    (2024, h.c_2024::numeric, h.column_21::text, false),
    (2025, h.c_2025::numeric, h.column_23::text, false),
    (2026, h.prelim::numeric, h.column_25::text, true)
) AS v (year, price, flag, is_prelim)
WHERE h.locality IS NOT NULL
  AND v.price IS NOT NULL;   -- "-": no published median, so no row (never zero or NULL)

INSERT INTO mart.suburb_trend (locality, change_24_25_pct, growth_15_25_pct, growth_pa_pct)
-- trim_scale: the source writes "5.0"; the published value is 5 (as the pre-dataload loader stored it).
SELECT locality, trim_scale(c_24_25::numeric), trim_scale(c_15_25::numeric), trim_scale(pa::numeric)
FROM staging.houses_raw
WHERE locality IS NOT NULL;
