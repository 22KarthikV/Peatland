"""Database tests against a real PostGIS (docker-compose, localhost:5432).

A throwaway database `peatwatch_test_<random>` is created per module run, migrated with
Alembic, and dropped afterwards. The module is skipped when PostGIS is unreachable.
"""

import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from api.db.models import Classification, Composite, Model, Run, Site
from geoalchemy2.shape import to_shape
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

ADMIN_DSN = "postgresql://peatwatch:peatwatch@localhost:5432/peatwatch"
API_DIR = Path(__file__).resolve().parents[1]

TABLES = {"sites", "models", "runs", "composites", "classifications", "changes"}

EXPECTED_INDEXES = {
    "sites": {"ix_sites_boundary", "ix_sites_created_at", "uq_sites_slug"},
    "models": {"uq_models_name_version", "uq_models_is_default"},
    "runs": {
        "ix_runs_site_id_created_at",
        "ix_runs_status_created_at",
        "ix_runs_model_id",
        "uq_runs_active_idempotency_key",
    },
    "composites": {
        "uq_composites_idempotency_key",
        "uq_composites_cog_uri",
        "uq_composites_site_id_year_season",
        "ix_composites_site_id_date_start",
        "ix_composites_run_id",
    },
    "classifications": {
        "uq_classifications_idempotency_key",
        "uq_classifications_cog_uri",
        "uq_classifications_composite_id_model_id",
        "ix_classifications_model_id",
        "ix_classifications_run_id",
    },
    "changes": {
        "uq_changes_idempotency_key",
        "uq_changes_cog_uri",
        "uq_changes_from_to_composite_model",
        "ix_changes_site_id_created_at",
        "ix_changes_to_composite_id",
        "ix_changes_from_classification_id",
        "ix_changes_to_classification_id",
        "ix_changes_model_id",
        "ix_changes_run_id",
    },
}


def _admin_connect() -> psycopg.Connection[Any]:
    return psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=3)


@pytest.fixture(scope="module")
def db_url() -> Iterator[str]:
    try:
        admin = _admin_connect()
    except psycopg.Error as exc:
        pytest.skip(f"PostGIS not reachable on localhost:5432 (start docker compose): {exc}")
    name = f"peatwatch_test_{uuid.uuid4().hex[:10]}"
    try:
        admin.execute(f'CREATE DATABASE "{name}"')
    except psycopg.Error as exc:
        admin.close()
        pytest.skip(f"cannot create a temporary database (needs CREATEDB): {exc}")
    try:
        yield f"postgresql+psycopg://peatwatch:peatwatch@localhost:5432/{name}"
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def _alembic_config(url: str) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="module")
def engine(db_url: str) -> Iterator[Engine]:
    command.upgrade(_alembic_config(db_url), "head")
    eng = create_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def conn(engine: Engine) -> Iterator[Connection]:
    """A connection in a transaction that is rolled back after each test."""
    with engine.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


@pytest.fixture
def session(conn: Connection) -> Iterator[Session]:
    with Session(conn, join_transaction_mode="create_savepoint") as s:
        yield s


# ---- helpers ---------------------------------------------------------------------------------


def sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def square(x: float, y: float, size: float = 0.01) -> Polygon:
    return Polygon([(x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)])


def make_site(session: Session, slug: str | None = None) -> Site:
    slug = slug or f"site-{uuid.uuid4().hex[:8]}"
    site = Site(
        slug=slug,
        name=slug,
        boundary=f"SRID=4326;{MultiPolygon([square(-3.9, 58.4)]).wkt}",
        boundary_sha256=sha(slug),
        boundary_uri=f"s3://peatwatch-data/sites/{slug}/boundary.geojson",
    )
    session.add(site)
    session.flush()
    return site


