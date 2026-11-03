"""Training-data builder on synthetic grids and vectors. No network: fetchers get fakes."""

import json
import urllib.error
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

from ml import build_data as bd

E0, N0 = 410000.0, 6090000.0  # inside galloway-south (about -4.37, 54.96)
TF = from_origin(E0, N0, 10, 10)
SIZE = 40
CRS = "EPSG:32630"


def _rect(c0: int, r0: int, c1: int, r1: int) -> Polygon:
    return box(E0 + c0 * 10, N0 - r1 * 10, E0 + c1 * 10, N0 - r0 * 10)


def _write_composite(path: Path, bands: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=SIZE,
        height=SIZE,
        count=bands,
        dtype="float32",
        crs=CRS,
        transform=TF,
        nodata=float("nan"),
    ) as dst:
        dst.write(np.ones((bands, SIZE, SIZE), dtype=np.float32))
    return path


def _nfi(rows: list[tuple[str, Polygon]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"IFT_IOA": [r[0] for r in rows]}, geometry=[r[1] for r in rows], crs=CRS
    )


def _cpm(rows: list[tuple[int, Polygon]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"importance": [r[0] for r in rows]}, geometry=[r[1] for r in rows], crs=CRS
    )


# ----------------------------------------------------------------------------- regions


def test_regions_are_outside_holdout_with_buffer() -> None:
    for name, bbox in bd.REGIONS.items():
        assert bd.check_outside_holdout(bbox) >= 1000.0, name


def test_bbox_inside_or_touching_holdout_is_rejected() -> None:
    with pytest.raises(ValueError, match="hold-out"):
        bd.check_outside_holdout((-4.50, 55.10, -4.35, 55.20))
    # 500 m from the box is closer than the 1 km buffer
    with pytest.raises(ValueError, match="at least"):
        bd.check_outside_holdout((-4.80, 55.05, -4.5575, 55.25))
    with pytest.raises(ValueError, match="west < east"):
        bd.check_outside_holdout((-4.2, 55.0, -4.4, 54.9))


