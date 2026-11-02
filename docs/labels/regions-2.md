# L1 part B2: candidate region Cairngorms / Deeside

Task: L1 (part B2). Scope: one candidate region (Cairngorms / Upper Deeside: blanket bog, wet heath,
Caledonian pinewood, birch, conifer plantation) plus a brief Lochaber comparison.
Companion files: `docs/labels/datasets.md` (L1-A dataset survey). No code here.

Marking: **VERIFIED** = fetched during this run (2026-11-02), URL given. **UNVERIFIED** = from prior
knowledge, not fetched this run; must be checked before L2 relies on it.

## 1. Area of interest

Working bbox used for the STAC queries (WGS84 lon/lat): `-3.6, 56.95, -2.9, 57.15`
(Braemar / Invercauld / Glen Muick / Ballater / Glen Tanar; about 42 km x 22 km, roughly 900 km2,
so a few hundred km2 training subset can be cut from it, per SPEC D4).

## 2. Sentinel-2 coverage and summer cloud (Earth Search STAC)

Source: `POST https://earth-search.aws.element84.com/v1/search`, collection `sentinel-2-l2a`,
datetime 1 Jun to 31 Aug each year, `limit 500` (all results fit in one page). **VERIFIED** (queried this run).

MGRS tiles intersecting the bbox: **30VVJ** (west: Braemar, Mar Lodge, Glen Muick) and **30VWJ**
(east: Ballater, Glen Tanar, Dinnet). **VERIFIED** (`grid:code` on returned items).

| Summer | Items | Distinct dates | Median `eo:cloud_cover` | Items < 20 % | Items < 10 % |
|---|---|---|---|---|---|
| 2023 | 110 | 55 | 80.6 % | 9 | 4 |
| 2024 | 108 | 54 | 83.0 % | 8 | 3 |
| 2025 | 166 | 64 | 73.9 % | 8 | 5 |

Clearest items (date, tile, tile-level cloud %), **VERIFIED**:

- 2023: 06-14 VWJ 1.8; **06-16 VVJ 0.2 and VWJ 0.0** (both tiles clear same day); 07-08 VWJ 2.2.
- 2024: 07-30 VWJ 8.3; 08-11 VVJ 8.5; **08-31 VWJ 0.9** (VVJ 18.4).
- 2025: 06-28 VWJ 4.6; 07-12 VVJ 0.0; 08-02 VWJ 4.2 (VVJ 14.3); **08-17 VVJ 3.5 and VWJ 2.6**.

Notes:
- `eo:cloud_cover` is the whole 110 km tile, not the bbox, so the bbox can be clearer or cloudier.
  Per-pixel SCL masking in the composite step is still required.
- 2025 has more items than dates (83 per tile on 64 dates): likely duplicate processing baselines
  in Earth Search. The pipeline should de-duplicate by `s2:product_uri`/latest processing (UNVERIFIED cause).
- Summer is cloudy (median ~80 %), but every year has at least one near-clear date covering both
  tiles, so a best-pixel or median summer composite is feasible. 2023-06-16 and 2025-08-17 are
  the best whole-region single dates.

### 2b. Strathspey extension (Glenmore / Abernethy), same tile

The north side of the Cairngorm plateau (Glenmore, Abernethy) is also inside **30VVJ**
(bbox `-3.80, 57.12, -3.55, 57.25`). **VERIFIED** (Earth Search, this run):

- Summer 2025: 55 items, median cloud 79.1 %; clearest 2025-08-17 (3.5 %), 2025-08-16 (13.9 %).
- 1 Jul to 31 Oct 2026: 72 items, median 72.6 %; clear dates **2026-07-08 (3.6 %)**, 2026-07-13 (7.4 %),
  2026-07-15 (0.8 %), 2026-08-12 (22.7 %), **2026-10-18 (0.0 %)**.

This matters because of the July 2026 wildfire (section 5).

## 3. LiDAR for Scotland

The portal `https://remotesensingdata.gov.scot/` is reachable (HTTP 200). **VERIFIED**.
It is a JavaScript app backed by `https://api.remotesensing.data.gov.scot` (found in the page bundle
`/data.f79d09b3.js`). **VERIFIED**.

