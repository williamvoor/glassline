-- mart: the explorer's whole world. Plain tables, refilled from the committed workbook by `vicprop load`.
-- The comments below are the agent's only data dictionary: keep them accurate and concise.

CREATE TABLE mart.suburb_price (
    locality      text           NOT NULL,
    year          smallint       NOT NULL,
    median_price  numeric(12,0)  NOT NULL,
    is_prelim     boolean        NOT NULL,
    flagged       boolean        NOT NULL,
    PRIMARY KEY (locality, year)
);
CREATE INDEX suburb_price_year_idx     ON mart.suburb_price (year);
CREATE INDEX suburb_price_locality_idx ON mart.suburb_price (locality);

CREATE TABLE mart.suburb_trend (
    locality           text          PRIMARY KEY,
    change_24_25_pct   numeric,
    growth_15_25_pct   numeric,
    growth_pa_pct      numeric
);

COMMENT ON TABLE mart.suburb_price IS 'One row per published yearly median house price for a Victorian suburb (locality), 2015 to 2026. Years with no published median (a dash in the source) have no row here, never a zero.';
COMMENT ON COLUMN mart.suburb_price.locality IS 'Suburb or locality name, uppercase (e.g. ABBOTSFORD). Unique per year.';
COMMENT ON COLUMN mart.suburb_price.year IS 'Calendar year of the median, 2015 to 2026. Use this to filter or compare years; do not pivot years into columns.';
COMMENT ON COLUMN mart.suburb_price.median_price IS 'Median house sale price for the suburb and year, in Australian dollars (AUD) as published. Never averaged across years without weighting; each year stands alone.';
COMMENT ON COLUMN mart.suburb_price.is_prelim IS 'True only for 2026: a preliminary median that will be revised. Prefer 2015-2025 for finished-year comparisons and say so when 2026 is shown.';
COMMENT ON COLUMN mart.suburb_price.flagged IS 'True when the source flagged this median (a ^ or * mark, whose legend is not published). Treat flagged medians with caution; they often sit beside years with no published median.';

COMMENT ON TABLE mart.suburb_trend IS 'One row per suburb with the change figures as published in the source. Prefer recomputing changes from mart.suburb_price over finished years; use these columns only for the exact published values.';
COMMENT ON COLUMN mart.suburb_trend.locality IS 'Suburb or locality name, uppercase. Joins to mart.suburb_price.locality.';
COMMENT ON COLUMN mart.suburb_trend.change_24_25_pct IS 'Published percentage change from the 2024 to the 2025 median. NULL where the source gives NA.';
COMMENT ON COLUMN mart.suburb_trend.growth_15_25_pct IS 'Published total percentage growth from the 2015 to the 2025 median. NULL where the source gives NA (usually no 2015 median).';
COMMENT ON COLUMN mart.suburb_trend.growth_pa_pct IS 'Published average annual percentage growth from 2015 to 2025. NULL where the source gives NA.';