def make_model(session: Session, name: str = "rf", version: str = "1.0.0") -> Model:
    model = Model(
        name=name,
        version=version,
        artifact_uri=f"s3://peatwatch-artifacts/models/{name}/{version}/model.onnx",
        git_commit="a1b2c3d",
        classes_contract=1,
    )
    session.add(model)
    session.flush()
    return model


def make_run(session: Session, site: Site, model: Model, key: str, **kw: Any) -> Run:
    run = Run(
        site_id=site.id, kind="pipeline", model_id=model.id, params={}, idempotency_key=key, **kw
    )
    session.add(run)
    session.flush()
    return run


def make_composite(
    session: Session, site: Site, season: str = "summer", tag: str = "a"
) -> Composite:
    comp = Composite(
        site_id=site.id,
        year=2023,
        season=season,
        date_start="2023-06-01",
        date_end="2023-08-31",
        cog_uri=f"s3://peatwatch-data/composites/{site.slug}/2023/{season}.tif",
        pipeline_version="pipeline 0.1.0 (a1b2c3d)",
        scene_ids=["S2A_1", "S2B_2"],
        cloud_stats={},
        parameters={},
        idempotency_key=sha(f"{site.slug}{season}{tag}"),
    )
    session.add(comp)
    session.flush()
    return comp


def assert_violation(session: Session, fn: Any, match: str | None = None) -> None:
    """Run fn inside a savepoint and expect an integrity error (optionally matching text)."""
    with pytest.raises(IntegrityError) as info, session.begin_nested():
        fn()
        session.flush()
    if match:
        assert match in str(info.value)


def now_ts() -> str:
    return "2024-01-01T00:00:00+00:00"


# ---- schema ----------------------------------------------------------------------------------


def test_extensions_tables_and_enums(engine: Engine) -> None:
    insp = inspect(engine)
    assert set(insp.get_table_names()) >= TABLES
    with engine.connect() as c:
        exts = set(c.execute(text("SELECT extname FROM pg_extension")).scalars())
        assert {"postgis", "pgcrypto"} <= exts
        kinds = list(c.execute(text("SELECT unnest(enum_range(NULL::run_kind))::text")).scalars())
        statuses = list(
            c.execute(text("SELECT unnest(enum_range(NULL::run_status))::text")).scalars()
        )
    assert kinds == ["pipeline", "change"]
    assert statuses == ["queued", "running", "succeeded", "failed", "cancelled"]


def test_indexes_exist(engine: Engine) -> None:
    with engine.connect() as c:
        rows = c.execute(
            text("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname='public'")
        ).all()
    by_table: dict[str, set[str]] = {}
    defs: dict[str, str] = {}
    for table, name, indexdef in rows:
        by_table.setdefault(table, set()).add(name)
        defs[name] = indexdef
    for table, expected in EXPECTED_INDEXES.items():
        assert expected <= by_table[table], f"{table}: missing {expected - by_table[table]}"
    assert "USING gist" in defs["ix_sites_boundary"]
    assert "WHERE is_default" in defs["uq_models_is_default"]
    active = defs["uq_runs_active_idempotency_key"]
    assert "UNIQUE" in active and "queued" in active and "running" in active


def test_geometry_column_and_generated_area(engine: Engine) -> None:
    with engine.connect() as c:
        geom = c.execute(
            text(
                "SELECT type, srid FROM geometry_columns "
                "WHERE f_table_name='sites' AND f_geometry_column='boundary'"
            )
        ).one()
        generated = c.execute(
            text(
                "SELECT is_generated FROM information_schema.columns "
                "WHERE table_name='sites' AND column_name='area_ha'"
            )
        ).scalar_one()
    assert (geom[0].upper(), geom[1]) == ("MULTIPOLYGON", 4326)
    assert generated == "ALWAYS"


def test_all_foreign_keys_restrict(engine: Engine) -> None:
    with engine.connect() as c:
        rules = set(
            c.execute(
                text("SELECT delete_rule FROM information_schema.referential_constraints")
            ).scalars()
        )
    assert rules == {"RESTRICT"}