Collections listed by `GET https://api.remotesensing.data.gov.scot/search/collection/scotland-gov/*`
**VERIFIED**: LiDAR for Scotland Phase I to VI (DTM, DSM, LAZ each) and the
"Scottish National LiDAR Programme" (DTM, DSM, LAZ), plus local HES, Orkney, Outer Hebrides and
coastal 2025-26 surveys. Collection abstracts state Phase 3 to 6 were captured by Fugro for SP Energy
Networks (Phase 3: 2015-16, Phase 4: 2017-19, Phase 5: 2020-21, Phase 6: 2021-22) along overhead
line corridors. **VERIFIED** (abstract text).

Coverage of Deeside: **UNVERIFIED**. The collection-level footprints for Phase I to IV are coarse
envelopes (they contain both Braemar and Fort William), so they prove nothing about tiles on the
ground; Phase V, VI and the National LiDAR Programme envelopes did not contain any Deeside test point.
A per-tile query (`POST /search/product/countByCollection` with `collections` + GeoJSON `footprint`) was
started but had not returned at time of writing (see section 8). Prior knowledge (UNVERIFIED): Phase 1-2
are lowland/flood-risk focused (Aberdeen, Dee valley towns such as Ballater/Braemar may be included);
the uplands and plateau are unlikely to be covered; SPEN corridor strips (Phase 3-6) are narrow.
**Planning assumption:** use Copernicus DEM 30 m for the whole region (SPEC P6), and treat any LiDAR
as an optional extra for valley-floor blocks only.

## 4. Likely label coverage

Dataset details and licences are in `docs/labels/datasets.md` (L1-A). Region-specific expectations,
all **UNVERIFIED** this run (not fetched):

| Label source | Expected coverage in Deeside / Strathspey | Use |
|---|---|---|
| NatureScot Carbon and Peatland Map 2016 | Extensive class 1/2 peat on plateau margins, Glen Muick, Glen Gairn, Gairnshiel, Dinnet / Morven moors | Peat vs non-peat context; coarse (1:250k soil-based) |
| NFI woodland map (Scottish Forestry / Forest Research) | Plantations (Ballochbuie fringe, Invercauld, Glen Tanar, Mar Lodge "restructuring plantations"), native pinewood and birch | Woodland / conifer class, yearly updates aid change |
| Native Woodland Survey of Scotland (NWSS) | Caledonian pinewoods: Ballochbuie, Mar Lodge (Glen Lui, Derry, Quoich), Glen Tanar, Abernethy, Glenmore | Native vs plantation split |
| Peatland ACTION restoration sites | Several Cairngorms NP projects (CNPA runs a Peatland ACTION team); exact Deeside polygons not checked | Rewetting / drain-blocking change; probably points or project areas, not condition maps |
| Hand-labelled set (SPEC D5) | Any | Validation |

## 5. Change events (candidate demo date pairs)