def test_boundary_geojson_is_a_closed_ring() -> None:
    ring = bd.boundary_geojson((-4.5, 54.9, -4.2, 55.0))["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 5


# ------------------------------------------------------------------------------ groups


@pytest.mark.parametrize(
    ("value", "group"),
    [
        ("Conifer", "woodland"),
        (" mixed mainly broadleaved ", "woodland"),
        ("Felled", "felled"),
        ("Ground prep", "felled"),
        ("Windblow", "felled"),
        ("Young trees", "uncertain"),
        ("Assumed woodland", "uncertain"),
        ("Something new", "uncertain"),
        (None, "uncertain"),
        ("Road", "hard_other"),
        ("Grassland", "soft_other"),
        ("Other vegetation", "neutral"),
    ],
)
def test_nfi_group(value: Any, group: str) -> None:
    assert bd.nfi_group(value) == group


@pytest.mark.parametrize(
    ("value", "group"),
    [
        (1, "bog"),
        (2.0, "bog"),
        ("1", "bog"),
        (0, "other"),
        (3, "other"),
        (4, "other"),
        (-2, "other"),
        (5, "ambiguous"),
        (-1, "ambiguous"),
        (None, "ambiguous"),
        (float("nan"), "ambiguous"),
    ],
)
def test_cpm_group(value: Any, group: str) -> None:
    assert bd.cpm_group(value) == group


def test_combine_truth_table() -> None:
    n, c = bd.NFI_CODES, bd.CPM_CODES
    none = bd.NODATA
    # columns: nfi, cpm -> expected class id (1 bog, 2 wood, 3 felled, 0 other, 255 unlabelled)
    cases = [
        (none, c["bog"], 1),
        (none, c["other"], 0),
        (none, none, 0),  # no CPM polygon: not bog
        (none, c["ambiguous"], 255),
        (bd.NFI_NEUTRAL_CODE, c["bog"], 1),
        (n["woodland"], c["bog"], 2),  # plantation on mapped peat is woodland
        (n["woodland"], c["other"], 2),
        (n["felled"], c["bog"], 3),
        (n["felled"], c["ambiguous"], 3),
        (n["uncertain"], c["bog"], 255),
        (n["uncertain"], c["other"], 255),
        (n["hard_other"], c["bog"], 0),
        (n["soft_other"], c["bog"], 255),  # NFI grass/bare vs CPM bog: sources disagree
        (n["soft_other"], c["ambiguous"], 255),
        (n["soft_other"], c["other"], 0),
        (n["soft_other"], none, 0),
    ]
    nfi = np.array([[x[0] for x in cases]], dtype=np.uint8)
    cpm = np.array([[x[1] for x in cases]], dtype=np.uint8)
    out = bd.combine_sources(nfi, cpm)
    assert out[0].tolist() == [x[2] for x in cases]
    with pytest.raises(ValueError, match="same shape"):
        bd.combine_sources(nfi, cpm[:, :2])


# ---------------------------------------------------------------------- label raster


def test_build_label_raster_on_exact_grid(tmp_path: Path) -> None:
    comp = _write_composite(tmp_path / "c.tif", bands=18)
    nfi = _nfi(
        [
            ("Conifer", _rect(0, 0, 20, 10)),
            ("Felled", _rect(0, 10, 20, 20)),
            ("Young trees", _rect(0, 20, 20, 30)),
            ("Grassland", _rect(20, 20, 40, 30)),
        ]
    )
    cpm = _cpm([(1, _rect(0, 0, 40, 40)), (4, _rect(0, 30, 40, 40))])
    # cpm polygons overlap on rows 30-40; rows get bog from the first, other from the second
    cpm = _cpm([(1, _rect(0, 0, 40, 30)), (4, _rect(0, 30, 40, 40))])
    res = bd.build_label_raster(
        comp, nfi, cpm, tmp_path / "l.tif", exclusions=[], exclusion_buffer_m=0.0
    )
    with rasterio.open(res.path) as lab, rasterio.open(comp) as src:
        assert lab.transform == src.transform and lab.crs == src.crs
        assert (lab.height, lab.width) == (src.height, src.width)
        assert lab.dtypes == ("uint8",) and lab.nodata == 255
        arr = lab.read(1)
    assert (arr[0:10, 0:20] == 2).all()  # conifer on bog
    assert (arr[10:20, 0:20] == 3).all()  # felled
    assert (arr[20:30, 0:20] == 255).all()  # young trees
    assert (arr[20:30, 20:40] == 255).all()  # grassland vs CPM bog
    assert (arr[0:20, 20:40] == 1).all()  # open bog
    assert (arr[30:40, :] == 0).all()  # CPM class 4
    assert res.counts["woodland"] == 200 and res.counts["bog"] == 400
    assert res.counts["unlabelled"] == 400
    assert res.labelled_fraction == pytest.approx(0.75)


def test_invalid_polygons_are_repaired_not_fatal(tmp_path: Path) -> None:
    # A self-intersecting "bowtie" (real NFI/CPM data has some); ml.labels used to crash on it.
    bowtie = Polygon([(E0, N0), (E0 + 200, N0 - 200), (E0 + 200, N0), (E0, N0 - 200)])
    assert not bowtie.is_valid
    comp = _write_composite(tmp_path / "c.tif")
    res = bd.build_label_raster(
        comp, _nfi([("Conifer", bowtie)]), _cpm([]), tmp_path / "l.tif", exclusions=[]
    )
    assert res.counts["woodland"] > 0


def test_exclusion_box_is_forced_to_unlabelled(tmp_path: Path) -> None:
    comp = _write_composite(tmp_path / "c.tif")
    cpm = _cpm([(1, _rect(0, 0, 40, 40))])
    from pyproj import Transformer

    t = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    w, s = t.transform(E0 + 100, N0 - 300)
    e, n = t.transform(E0 + 200, N0 - 100)  # columns 10-20, rows 10-30
    res = bd.build_label_raster(
        comp, _nfi([]), cpm, tmp_path / "l.tif", exclusions=[(w, s, e, n)], exclusion_buffer_m=0.0
    )
    arr = rasterio.open(res.path).read(1)
    assert (arr[12:28, 12:18] == 255).all()
    assert (arr[:5, :] == 1).all() and (arr[:, :5] == 1).all()
    wide = bd.build_label_raster(
        comp, _nfi([]), cpm, tmp_path / "l2.tif", exclusions=[(w, s, e, n)], exclusion_buffer_m=50
    )
    assert wide.counts["unlabelled"] > res.counts["unlabelled"]


def test_label_errors(tmp_path: Path) -> None:
    comp = _write_composite(tmp_path / "c.tif")
    far = _cpm([(1, box(0, 0, 10, 10))])
    with pytest.raises(bd.DataFetchError, match="no pixel got a label"):
        bd.build_label_raster(comp, _nfi([]), far, tmp_path / "l.tif", exclusions=[])
    with pytest.raises(bd.DataFetchError, match="no pixel got a label"):
        bd.build_label_raster(comp, _nfi([]), _cpm([]), tmp_path / "l.tif", exclusions=[])
    with pytest.raises(bd.DataFetchError, match="column 'IFT_IOA' not found"):
        bd.build_label_raster(
            comp, _cpm([(1, _rect(0, 0, 5, 5))]), far, tmp_path / "l.tif", exclusions=[]
        )
    wrong_crs = tmp_path / "w.tif"
    with rasterio.open(
        wrong_crs,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="float32",
        crs="EPSG:27700",
        transform=from_origin(0, 100, 10, 10),
    ) as dst:
        dst.write(np.ones((1, 4, 4), dtype=np.float32))
    with pytest.raises(ValueError, match="expected EPSG:32630"):
        bd.build_label_raster(wrong_crs, _nfi([]), far, tmp_path / "x.tif", exclusions=[])


# ------------------------------------------------------------------------------ fetch


def _nfi_page(n: int, more: bool) -> bytes:
    feats = [
        {
            "type": "Feature",
            "geometry": None,
            "properties": {"CATEGORY": "Woodland", "IFT_IOA": "Conifer"},
        }
        for _ in range(n)
    ]
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": feats,
            "properties": {"exceededTransferLimit": more},
        }
    ).encode()