# ---- sites -----------------------------------------------------------------------------------


def test_site_multipolygon_roundtrip_and_area(session: Session) -> None:
    site = make_site(session)
    session.expire_all()
    loaded = session.get(Site, site.id)
    assert loaded is not None
    geom = to_shape(loaded.boundary)
    assert isinstance(geom, MultiPolygon)
    assert geom.equals(MultiPolygon([square(-3.9, 58.4)]))
    # 0.01 deg square at 58.4 N is roughly 0.58 km x 1.11 km = ~64 ha
    assert 50 < float(loaded.area_ha) < 80
    assert loaded.created_at is not None


def test_site_rejects_polygon_invalid_and_bad_values(session: Session) -> None:
    make_site(session, "dup-site")
    assert_violation(session, lambda: make_site(session, "dup-site"), "uq_sites_slug")
    assert_violation(session, lambda: make_site(session, "Bad_Slug"), "ck_sites_slug_format")

    def bowtie() -> None:
        s = make_site(session)
        session.execute(
            text("UPDATE sites SET boundary = ST_Multi(ST_GeomFromText(:w, 4326)) WHERE id = :i"),
            {"w": "POLYGON((0 0, 1 1, 1 0, 0 1, 0 0))", "i": s.id},
        )

    assert_violation(session, bowtie, "ck_sites_boundary_valid")

    # A non-polygonal geometry is rejected by the column type (the API promotes Polygon with
    # ST_Multi before insert; PostGIS itself may or may not coerce, so only Point is asserted).
    with pytest.raises(DBAPIError), session.begin_nested():
        session.execute(
            text(
                "INSERT INTO sites (slug, name, boundary, boundary_sha256, boundary_uri) VALUES "
                "('pt-only', 'n', ST_GeomFromText('POINT(0 0)', 4326), :h, 'u')"
            ),
            {"h": sha("x")},
        )

    def bad_hash() -> None:
        s = make_site(session)
        s.boundary_sha256 = "ABC"
        session.flush()

    assert_violation(session, bad_hash, "boundary_sha256_hex")


# ---- models ----------------------------------------------------------------------------------


def test_model_constraints_and_single_default(session: Session) -> None:
    a = make_model(session, "rf", "1")
    a.is_default = True
    session.flush()
    assert_violation(session, lambda: make_model(session, "rf", "1"), "uq_models_name_version")
    assert_violation(session, lambda: make_model(session, "Bad Name", "1"), "ck_models_name_format")

    def second_default() -> None:
        b = make_model(session, "unet", "2")
        b.is_default = True

    assert_violation(session, second_default, "uq_models_is_default")
    c = make_model(session, "ndvi_threshold", "1")
    assert c.is_default is False
    assert c.metrics == {}


# ---- runs ------------------------------------------------------------------------------------


def test_run_defaults(session: Session) -> None:
    site, model = make_site(session), make_model(session)
    run = make_run(session, site, model, sha("r"))
    session.refresh(run)
    assert run.status == "queued"
    assert run.progress == {} and run.outputs == {}
    assert isinstance(run.id, uuid.UUID)


def test_run_active_idempotency_key_partial_unique(session: Session) -> None:
    site, model = make_site(session), make_model(session)
    key = sha("same")
    first = make_run(session, site, model, key)
    assert_violation(session, lambda: make_run(session, site, model, key), "uq_runs_active")

    # Still unique while running.
    first.status = "running"
    first.started_at = now_ts()  # type: ignore[assignment]
    session.flush()
    assert_violation(session, lambda: make_run(session, site, model, key), "uq_runs_active")

    # Once terminal, a new run with the same key is allowed (retry), and several terminal rows too.
    first.status = "succeeded"
    first.finished_at = now_ts()  # type: ignore[assignment]
    session.flush()
    second = make_run(session, site, model, key)
    assert second.status == "queued"
    second.status = "failed"
    second.error = {"code": "x", "message": "m", "detail": None}
    second.finished_at = now_ts()  # type: ignore[assignment]
    session.flush()
    make_run(session, site, model, key)


