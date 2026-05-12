-- Film & TV Production Tax Incentive Database
-- SQLite schema. Currency stays in local units; never store USD conversions.
-- All monetary fields are stored as REAL (currency unit defined per jurisdiction).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jurisdictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    country         TEXT NOT NULL,
    region          TEXT,
    city            TEXT,
    display_name    TEXT NOT NULL UNIQUE,
    currency        TEXT NOT NULL CHECK (length(currency) = 3),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jurisdictions_country ON jurisdictions(country);

CREATE TABLE IF NOT EXISTS incentive_programs (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    jurisdiction_id             INTEGER NOT NULL REFERENCES jurisdictions(id) ON DELETE CASCADE,
    program_name                TEXT NOT NULL,
    incentive_type              TEXT NOT NULL CHECK (incentive_type IN (
                                    'tax_credit_transferable',
                                    'tax_credit_refundable',
                                    'tax_credit_non_refundable',
                                    'cash_rebate',
                                    'grant',
                                    'combined'
                                )),
    headline_rate_pct           REAL NOT NULL CHECK (headline_rate_pct >= 0 AND headline_rate_pct <= 100),
    rate_details                TEXT,                       -- prose or JSON describing rate composition / uplifts
    minimum_spend               REAL,                       -- in jurisdiction currency
    minimum_spend_notes         TEXT,
    cap_per_project             REAL,                       -- in jurisdiction currency
    annual_program_cap          REAL,                       -- in jurisdiction currency
    atl_eligible                INTEGER NOT NULL CHECK (atl_eligible IN (0, 1)),
    atl_cap_notes               TEXT,
    qualifying_spend_summary    TEXT,
    non_qualifying_spend        TEXT,
    application_process         TEXT,
    payment_timing              TEXT,
    sunset_date                 TEXT,                       -- ISO 8601 date, nullable
    last_verified_date          TEXT NOT NULL,              -- ISO 8601 date
    notes                       TEXT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                  TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE (jurisdiction_id, program_name),

    -- If minimum_spend is set, minimum_spend_notes must be set too.
    CHECK (minimum_spend IS NULL OR minimum_spend_notes IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_programs_jurisdiction ON incentive_programs(jurisdiction_id);
CREATE INDEX IF NOT EXISTS idx_programs_atl ON incentive_programs(atl_eligible);
CREATE INDEX IF NOT EXISTS idx_programs_rate ON incentive_programs(headline_rate_pct);

CREATE TABLE IF NOT EXISTS sources (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    incentive_program_id    INTEGER NOT NULL REFERENCES incentive_programs(id) ON DELETE CASCADE,
    url                     TEXT NOT NULL,
    source_title            TEXT NOT NULL,
    source_type             TEXT NOT NULL CHECK (source_type IN (
                                'official_government',
                                'film_office',
                                'industry_publication',
                                'legal_summary'
                            )),
    accessed_date           TEXT NOT NULL,                   -- ISO 8601 date
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sources_program ON sources(incentive_program_id);
CREATE INDEX IF NOT EXISTS idx_sources_type ON sources(source_type);

CREATE TABLE IF NOT EXISTS change_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    incentive_program_id    INTEGER NOT NULL REFERENCES incentive_programs(id) ON DELETE CASCADE,
    changed_date            TEXT NOT NULL,                   -- ISO 8601 date
    field_changed           TEXT NOT NULL,
    old_value               TEXT,
    new_value               TEXT,
    change_reason           TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_changelog_program ON change_log(incentive_program_id);
CREATE INDEX IF NOT EXISTS idx_changelog_date ON change_log(changed_date);
