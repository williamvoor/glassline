-- Schemas and the read-only role's access. Roles themselves are created by the migration runner
-- (their passwords come from the environment); this file runs as vicprop_loader.
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS ops;

-- explorer_ro sees schema mart only, including tables created later.
GRANT USAGE ON SCHEMA mart TO explorer_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE vicprop_loader IN SCHEMA mart GRANT SELECT ON TABLES TO explorer_ro;