| # | Event | Where | Approx date | Evidence | Status | Suggested S2 pair |
|---|---|---|---|---|---|---|
| 1 | **Major wildfire**, Caledonian pinewood and moorland burnt; front 25 km wide by 27 July | Glenmore, An Lochan Uaine, Abernethy, Nethy Bridge (Strathspey side of Cairngorms NP) | started 15 Jul 2026; flare-up 24 Jul; damping down to 12 Aug 2026 | [Wikipedia: 2026 Cairngorms wildfire](https://en.wikipedia.org/wiki/2026_Cairngorms_wildfire); [Cairngorms National Park](https://en.wikipedia.org/wiki/Cairngorms_National_Park) ("approximately 25 square kilometres") | VERIFIED (secondary source; fetched via Wikipedia API). Primary (SFRS / SEPA / Copernicus Charter activation 16 Jul) UNVERIFIED | **2026-07-08 (3.6 %) vs 2026-10-18 (0.0 %)**, or summer 2025 composite (2025-08-17) vs 2026-10-18. Caveat: October senescence; compare against a 2025 autumn baseline if possible |
| 2 | Storm Arwen windthrow then salvage felling in Aberdeenshire forests | Deeside plantations (Invercauld, Ballater, Aboyne area) | storm 26-27 Nov 2021; felling 2022-2024 | [Wikipedia: Storm Arwen](https://en.wikipedia.org/wiki/Storm_Arwen) confirms red wind warning and tree-fall impacts in Aberdeenshire | Storm and Aberdeenshire impact VERIFIED; specific Deeside windblow stands and felling dates UNVERIFIED (check Scottish Forestry felling permissions / FLS) | summer 2021 vs summer 2023 (2023-06-16, both tiles clear) |
| 3 | Pinewood expansion by natural regeneration after deer reduction; plantation restructuring | Mar Lodge Estate (Glen Lui, Derry, Quoich), Glen Tanar (non-native 1950s-60s conifers "gradually removed") | ongoing since 1995 (Mar Lodge NTS ownership); deer down to ~1,700 by 2011 | [Wikipedia: Mar Lodge Estate](https://en.wikipedia.org/wiki/Mar_Lodge_Estate); [Wikipedia: Glen Tanar](https://en.wikipedia.org/wiki/Glen_Tanar) | VERIFIED (secondary); rate and visibility at 10 m UNVERIFIED | Slow change: needs long pair (e.g. 2017 vs 2025); poor for a short demo, good for "woodland gained" narrative |
| 4 | Peat restoration (drain blocking, re-profiling) | Cairngorms NP Peatland ACTION projects | 2019 onwards | NatureScot / CNPA pages returned 403/404 to scripted fetch | UNVERIFIED | Spectrally subtle; not recommended as primary demo |

Note: the July 2026 Cairngorms fire is distinct from the June 2025 Carrbridge / Dava Moor fire
(UNVERIFIED, page not found this run), which lies further north and probably outside 30VVJ.

## 6. Candidate held-out demo site

**Primary: Glenmore / Abernethy 2026 burn scar** (bbox approx `-3.80, 57.12, -3.55, 57.25`).
- Same MGRS tile (30VVJ) as Upper Deeside, so one processing grid and one cloud regime.
- Separated from the Deeside training area by the Cairngorm plateau (about 15-20 km), so spatial
  hold-out is clean.
- Real, large, dated change (pinewood and heath to burnt ground) with clear pre/post scenes
  (2026-07-08, 2026-10-18). Answers "hectares of woodland lost between two dates" directly.
- Risk: the classifier must handle "burnt" as not-woodland; a burnt class is not in any official
  label map, so either map burnt to "other/bare" or add hand labels.

**Fallback: Glen Tanar NNR** (about 42 km2, around `-2.87, 57.05`), held out from a Deeside training area
west of Ballater. Change is slow (regeneration, conifer removal), so less visual.

Suggested training area: Upper Deeside, Braemar to Ballater (Mar Lodge, Invercauld, Glen Muick,
Glen Gairn), cut to a few hundred km2 and excluding Glen Tanar if it is the fallback demo.

## 7. Alternative fourth choice: Lochaber (brief)

Same STAC query on bbox `-5.2, 56.75, -4.6, 57.0` (Fort William / Glen Spean). **VERIFIED**:
4 tiles (30VUH, 30VVH, 30VUJ, 30VVJ), median summer cloud 86.1 % (2023), 88.8 % (2024), 87.5 % (2025);
items under 10 %: 4, 1, 5. Fewer distinct dates (35-45 vs 54-64) and cloudier than Deeside, and the
bbox straddles four tiles. **Not better: keep Cairngorms / Deeside.**

## 8. Open items for L2

1. Per-tile LiDAR coverage query (section 3) still UNVERIFIED; rerun the API query with a longer timeout.
2. Confirm the 2026 wildfire perimeter from a primary source (SFRS, SEPA, Copernicus EMS / Charter, or
   EFFIS burnt-area product) before fixing the demo bbox.
3. Confirm NFI and NWSS polygons exist for Glenmore / Abernethy (pre-fire state) and for Deeside.
4. 2025 duplicate STAC items: pipeline should de-duplicate by processing baseline.
