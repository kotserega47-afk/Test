-- Wallet Editor Registry PostgreSQL mirror — Phase 1 Stage A
-- Apply with: psql $DATABASE_URL -f integrations/wallet_editor_registry_db/schema.sql
-- Operator sheets (hold / partner cooldown) intentionally excluded from mirror tables

CREATE TABLE IF NOT EXISTS we_registry_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS we_registry_runs (
    run_id            TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL,
    finished_at       TEXT NOT NULL,
    input_rows        INTEGER NOT NULL DEFAULT 0,
    success_rows      INTEGER NOT NULL DEFAULT 0,
    failed_rows       INTEGER NOT NULL DEFAULT 0,
    skipped_rows      INTEGER NOT NULL DEFAULT 0,
    output_file       TEXT,
    operator_profile  TEXT,
    source            TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS we_registry_runs_started_at_idx
    ON we_registry_runs (started_at);

CREATE TABLE IF NOT EXISTS we_registry_results (
    id                BIGSERIAL PRIMARY KEY,
    run_id            TEXT REFERENCES we_registry_runs (run_id) ON DELETE SET NULL,
    operation_date    TEXT,
    disable_at        TEXT,
    reenable_date     TEXT,
    enable_status     TEXT,
    vklyucheno        TEXT,
    enable_comment    TEXT,
    card              TEXT NOT NULL,
    partner           TEXT NOT NULL DEFAULT '',
    action            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT '',
    comment           TEXT,
    hold_mark         TEXT,
    row_fingerprint   TEXT NOT NULL,
    source_row_index  INTEGER,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT we_registry_results_row_fingerprint_key UNIQUE (row_fingerprint)
);

CREATE INDEX IF NOT EXISTS we_registry_results_run_id_idx
    ON we_registry_results (run_id);

CREATE INDEX IF NOT EXISTS we_registry_results_card_partner_idx
    ON we_registry_results (card, partner);

CREATE INDEX IF NOT EXISTS we_registry_results_enable_status_idx
    ON we_registry_results (enable_status)
    WHERE action = 'remove_partner' AND status = 'OK';

CREATE TABLE IF NOT EXISTS we_registry_reconcile (
    id            BIGSERIAL PRIMARY KEY,
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    entity_type   TEXT NOT NULL CHECK (entity_type IN ('results', 'runs', 'aggregate')),
    entity_key    TEXT NOT NULL,
    excel_hash    TEXT,
    db_hash       TEXT,
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'ignored')),
    details       TEXT
);

CREATE INDEX IF NOT EXISTS we_registry_reconcile_status_idx
    ON we_registry_reconcile (status, detected_at DESC);

INSERT INTO we_registry_meta (key, value, updated_at)
VALUES ('schema_version', '1', now())
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    updated_at = EXCLUDED.updated_at;
