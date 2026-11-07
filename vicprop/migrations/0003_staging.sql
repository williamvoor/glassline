-- staging: the workbook as dataload loads it (load.toml). vicprop_loader owns it; explorer_ro gets
-- no USAGE, so it can't see it. transforms/ refills mart from it.
CREATE SCHEMA IF NOT EXISTS staging;
