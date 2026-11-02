# L1 part B1: candidate regions (Flow Country, Galloway)

Part of task L1 (`tasks/todo.md`). Covers two of the shortlisted regions. Each claim is
tagged **VERIFIED** (fetched on 2026-11-02 during this run, URL given) or **UNVERIFIED**
(from background knowledge; needs a check before L2 relies on it).

## Method: Sentinel-2 scene query

- API: Earth Search STAC v1, `POST https://earth-search.aws.element84.com/v1/search`,
  collection `sentinel-2-l2a`, `limit` 200 with next-link paging, Python `urllib` (`python -I`).
- Window: 1 June to 31 August of each year, 2023, 2024 and 2025.
- `eo:cloud_cover` is the cloud percentage for the **whole 110 km MGRS tile**, not for the
  query bbox. Read it as a screen only. Per-site cloud needs the SCL band (pipeline task P3/P4).
- Query bboxes (lon/lat, WGS84):
  - Flow Country: `[-4.10, 58.33, -3.75, 58.50]` (Forsinard / Strath Halladale core)
  - Galloway: `[-4.60, 55.00, -4.30, 55.15]` (Galloway Forest Park, Loch Trool / Cairnsmore of Fleet area)

## Region 1: Flow Country (Caithness and Sutherland)

### Sentinel-2 (VERIFIED, Earth Search query above)

| Summer | Items | Distinct dates | Median tile cloud % | Min % | Items <20% | Items <10% | Clearest items |
|---|---|---|---|---|---|---|---|
| 2023 | 37 | 37 | 82.2 | 1.0 | 4 | 2 | `S2A_30VVK_20230616_0_L2A` (1.0), `S2B_30VVK_20230810_0_L2A` (9.2), `S2B_30VVK_20230614_0_L2A` (12.8), `S2A_30VVK_20230726_0_L2A` (15.1) |
| 2024 | 35 | 35 | 77.2 | 50.2 | 0 | 0 | best is `S2A_30VVK_20240723_0_L2A` (50.2) |
| 2025 | 58 | 45 | 77.2 | 7.7 | 2 | 1 | `S2B_30VVK_20250713_0_L2A` (7.7), `S2B_30VVK_20250802_0_L2A` (12.8) |

- MGRS tile: **30VVK** only, for this bbox. VERIFIED (all returned items carry `mgrs:grid_square` VK, zone 30, band V).
- The wider Flow Country (west to Altnaharra, east to Wick) may spill into neighbouring
  tiles; not queried. UNVERIFIED.
- 2025 has 58 items on 45 dates: some dates have two processing versions (`_0_` and `_1_`
  suffix, for example 20250620). The pipeline must de-duplicate by date. VERIFIED (same query).
- Takeaway: summer 2024 had **no** tile-level scene under 50% cloud. A single-summer
  composite for 2024 will be poor; 2023 and 2025 each have one or two near-clear dates.
- North of about 58.45N (Melvich/Strathy coast) the area also falls in tile **30VVL**. VERIFIED
  (2019 fire-window query below returned both 30VVK and 30VVL).

### LiDAR for Scotland (VERIFIED, with caveats)

Source: Scottish Remote Sensing Portal, https://remotesensingdata.gov.scot/data (reachable,
JavaScript app). Its bundle points to the GeoServer at
`https://ows.remotesensing.data.gov.scot/geoserver/scotland/wfs`, whose WFS exposes one
layer, `scotland:dsm-footprint-aggregate` (DSM coverage footprints, 19 features: LiDAR
phases 1 to 6, HES 2010/2016/2017 surveys, National LiDAR Programme `nlp-dsm-NR/NS/NX`,
Orkney 2023, Outer Hebrides 2019).

- Flow Country core bbox above: **no footprint intersects**. Rechecked in British National
  Grid (bbox E 270000 to 300000, N 925000 to 945000): still none. VERIFIED.
- A much wider Caithness/Sutherland box (E 200000 to 400000, N 900000 to 980000) hits only
  phases 1 and 2, which are likely the coastal towns (Thurso, Wick). The town attribution is
  UNVERIFIED.
- So: no LiDAR for the bog interior. Use Copernicus DEM (30 m) for slope and TWI (task P6).

## Region 1 labels and events

### Likely label coverage

