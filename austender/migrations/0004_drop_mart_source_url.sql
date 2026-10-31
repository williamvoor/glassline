-- The API never supplies a notice URL (checked on a full live month: 0 of 4,463 releases), so the column
-- is always NULL and only adds noise to the agent's prompt.
ALTER TABLE mart.contract DROP COLUMN source_url;
