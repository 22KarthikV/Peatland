# Hand-labelling protocol (L3)

Goal: about 100 independent polygons that score the classifier on ground the training labels never touched. Hand labels are the validation set, not training data.

## 1. Scope

- **Region:** Galloway / Southern Uplands training region (S2 tiles 30UUF, 30UVF, 30UUG, 30UVG), per ADR 0003.
- **Held out:** the Glen Trool to Loch Doon demo box, bbox about `[-4.55, 55.05, -4.30, 55.30]`. No label, training chip or tuning sample may touch it, with a buffer of at least 1 km (about 0.015 deg). The demo burn is used only for the final demonstration. Reject any polygon whose geometry intersects the buffered box.
- **Where inside the region:** only inside the **test blocks**, the spatial blocks reserved for evaluation (defined with the split in M-series tasks; until then use a placeholder grid of 10 km blocks, assigning every third block by a fixed seed to test). Training blocks are never labelled by hand. Keep a 500 m gap between any test block and its nearest training block so label polygons never share a 10 m pixel neighbourhood or an autocorrelated patch with training data.
- Labels live in their own file (`data/labels/handlabel.geojson`, copied from `docs/labels/handlabel.template.geojson`), separate from the national-source training labels, and are never merged into training sets.

## 2. Classes and target counts

| class | what to label | target |
|---|---|---|
| `bog` | blanket or raised bog with intact or degraded peat vegetation (Sphagnum, heather, cotton-grass, pools, hags). Include drained or eroding bog if peat is visibly exposed but still bog-dominated. | 30 |
| `woodland` | conifer plantation or broadleaf woodland, closed canopy, standing trees | 30 |
| `felled_or_burnt_bare` | clear-fell coupes, brash and stumps, recent muirburn or wildfire scar, bare ground from works | 20 |
| `other` | grassland, improved pasture, water, rock and scree, built-up, roads | 20 |

Aim for at least 8 polygons per class in each of at least 3 test blocks, if the test blocks allow it. Draw hard cases on purpose: bog edge next to plantation, young restock versus bog, burnt versus dark wet peat.

## 3. Polygon rules

- **Minimum size:** a polygon must contain at least 9 full 10 m pixels at its core. Practical rule: at least 60 m x 60 m (0.36 ha), with every edge at least 30 m from a class boundary, so mixed edge pixels are excluded. Prefer 100 m x 100 m or larger, up to about 20 ha. Do not label linear features narrower than 40 m.
- **Homogeneous:** one class per polygon. Digitise inside the patch, not on its outline. Never draw across a visible boundary; if a patch is mixed, label the pure part only or skip it.
- **No overlap:** polygons must not overlap or touch each other. Keep at least 20 m between polygons of different classes.
- **Geometry:** Polygon or MultiPolygon, EPSG:4326 in the file (digitise in EPSG:27700 or UTM 30N if you prefer, QGIS reprojects on export). Simple, valid geometry (run Vector > Geometry Tools > Check Validity).
- Two polygons from the same stand count once per stand for balance; spread polygons over the block.

## 4. Label date and imagery

- **label_date** is the ISO date (`YYYY-MM-DD`) of the Sentinel-2 scene that the label describes. The label is true on that date only; it is not carried to other dates. For change classes, pick the post-event scene.
- Use cloud-free summer or early-spring scenes with less than 10% cloud over the block. Candidate dates are listed in `docs/labels/candidates.md` (for example 2025-04-08 and 2025-05-16 for the 2025 season). Label a block from one date only.
- Cross-check against a second, independent source before assigning `high`: current aerial imagery (see section 5) for the stand, and for felling or burning at least one S2 scene before and after.

## 5. QGIS workflow

1. Create a QGIS project in EPSG:27700 or UTM 30N. Import `data/labels/handlabel.geojson` (copy of the template) as a polygon layer; set it editable. Keep the project file outside git or in `data/labels/`.
2. **Sentinel-2:** add the scene for label_date as a COG. Either use the QGIS STAC browser (Data Source Manager > STAC) against the Earth Search endpoint, or add `visual` and band assets by URL (`/vsicurl/...`). Display true colour (B04, B03, B02) and a false colour composite (B08, B04, B03; or B11, B08, B04 for burn and bare ground). Stretch 2 to 98 percent.
3. **Aerial imagery:** add a basemap for context only: OS and Scottish aerial layers available to you, or Esri World Imagery and Google/Bing via the QuickMapServices plugin where licence allows. Note the basemap capture date, which may differ from label_date; trust S2 for date, aerial for texture and boundaries. Do not copy imagery into the repo.
4. **Grid overlay:** load the 10 m S2 pixel grid (Vector > Research Tools > Create Grid, 10 m, aligned to the S2 raster) to check the minimum size.
5. **Test block and holdout layers:** load test blocks, training blocks and the buffered demo box as reference layers. Turn on snapping off for these. Never digitise outside a test block or inside the buffered demo box.
6. Digitise each polygon, then fill the attributes form (section 6). Save often; commit the file at the end of each block.
7. Run a QA pass at the end: Check Validity, intersection test against training blocks and the demo box (expect empty), count by class, and the schema test below.

## 6. Attributes

Documented formally in `docs/labels/handlabel.schema.json` (JSON Schema; the template is an empty FeatureCollection).

| field | type | values |
|---|---|---|
| `class` | enum | `bog`, `woodland`, `felled_or_burnt_bare`, `other` |
| `label_date` | date string | `YYYY-MM-DD`, S2 scene date |
| `confidence` | enum | `high`, `medium`, `low` |
| `source` | string | imagery used, e.g. `S2 2025-04-08 + aerial basemap` |
| `labeller` | string | initials or handle |
| `notes` | string, optional | free text, e.g. `edge of restock` |

Confidence guide: `high` = obvious on S2 and aerial, two sources agree; `medium` = one source clear, the other ambiguous or old; `low` = guess. Low polygons are kept in the file but excluded from scoring and used only to study ambiguity.

## 7. Independence from training

- Labellers must not see classifier output or national label layers while drawing (hide them), to avoid anchoring.
- Training data (HabMoS, NFI, CPM2016) is drawn only from training blocks. Hand labels are used only for evaluation; if they are later used for tuning, split by block and report which blocks.
- Record the blocks, dates and counts in the L3 handoff.

## 8. Checks

```
uv run pytest ml -k handlabel
```

validates the template and schema. Run a `jsonschema` validation over the finished file with the same schema before committing it.