def test_fetch_nfi_pages_until_limit_not_exceeded() -> None:
    urls: list[str] = []
    pages = [_nfi_page(2, True), _nfi_page(2, True), _nfi_page(1, False)]

    def get(url: str) -> bytes:
        urls.append(url)
        return pages[len(urls) - 1]

    fc = bd.fetch_nfi_geojson((-4.5, 54.9, -4.2, 55.0), get=get, page_size=2)
    assert len(fc["features"]) == 5 and len(urls) == 3
    assert "resultOffset=4" in urls[2] and "outSR=27700" in urls[0] and "f=geojson" in urls[0]
    assert "inSR=4326" in urls[0]


def test_fetch_nfi_server_error_and_bad_json() -> None:
    with pytest.raises(bd.DataFetchError, match="server error"):
        bd.fetch_nfi_geojson(
            (0, 0, 1, 1), get=lambda u: json.dumps({"error": {"code": 400}}).encode()
        )
    with pytest.raises(bd.DataFetchError, match="not JSON"):
        bd.fetch_nfi_geojson((0, 0, 1, 1), get=lambda u: b"<html>maintenance</html>")


def test_fetch_nfi_endless_paging_is_stopped() -> None:
    with pytest.raises(bd.DataFetchError, match="did not finish"):
        bd.fetch_nfi_geojson((0, 0, 1, 1), get=lambda u: _nfi_page(1, True), max_pages=3)


