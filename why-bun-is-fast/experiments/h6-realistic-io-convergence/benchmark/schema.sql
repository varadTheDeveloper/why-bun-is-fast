-- H6 benchmark schema. Deliberately minimal: one table, one indexed lookup column.
-- Chosen to keep the query's own cost small and predictable (a single indexed
-- point lookup) so the experiment measures "does the runtime path change with
-- an I/O step present", not "how does this runtime handle a heavy query".
DROP TABLE IF EXISTS accounts;

CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    balance_cents BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

-- Seed 10,000 deterministic rows (no random() — reproducibility requirement).
INSERT INTO accounts (id, name, email, balance_cents, created_at)
SELECT
    i,
    'user_' || i,
    'user_' || i || '@example.test',
    (i * 137) % 1000000,
    TIMESTAMP '2024-01-01 00:00:00' + (i || ' seconds')::interval
FROM generate_series(1, 10000) AS i;

-- The benchmark query always targets id = 42 (fixed, not random) across every
-- runtime and every run, per the protocol's "fixed" requirement.
