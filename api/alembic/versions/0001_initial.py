"""Initial schema (contracts/db-schema.md v1).

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

HEX64 = "'^[0-9a-f]{64}$'"

STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS postgis",
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
    "CREATE TYPE run_kind AS ENUM ('pipeline', 'change')",
    "CREATE TYPE run_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
    f"""
    CREATE TABLE sites (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        slug text NOT NULL,
        name text NOT NULL,
        boundary geometry(MultiPolygon, 4326) NOT NULL,
        area_ha numeric(12, 2) GENERATED ALWAYS AS
            ((ST_Area(ST_Transform(boundary, 32630)) / 10000)::numeric(12,2)) STORED,
        boundary_sha256 char(64) NOT NULL,
        boundary_uri text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_sites PRIMARY KEY (id),
        CONSTRAINT uq_sites_slug UNIQUE (slug),
        CONSTRAINT ck_sites_slug_format CHECK (slug ~ '^[a-z0-9][a-z0-9-]{{1,62}}$'),
        CONSTRAINT ck_sites_name_length CHECK (length(name) BETWEEN 1 AND 200),
        CONSTRAINT ck_sites_boundary_valid CHECK (ST_IsValid(boundary)),
        CONSTRAINT ck_sites_boundary_sha256_hex CHECK (boundary_sha256 ~ {HEX64})
    )
    """,
    "CREATE INDEX ix_sites_boundary ON sites USING gist (boundary)",
    "CREATE INDEX ix_sites_created_at ON sites (created_at DESC)",
    """
    CREATE TABLE models (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        name text NOT NULL,
        version text NOT NULL,
        metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
        artifact_uri text NOT NULL,
        model_card_uri text,
        git_commit text NOT NULL,
        classes_contract smallint NOT NULL,
        is_default boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_models PRIMARY KEY (id),
        CONSTRAINT uq_models_name_version UNIQUE (name, version),
        CONSTRAINT ck_models_name_format CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,31}$'),
        CONSTRAINT ck_models_version_format CHECK (version ~ '^[0-9A-Za-z._-]{1,32}$'),
        CONSTRAINT ck_models_git_commit_format CHECK (git_commit ~ '^[0-9a-f]{7,40}$')
    )
    """,
    "CREATE UNIQUE INDEX uq_models_is_default ON models (is_default) WHERE is_default",
    f"""
    CREATE TABLE runs (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        site_id uuid NOT NULL,
        kind run_kind NOT NULL,
        status run_status NOT NULL DEFAULT 'queued',
        model_id uuid NOT NULL,
        params jsonb NOT NULL,
        idempotency_key char(64) NOT NULL,
        progress jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        outputs jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        error jsonb,
        log_uri text,
        created_at timestamptz NOT NULL DEFAULT now(),
        started_at timestamptz,
        finished_at timestamptz,
        heartbeat_at timestamptz,
        CONSTRAINT pk_runs PRIMARY KEY (id),
        CONSTRAINT fk_runs_site_id_sites FOREIGN KEY (site_id)
            REFERENCES sites (id) ON DELETE RESTRICT,
        CONSTRAINT fk_runs_model_id_models FOREIGN KEY (model_id)
            REFERENCES models (id) ON DELETE RESTRICT,
        CONSTRAINT ck_runs_idempotency_key_hex CHECK (idempotency_key ~ {HEX64}),
        CONSTRAINT ck_runs_error_iff_failed CHECK ((status = 'failed') = (error IS NOT NULL)),
        CONSTRAINT ck_runs_started_when_running
            CHECK (status NOT IN ('running', 'succeeded') OR started_at IS NOT NULL),
        CONSTRAINT ck_runs_finished_iff_terminal
            CHECK ((status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)),
        CONSTRAINT ck_runs_finished_after_started
            CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
    )
    """,
    "CREATE INDEX ix_runs_site_id_created_at ON runs (site_id, created_at DESC)",
    "CREATE INDEX ix_runs_status_created_at ON runs (status, created_at)",
    "CREATE INDEX ix_runs_model_id ON runs (model_id)",
    """
    CREATE UNIQUE INDEX uq_runs_active_idempotency_key ON runs (idempotency_key)
        WHERE status IN ('queued', 'running')
    """,
    # State machine: only the transitions in db-schema.md section 4 are allowed.
    """
    CREATE FUNCTION runs_enforce_status_transition() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.status = OLD.status THEN
            RETURN NEW;
        END IF;
        IF (OLD.status, NEW.status) IN (
            ('queued', 'running'), ('queued', 'failed'), ('queued', 'cancelled'),
            ('running', 'succeeded'), ('running', 'failed'), ('running', 'cancelled')
        ) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'invalid run status transition % -> %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END
    $$
    """,
    """
    CREATE TRIGGER trg_runs_status_transition BEFORE UPDATE OF status ON runs
        FOR EACH ROW EXECUTE FUNCTION runs_enforce_status_transition()
    """,
    f"""
    CREATE TABLE composites (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        site_id uuid NOT NULL,
        year smallint NOT NULL,
        season text NOT NULL,
        date_start date NOT NULL,
        date_end date NOT NULL,
        cog_uri text NOT NULL,
        pipeline_version text NOT NULL,
        scene_ids text[] NOT NULL,
        cloud_stats jsonb NOT NULL,
        parameters jsonb NOT NULL,
        idempotency_key char(64) NOT NULL,
        run_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_composites PRIMARY KEY (id),
        CONSTRAINT fk_composites_site_id_sites FOREIGN KEY (site_id)
            REFERENCES sites (id) ON DELETE RESTRICT,
        CONSTRAINT fk_composites_run_id_runs FOREIGN KEY (run_id)
            REFERENCES runs (id) ON DELETE RESTRICT,
        CONSTRAINT ck_composites_year_range CHECK (year BETWEEN 2015 AND 2100),
        CONSTRAINT ck_composites_season_format CHECK (
            season ~ '^(spring|summer|autumn|winter|annual|w[0-9]{{8}}-[0-9]{{8}})$'),
        CONSTRAINT ck_composites_dates_ordered CHECK (date_end >= date_start),
        CONSTRAINT ck_composites_idempotency_key_hex CHECK (idempotency_key ~ {HEX64}),
        CONSTRAINT uq_composites_idempotency_key UNIQUE (idempotency_key),
        CONSTRAINT uq_composites_cog_uri UNIQUE (cog_uri),
        CONSTRAINT uq_composites_site_id_year_season UNIQUE (site_id, year, season)
    )
    """,
    "CREATE INDEX ix_composites_site_id_date_start ON composites (site_id, date_start)",
    "CREATE INDEX ix_composites_run_id ON composites (run_id)",
    f"""
    CREATE TABLE classifications (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        composite_id uuid NOT NULL,
        model_id uuid NOT NULL,
        cog_uri text NOT NULL,
        class_areas_ha jsonb NOT NULL,
        idempotency_key char(64) NOT NULL,
        run_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_classifications PRIMARY KEY (id),
        CONSTRAINT fk_classifications_composite_id_composites FOREIGN KEY (composite_id)
            REFERENCES composites (id) ON DELETE RESTRICT,
        CONSTRAINT fk_classifications_model_id_models FOREIGN KEY (model_id)
            REFERENCES models (id) ON DELETE RESTRICT,
        CONSTRAINT fk_classifications_run_id_runs FOREIGN KEY (run_id)
            REFERENCES runs (id) ON DELETE RESTRICT,
        CONSTRAINT ck_classifications_idempotency_key_hex CHECK (idempotency_key ~ {HEX64}),
        CONSTRAINT uq_classifications_idempotency_key UNIQUE (idempotency_key),
        CONSTRAINT uq_classifications_cog_uri UNIQUE (cog_uri),
        CONSTRAINT uq_classifications_composite_id_model_id UNIQUE (composite_id, model_id)
    )
    """,
    "CREATE INDEX ix_classifications_model_id ON classifications (model_id)",
    "CREATE INDEX ix_classifications_run_id ON classifications (run_id)",
    f"""
    CREATE TABLE changes (
        id uuid NOT NULL DEFAULT gen_random_uuid(),
        site_id uuid NOT NULL,
        from_composite_id uuid NOT NULL,
        to_composite_id uuid NOT NULL,
        from_classification_id uuid NOT NULL,
        to_classification_id uuid NOT NULL,
        model_id uuid NOT NULL,
        cog_uri text NOT NULL,
        stats jsonb NOT NULL,
        stats_csv_uri text,
        idempotency_key char(64) NOT NULL,
        run_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_changes PRIMARY KEY (id),
        CONSTRAINT fk_changes_site_id_sites FOREIGN KEY (site_id)
            REFERENCES sites (id) ON DELETE RESTRICT,
        CONSTRAINT fk_changes_from_composite_id_composites FOREIGN KEY (from_composite_id)
            REFERENCES composites (id) ON DELETE RESTRICT,
        CONSTRAINT fk_changes_to_composite_id_composites FOREIGN KEY (to_composite_id)
            REFERENCES composites (id) ON DELETE RESTRICT,
        CONSTRAINT fk_changes_from_classification_id_classifications
            FOREIGN KEY (from_classification_id)
            REFERENCES classifications (id) ON DELETE RESTRICT,
        CONSTRAINT fk_changes_to_classification_id_classifications
            FOREIGN KEY (to_classification_id)
            REFERENCES classifications (id) ON DELETE RESTRICT,
        CONSTRAINT fk_changes_model_id_models FOREIGN KEY (model_id)
            REFERENCES models (id) ON DELETE RESTRICT,
        CONSTRAINT fk_changes_run_id_runs FOREIGN KEY (run_id)
            REFERENCES runs (id) ON DELETE RESTRICT,
        CONSTRAINT ck_changes_distinct_composites CHECK (from_composite_id <> to_composite_id),
        CONSTRAINT ck_changes_idempotency_key_hex CHECK (idempotency_key ~ {HEX64}),
        CONSTRAINT uq_changes_idempotency_key UNIQUE (idempotency_key),
        CONSTRAINT uq_changes_cog_uri UNIQUE (cog_uri),
        CONSTRAINT uq_changes_from_to_composite_model
            UNIQUE (from_composite_id, to_composite_id, model_id)
    )
    """,
    "CREATE INDEX ix_changes_site_id_created_at ON changes (site_id, created_at DESC)",
    "CREATE INDEX ix_changes_to_composite_id ON changes (to_composite_id)",
    "CREATE INDEX ix_changes_from_classification_id ON changes (from_classification_id)",
    "CREATE INDEX ix_changes_to_classification_id ON changes (to_classification_id)",
    "CREATE INDEX ix_changes_model_id ON changes (model_id)",
    "CREATE INDEX ix_changes_run_id ON changes (run_id)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for table in ("changes", "classifications", "composites", "runs", "models", "sites"):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION runs_enforce_status_transition()")
    op.execute("DROP TYPE run_status")
    op.execute("DROP TYPE run_kind")
    # Extensions are left in place: they may be shared with other objects.