| Source | Coverage here | Status |
|---|---|---|
| Carbon and Peatland 2016 map (NatureScot): 5 classes; Class 1/2 = peat soil with priority peatland habitat, Class 5 = peat >50 cm without peatland habitat. Shapefile from the NatureScot Open Data Hub. | Flow Country is the largest blanket bog block in Scotland, so expect mostly Class 1 | Map facts VERIFIED, https://www.nature.scot/professional-advice/planning-and-development/planning-and-development-advice/soils/carbon-and-peatland-2016-map ; local class mix UNVERIFIED |
| NFI woodland map (Forest Research), downloadable, OGL v3 | Covers the 1980s conifer plantations (Fountain Forestry) and their felled forest-to-bog parcels | Download and OGL VERIFIED, https://www.forestresearch.gov.uk/tools-and-resources/fthr/national-forest-inventory/ ; local coverage UNVERIFIED |
| Peatland ACTION data mapping portal (project locations, hectares restored, condition surveys) and InSAR peatland condition class / change data (c.680,000 ha) | RSPB Forsinard and Flows to the Future restoration sites likely included | Portals exist VERIFIED, https://www.nature.scot/climate-change/nature-based-solutions/peatland-action/peatland-action-data-research-and-monitoring ; whether Forsinard polygons are in it UNVERIFIED |
| UNESCO World Heritage Site boundary (The Flow Country, inscribed 2024, 187,000 ha); says most bog is near-natural with some under restoration | Whole region | VERIFIED, https://whc.unesco.org/en/list/1722 and https://en.wikipedia.org/wiki/Flow_Country |

### Change events

1. **Wildfire, Melvich to Strathy, May 2019** (about 12 to 18 May 2019). About 8,100 ha of
   Flow Country peatland burned, including about 610 ha of Forsinard Flows NNR; rain on
   17 to 18 May helped control it. VERIFIED (secondary source), https://en.wikipedia.org/wiki/2019_United_Kingdom_wildfires
   (cites BBC News, 13 and 18 May 2019). Start date and burn perimeter UNVERIFIED.
   - Demo date pair, VERIFIED on Earth Search (bbox `[-4.10, 58.40, -3.85, 58.56]`):
     pre `S2B_30VVK_20190413_0_L2A` (2.3% cloud) / `S2B_30VVL_20190413_0_L2A` (7.9%);
     post `S2A_30VVL_20190607_1_L2A` (5.1%) or `S2B_30VVK_20190715_0_L2A` (5.7%).
   - Outside the 2023 to 2025 window, but it is the clearest dated large event in the region.
2. **Forest-to-bog felling at Forsinard.** RSPB bought back more than 20 km2 from Fountain
   Forestry, felled the young trees and left them to rot in the plough furrows. VERIFIED,
   https://en.wikipedia.org/wiki/Flow_Country (RSPB reserve now over 21,000 ha: VERIFIED,
   https://www.rspb.org.uk/days-out/reserves/forsinard-flows). Specific felling dates and
   parcels in 2023 to 2025 are UNVERIFIED; need RSPB or Peatland ACTION project records.
3. **Drain-blocking / rewetting** under Flows to the Future and Peatland ACTION. Programme
   VERIFIED (same Wikipedia page); sub-pixel ditch blocking is unlikely to show at 10 m, so
   not a good demo event. UNVERIFIED as a visible signal.

## Region 2: Galloway and Southern Uplands

### Sentinel-2 (VERIFIED, Earth Search query above)

The bbox sits on a corner of four MGRS tiles: **30UUF, 30UVF, 30UUG, 30UVG**. VERIFIED (item
`mgrs:*` properties). Item counts below are per tile (about a quarter of the total).

| Summer | Items (all 4 tiles) | Distinct dates | Median tile cloud % | Min % | Items <20% | Items <10% | Clearest items |
|---|---|---|---|---|---|---|---|
| 2023 | 150 | 37 | 69.1 | 0.0 | 9 | 6 | 20230614 on all 4 tiles (0.0 to 7.1); 20230604 on UUF/UVF (7.6, 9.5) |
| 2024 | 144 | 35 | 88.5 | 4.8 | 7 | 1 | `S2A_30UUF_20240723_0_L2A` (4.8); 20240814 on all 4 tiles (12.1 to 14.0) |
| 2025 | 217 | 45 | 89.3 | 0 | 12 | 6 | 20250713 (UVF 0, UUF 0.0, UUG 4.0); 20250817 (UVF 0, UUG 0.0, UVG 0.8) |

- Takeaway: Galloway has a clear date in **every** summer 2023 to 2025 (20230614,
  20240723 or 20240814, 20250713 or 20250817), unlike the Flow Country in 2024.
- Cost: an AOI on the tile corner needs mosaicking of up to 4 tiles. Moving the AOI a few km
  could keep it inside one tile. UNVERIFIED (tile footprints not checked).
- The Glen Trool to Loch Doon fire area (bbox `[-4.55, 55.05, -4.30, 55.30]`) falls in
  **30UUG and 30UVG** only. VERIFIED (fire-window query below).

### LiDAR for Scotland (VERIFIED, with caveats)

Same WFS layer as Region 1. Galloway bbox `[-4.60, 55.00, -4.30, 55.15]`, rechecked in BNG
(E 236000 to 256000, N 565000 to 582000): intersects **phase 1 and phase 3** DSM footprints.
VERIFIED. The aggregate footprint only proves overlap with the box, not full coverage; how
much of the moorland is covered is UNVERIFIED (inspect the portal map before relying on it).
The National LiDAR Programme `nlp-dsm-NX` footprint extends only to N 550000 (E 203948 to
249640), so it covers the Machars, south of the forest park. VERIFIED (feature bbox from WFS).