def test_fetch_cpm_builds_a_lat_first_wfs_query() -> None:
    seen: list[str] = []

    def get(url: str) -> bytes:
        seen.append(url)
        return json.dumps({"type": "FeatureCollection", "features": [{}]}).encode()

    fc = bd.fetch_cpm_geojson((-4.5, 54.9, -4.2, 55.0), get=get, pad_deg=0)
    assert len(fc["features"]) == 1
    assert "bbox=54.9%2C-4.5%2C55.0%2C-4.2%2Curn" in seen[0]
    assert "srsName=EPSG%3A27700" in seen[0] and "carbonpeatlandmap2016" in seen[0]
    with pytest.raises(bd.DataFetchError, match="truncated"):
        bd.fetch_cpm_geojson((0, 0, 1, 1), get=get, max_features=1)


def test_cached_geojson_fetches_once(tmp_path: Path) -> None:
    calls: list[int] = []

    def fetch() -> dict[str, Any]:
        calls.append(1)
        return {"type": "FeatureCollection", "features": []}

    p = tmp_path / "raw" / "x.geojson"
    bd.cached_geojson(p, fetch)
    bd.cached_geojson(p, fetch)
    assert len(calls) == 1 and p.is_file()
    bd.cached_geojson(p, fetch, refresh=True)
    assert len(calls) == 2


def test_http_get_wraps_network_errors() -> None:
    def boom(req: Any, timeout: float) -> Any:
        raise urllib.error.URLError("no route")

    with pytest.raises(bd.DataFetchError, match="GET failed"):
        bd.http_get("https://example.invalid/x", opener=boom)


class _Resp:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def read(self, n: int = -1) -> bytes:
        chunk = self._data[self._pos : self._pos + (n if n > 0 else len(self._data))]
        self._pos += len(chunk)
        return chunk


def test_download_file_streams_caches_and_cleans_up(tmp_path: Path) -> None:
    calls: list[str] = []

    def opener(req: Any, timeout: float) -> _Resp:
        calls.append(req.full_url)
        assert "Mozilla" in req.headers["User-agent"]
        return _Resp(b"x" * 100)

    dest = tmp_path / "a" / "f.zip"
    bd.download_file("https://e.invalid/f.zip", dest, opener=opener, chunk=30)
    bd.download_file("https://e.invalid/f.zip", dest, opener=opener)
    assert dest.read_bytes() == b"x" * 100 and len(calls) == 1
    assert not list(dest.parent.glob("*.part"))

    def fail(req: Any, timeout: float) -> Any:
        raise urllib.error.URLError("down")

    bad = tmp_path / "b.zip"
    with pytest.raises(bd.DataFetchError, match="download failed"):
        bd.download_file("https://e.invalid/b.zip", bad, opener=fail)
    assert not bad.exists() and not list(tmp_path.glob("*.part"))
    with pytest.raises(bd.DataFetchError, match="empty response"):
        bd.download_file(
            "https://e.invalid/c.zip", tmp_path / "c.zip", opener=lambda r, timeout: _Resp(b"")
        )


def test_read_vector_gpkg_zip_and_missing(tmp_path: Path) -> None:
    gdf = _cpm([(1, _rect(0, 0, 10, 10)), (4, box(500000, 5000000, 500100, 5000100))])
    gpkg = tmp_path / "v.gpkg"
    gdf.to_file(gpkg, driver="GPKG")
    got = bd.read_vector(gpkg, bd.REGIONS["galloway-south"])
    assert len(got) == 1  # the far polygon is outside the bbox filter

    shp_dir = tmp_path / "shp"
    shp_dir.mkdir()
    gdf.to_file(shp_dir / "cpm.shp")
    zpath = tmp_path / "cpm.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for f in shp_dir.iterdir():
            zf.write(f, f.name)
    assert len(bd.read_vector(zpath, bd.REGIONS["galloway-south"])) == 1

    empty_zip = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty_zip, "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(bd.DataFetchError, match=r"no \.shp"):
        bd.read_vector(empty_zip, bd.REGIONS["galloway-south"])
    with pytest.raises(bd.DataFetchError, match="file not found"):
        bd.read_vector(tmp_path / "nope.shp", bd.REGIONS["galloway-south"])