def test_run_check_constraints(session: Session) -> None:
    site, model = make_site(session), make_model(session)

    assert_violation(
        session,
        lambda: make_run(session, site, model, sha("1"), status="running"),
        "started_when_running",
    )
    assert_violation(
        session,
        lambda: make_run(session, site, model, sha("2"), status="failed", finished_at=now_ts()),
        "error_iff_failed",
    )
    assert_violation(
        session,
        lambda: make_run(session, site, model, sha("3"), error={"code": "x"}),
        "error_iff_failed",
    )
    assert_violation(
        session,
        lambda: make_run(session, site, model, sha("4"), status="succeeded", started_at=now_ts()),
        "finished_iff_terminal",
    )
    assert_violation(
        session,
        lambda: make_run(
            session,
            site,
            model,
            sha("5"),
            status="succeeded",
            started_at="2024-01-02T00:00:00+00:00",
            finished_at="2024-01-01T00:00:00+00:00",
        ),
        "finished_after_started",
    )
    assert_violation(
        session, lambda: make_run(session, site, model, "nothex"), "idempotency_key_hex"
    )

    # failed and cancelled may skip started_at when coming straight from queued.
    make_run(
        session,
        site,
        model,
        sha("6"),
        status="cancelled",
        finished_at=now_ts(),
    )
    make_run(
        session,
        site,
        model,
        sha("7"),
        status="failed",
        error={"code": "enqueue_failed", "message": "m", "detail": None},
        finished_at=now_ts(),
    )


def test_run_state_machine_trigger(session: Session) -> None:
    site, model = make_site(session), make_model(session)
    run = make_run(session, site, model, sha("sm"))

    # queued -> succeeded is not allowed
    def skip() -> None:
        session.execute(
            text(
                "UPDATE runs SET status='succeeded', started_at=now(), "
                "finished_at=now() WHERE id=:i"
            ),
            {"i": run.id},
        )

    assert_violation(session, skip, "invalid run status transition")

    session.execute(
        text("UPDATE runs SET status='running', started_at=now(), heartbeat_at=now() WHERE id=:i"),
        {"i": run.id},
    )
    session.execute(
        text("UPDATE runs SET status='succeeded', finished_at=now() WHERE id=:i"), {"i": run.id}
    )

    # terminal states never leave
    for target in ("queued", "running", "failed", "cancelled"):

        def leave(target: str = target) -> None:
            session.execute(
                text("UPDATE runs SET status = CAST(:s AS run_status) WHERE id=:i"),
                {"s": target, "i": run.id},
            )

        assert_violation(session, leave, "invalid run status transition")

    # compare-and-set claim: second claim updates zero rows
    other = make_run(session, site, model, sha("cas"))
    claim = text(
        "UPDATE runs SET status='running', started_at=now(), heartbeat_at=now() "
        "WHERE id=:i AND status='queued' RETURNING id"
    )
    assert session.execute(claim, {"i": other.id}).first() is not None
    assert session.execute(claim, {"i": other.id}).first() is None


# ---- composites / classifications / changes / FK ---------------------------------------------


def test_composite_constraints(session: Session) -> None:
    site = make_site(session)
    comp = make_composite(session, site)
    assert comp.scene_ids == ["S2A_1", "S2B_2"]

    assert_violation(session, lambda: make_composite(session, site), "uq_composites")
    make_composite(session, site, "w20230601-20230630")
    assert_violation(session, lambda: make_composite(session, site, "monsoon"), "season_format")

    def bad_dates() -> None:
        c = make_composite(session, site, "winter")
        c.date_end = "2022-01-01"  # type: ignore[assignment]
        session.flush()

    assert_violation(session, bad_dates, "dates_ordered")

    def bad_year() -> None:
        c = make_composite(session, site, "autumn")
        c.year = 2014
        session.flush()

    assert_violation(session, bad_year, "year_range")

    def dup_key() -> None:
        c = make_composite(session, site, "annual")
        c.idempotency_key = comp.idempotency_key
        session.flush()

    assert_violation(session, dup_key, "uq_composites_idempotency_key")