### Likely label coverage

| Source | Coverage here | Status |
|---|---|---|
| Carbon and Peatland 2016 map | Upland blanket bog patches (for example Silver Flowe, Cairnsmore of Fleet) amid plantation; expect Class 1/2 on open moor and Class 5 under conifers | Map VERIFIED (URL in Region 1); local mix UNVERIFIED |
| NFI woodland map | Galloway Forest Park is a large commercial conifer forest, so NFI is the main woodland/felling label source | NFI download VERIFIED (URL in Region 1); local content UNVERIFIED |
| Peatland ACTION portal | Forest Land Scotland and NatureScot restoration projects likely, including Cairnsmore of Fleet NNR (1,922 ha, managed by NatureScot) | NNR size VERIFIED, https://en.wikipedia.org/wiki/Cairnsmore_of_Fleet ; Peatland ACTION projects there UNVERIFIED |

### Change events

1. **Galloway Forest Park wildfire, 3 to 7 April 2025.** Called in 4:14 p.m. on 3 April near
   Glen Trool, spread to Loch Doon, under control by 7 April; estimated 65 km2 burned (the
   largest single fire in the record 2 to 8 April week). VERIFIED (secondary source),
   https://en.wikipedia.org/wiki/2025_United_Kingdom_wildfires (cites BBC News 8 and 9 April
   2025 and Greatest Hits Radio Dumfries and Galloway, 10 April 2025). Perimeter UNVERIFIED.
   - Demo date pair, VERIFIED on Earth Search (bbox `[-4.55, 55.05, -4.30, 55.30]`):
     pre `S2A_30UUG_20250319_0_L2A` (8.1% cloud) or `S2B_30UVG_20250401_0_L2A` (18.3%);
     post `S2A_30UUG_20250408_0_L2A` (0.0%) / `S2A_30UVG_20250408_1_L2A` (0.0%), or
     `S2C_30UUG_20250516_0_L2A` / `S2C_30UVG_20250516_0_L2A` (both 0.0%).
   - A season-scale pair also works: summer 2024 (`20240723`/`20240814`) vs summer 2025
     (`20250713`/`20250817`), though regrowth may soften the scar. UNVERIFIED.
2. **Routine clear-fell and restock in Galloway Forest Park** (Forestry and Land Scotland).
   Ongoing every year; specific coupes and dates UNVERIFIED. Need FLS felling records or
   Scottish Forestry felling permissions.
3. **Storm windblow.** Storm Arwen (Nov 2021) damaged about 8 million trees in Scotland,
   VERIFIED, https://en.wikipedia.org/wiki/Storm_Arwen ; Galloway impact UNVERIFIED (Arwen was
   a north-easterly storm). Storm Eowyn (24 Jan 2025) hit Scotland, VERIFIED,
   https://en.wikipedia.org/wiki/Storm_%C3%89owyn ; Galloway forest damage UNVERIFIED.

## Candidate held-out demo site

**Recommended: Glen Trool to Loch Doon burn area, Galloway (about `[-4.55, 55.05, -4.30, 55.30]`).**

- Large (about 65 km2), dated (3 to 7 April 2025) and recent; mixes moorland, peat and
  plantation edge.
- Near-cloud-free pre and post scenes within weeks of the fire (above), all VERIFIED.
- Some LiDAR (phases 1 and 3) nearby, VERIFIED but extent unclear.
- Hold out this box; train elsewhere in Galloway/Southern Uplands, keeping a buffer so no
  training tile overlaps the burn.
- Risks: burn perimeter not yet sourced (UNVERIFIED; could map it from dNBR on the pair
  above, or ask SFRS / FLS); the 2-tile seam (30UUG / 30UVG) needs a mosaic.

**Fallback: the 2019 Melvich to Strathy burn, Flow Country.** Clear 2019 pair VERIFIED, but
the event is older than the 2023 to 2025 window and summer 2024 there had no scene under 50%
cloud, so recent change maps would be weak.

## Comparison

| | Flow Country | Galloway |
|---|---|---|
| MGRS tiles | 30VVK (+30VVL on north coast) | 30UUF, 30UVF, 30UUG, 30UVG (corner) |
| Clear summer dates 2023/2024/2025 | 2 / 0 / 1 under 10% | 6 / 1 / 6 items under 10% |
| LiDAR | none over bog interior | phases 1 and 3 overlap, extent unclear |
| Best dated event | 2019 wildfire, 8,100 ha | April 2025 wildfire, about 65 km2 |
| Label richness | peat map dominant; little woodland | peat + NFI woodland + fire; more classes |

## Open questions for L2

- Burn perimeters for both fires (SFRS, FLS, NatureScot, or derive from dNBR).
- Peatland ACTION polygons in each region (query the portal's map service).
- LiDAR phase 1/3 extent inside the Galloway hold-out box.