def test_geojson_to_frame_crs_and_empty() -> None:
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::27700"}},
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1, 2]},
                "properties": {"a": 1},
            }
        ],
    }
    assert bd.geojson_to_frame(fc).crs.to_epsg() == 27700
    assert len(bd.geojson_to_frame({"type": "FeatureCollection", "features": []})) == 0


# ---------------------------------------------------------------------------- composite


def test_local_s3_stub_never_reports_up_to_date() -> None:
    from pipeline.store import is_up_to_date, upload_product

    s3 = bd.LocalOnlyS3()
    assert is_up_to_date(s3, "b", "composites/x.tif", "key") is False
    assert upload_product is not None  # the stub also provides upload_file
    s3.upload_file("a", "b", "c")


def test_write_pipeline_config_loads_with_the_pipeline(tmp_path: Path) -> None:
    from pipeline.config import load_config

    bbox = bd.REGIONS["galloway-south"]
    cfg_path = bd.write_pipeline_config(
        "galloway-south", bbox, date(2025, 4, 1), date(2025, 5, 31), 30.0, tmp_path
    )
    cfg = load_config(cfg_path)
    assert cfg.site == "galloway-south" and cfg.cloud_threshold == 30.0
    assert cfg.window.start == date(2025, 4, 1) and cfg.boundary_path.is_file()
    assert bd.pipeline_command(cfg_path).startswith("uv run peatwatch-pipeline run ")


def test_run_composite_copies_runner_output(tmp_path: Path) -> None:
    made = tmp_path / "scratch" / "x.tif"
    _write_composite(made)
    side = made.with_suffix(".json")
    side.write_text("{}", encoding="utf-8")
    calls: list[Any] = []

    def runner(cfg: Any, logger: Any, *, s3: Any) -> Any:
        calls.append((cfg.site, s3))
        return SimpleNamespace(tif_path=made, sidecar_path=side)

    data = tmp_path / "data"
    args = ("galloway-south", bd.REGIONS["galloway-south"], date(2025, 4, 1), date(2025, 5, 31))
    tif, sc = bd.run_composite(*args, 30.0, data, runner=runner)
    assert tif == data / "composites" / "galloway-south__20250401-20250531.tif"
    assert tif.is_file() and sc is not None and sc.is_file()
    assert isinstance(calls[0][1], bd.LocalOnlyS3)
    bd.run_composite(*args, 30.0, data, runner=runner)  # cached: runner not called again
    assert len(calls) == 1
    bd.run_composite(*args, 30.0, data, refresh=True, runner=runner)
    assert len(calls) == 2


def test_run_composite_reports_no_scenes(tmp_path: Path) -> None:
    from pipeline.stac import NoUsableScenesError

    def runner(cfg: Any, logger: Any, *, s3: Any) -> Any:
        raise NoUsableScenesError("none")

    with pytest.raises(bd.DataFetchError, match="widen the window"):
        bd.run_composite(
            "galloway-south",
            bd.REGIONS["galloway-south"],
            date(2025, 4, 1),
            date(2025, 4, 2),
            5.0,
            tmp_path,
            runner=runner,
        )


# ----------------------------------------------------------------------------- manifest