def test_classification_and_change_constraints(session: Session) -> None:
    site, model = make_site(session), make_model(session)
    c1 = make_composite(session, site, "summer", "x")
    c2 = make_composite(session, site, "autumn", "y")

    def classify(comp: Composite, tag: str) -> Classification:
        cl = Classification(
            composite_id=comp.id,
            model_id=model.id,
            cog_uri=f"s3://peatwatch-data/classifications/{tag}.tif",
            class_areas_ha={"1": 1.5},
            idempotency_key=sha(f"cl{tag}"),
        )
        session.add(cl)
        session.flush()
        return cl

    cl1, cl2 = classify(c1, "1"), classify(c2, "2")
    assert_violation(session, lambda: classify(c1, "3"), "uq_classifications_composite_id_model_id")

    def change(frm: Composite, to: Composite, tag: str) -> None:
        session.execute(
            text(
                "INSERT INTO changes (site_id, from_composite_id, to_composite_id, "
                "from_classification_id, to_classification_id, model_id, cog_uri, stats, "
                "idempotency_key) VALUES (:s, :f, :t, :fc, :tc, :m, :u, '{}'::jsonb, :k)"
            ),
            {
                "s": site.id,
                "f": frm.id,
                "t": to.id,
                "fc": cl1.id,
                "tc": cl2.id,
                "m": model.id,
                "u": f"s3://peatwatch-data/changes/{tag}.tif",
                "k": sha(f"ch{tag}"),
            },
        )

    assert_violation(session, lambda: change(c1, c1, "same"), "ck_changes_distinct_composites")
    change(c1, c2, "ok")
    assert_violation(session, lambda: change(c1, c2, "again"), "uq_changes_from_to_composite_model")


def test_foreign_keys_restrict_deletes(session: Session) -> None:
    site, model = make_site(session), make_model(session)
    run = make_run(session, site, model, sha("fk"))
    comp = make_composite(session, site)
    comp.run_id = run.id
    session.flush()

    for table, ident in (("runs", run.id), ("models", model.id)):

        def delete(table: str = table, ident: uuid.UUID = ident) -> None:
            session.execute(text(f"DELETE FROM {table} WHERE id = :i"), {"i": ident})

        assert_violation(session, delete, "violates foreign key")

    assert_violation(
        session,
        lambda: session.execute(text("DELETE FROM sites WHERE id = :i"), {"i": site.id}),
        "violates foreign key",
    )

    # Without dependants the delete works.
    lonely = make_site(session)
    session.execute(text("DELETE FROM sites WHERE id = :i"), {"i": lonely.id})


# ---- downgrade (must stay last: it tears the schema down) ------------------------------------


def test_downgrade_base_removes_everything_then_upgrade_again(engine: Engine, db_url: str) -> None:
    cfg = _alembic_config(db_url)
    command.downgrade(cfg, "base")
    insp = inspect(engine)
    remaining = TABLES & set(insp.get_table_names())
    assert not remaining
    with engine.connect() as c:
        types = set(
            c.execute(
                text("SELECT typname FROM pg_type WHERE typname IN ('run_kind', 'run_status')")
            ).scalars()
        )
        funcs = set(
            c.execute(
                text("SELECT proname FROM pg_proc WHERE proname = 'runs_enforce_status_transition'")
            ).scalars()
        )
    assert not types and not funcs
    command.upgrade(cfg, "head")
    assert set(inspect(engine).get_table_names()) >= TABLES