def test_manifest_merges_regions_and_files(tmp_path: Path) -> None:
    f = tmp_path / "a.tif"
    f.write_bytes(b"abc")
    rec = bd.file_record(f, tmp_path)
    assert rec["path"] == "a.tif" and rec["bytes"] == 3 and len(rec["sha256"]) == 64
    bbox = bd.REGIONS["galloway-south"]
    bd.update_manifest(
        tmp_path,
        region="galloway-south",
        bbox=bbox,
        composites=[rec],
        labels=None,
        sources={"nfi": {"licence": "OGL"}},
    )
    p = bd.update_manifest(
        tmp_path,
        region="galloway-south",
        bbox=bbox,
        composites=[],
        labels={"path": "l.tif"},
        sources={"cpm2016": {"licence": "OGL"}},
    )
    m = json.loads(p.read_text("utf-8"))
    assert set(m["sources"]) == {"nfi", "cpm2016"}
    assert m["regions"]["galloway-south"]["composites"] == [rec]
    assert m["regions"]["galloway-south"]["labels"] == {"path": "l.tif"}
    assert any("overstate skill" in c for c in m["caveats"])
    assert any("UKCEH" in c for c in m["excluded_sources"])
    assert m["holdout"]["bbox_wgs84"] == list(bd.HOLDOUT_BBOX_WGS84)


# --------------------------------------------------------------------------------- CLI


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as ei:
        bd.main(["--help"])
    assert ei.value.code == 0
    assert "--nfi-path" in capsys.readouterr().out


def test_cli_rejects_bad_arguments(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    d = str(tmp_path)
    assert bd.main(["--data-dir", d, "--bbox", "-4.5", "55.1", "-4.4", "55.2", "--name", "x"]) == 2
    assert bd.main(["--data-dir", d, "--bbox", "-4.8", "55.0", "-4.6", "55.1"]) == 2
    assert bd.main(["--data-dir", d, "--window", "2025-05-01", "2025-04-01"]) == 2
    err = capsys.readouterr().err
    assert "hold-out" in err and "--name" in err and "before start" in err


def test_cli_dry_run_prints_the_pipeline_command(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert bd.main(["--data-dir", str(tmp_path), "--dry-run", "--region", "galloway-west"]) == 0
    out = capsys.readouterr().out
    assert "peatwatch-pipeline run" in out and "galloway-west__20250401-20250531.yaml" in out


def test_cli_labels_only_with_local_vector_files(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    data = tmp_path / "data"
    _write_composite(data / "composites" / "galloway-south__20250401-20250531.tif", bands=18)
    nfi_p = tmp_path / "nfi.gpkg"
    _nfi([("Conifer", _rect(0, 0, 20, 40)), ("Felled", _rect(20, 0, 30, 40))]).to_file(nfi_p)
    cpm_p = tmp_path / "cpm.gpkg"
    _cpm([(1, _rect(0, 0, 40, 40))]).to_file(cpm_p)
    rc = bd.main(
        [
            "--data-dir",
            str(data),
            "--region",
            "galloway-south",
            "--skip-composite",
            "--nfi-path",
            str(nfi_p),
            "--cpm-path",
            str(cpm_p),
        ]
    )
    assert rc == 0, capsys.readouterr().err
    arr = rasterio.open(data / "labels" / "galloway-south.tif").read(1)
    assert (arr[:, :20] == 2).all() and (arr[:, 20:30] == 3).all() and (arr[:, 30:] == 1).all()
    m = json.loads((data / "MANIFEST.json").read_text("utf-8"))
    reg = m["regions"]["galloway-south"]
    assert reg["labels"]["class_counts"]["bog"] == 400
    assert m["sources"]["nfi"]["licence"].startswith("Open Government Licence")
    assert m["sources"]["cpm2016"]["file"]["sha256"]
    # a second run with the same inputs is stable
    assert (
        bd.main(
            [
                "--data-dir",
                str(data),
                "--region",
                "galloway-south",
                "--skip-composite",
                "--nfi-path",
                str(nfi_p),
                "--cpm-path",
                str(cpm_p),
            ]
        )
        == 0
    )


def test_cli_labels_without_composite_fails_clearly(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert bd.main(["--data-dir", str(tmp_path), "--skip-composite"]) == 2
    assert "composite not found" in capsys.readouterr().err
