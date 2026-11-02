# L1: candidate regions and label data

Merged from `datasets.md` (national label and ancillary sources), `regions-1.md` (Flow Country, Galloway) and `regions-2.md` (Cairngorms/Deeside). Each claim there is marked VERIFIED (primary or API fetched on 2026-11-02, with URL) or UNVERIFIED. This file summarises; follow the links in the part files for evidence.

## Region comparison

| | Flow Country (Caithness/Sutherland) | Galloway / Southern Uplands | Cairngorms / Deeside |
|---|---|---|---|
| S2 tiles | 30VVK (+30VVL on north coast) | 30UUF, 30UVF, 30UUG, 30UVG (four-tile corner, needs mosaic) | 30VVJ, 30VWJ |
| Summer clear scenes (<10% cloud) 2023/24/25 | 2 / 0 / 1 (tile-level) | 6 / 1 / 6 (items) | few; median summer cloud 81% / 83% / 74%; best dates 2023-06-16, 2025-08-17 |
| LiDAR (Scotland phases) | none over bog interior (VERIFIED) | phases 1 and 3 overlap; extent unclear | unverified; use Copernicus GLO-30 |
| Labels | peat map dominant, little woodland | peat + NFI woodland + fire; most classes | peat, native/plantation woodland (coverage UNVERIFIED) |
| Best dated event | wildfire May 2019, ~8,100 ha | wildfire Glen Trool to Loch Doon, 3-7 Apr 2025, ~65 km2 | wildfire Glenmore/Abernethy from 15 Jul 2026, ~25 km2 |
| Event evidence | Wikipedia citing BBC (secondary) | Wikipedia citing BBC (secondary) | Wikipedia only (secondary) |
| Demo pair | 2019-04-13 vs 2019-06-07/07-15 | **2025-03-19 (8.1%) vs 2025-04-08 / 05-16 (0-4%)** | 2026-07-08 (3.6%) vs 2026-10-18 (0.0%), autumn senescence caveat |
| Verdict | Peat-only, cloudiest, weak recent coverage | **Best balance** of cloud, classes and event | Good woodland, cloudy; viable fallback |

## Label data (summary)

- **Peatland extent:** HabMoS NVC polygons (OGL) where surveyed; Carbon and Peatland Map 2016 as a coarse prior (OGL); LCM bog class is weak and circular.
- **Peatland condition:** no open national map found. Hand labels (aerial/LiDAR) are the main route. Peatland ACTION footprints give restored sites with dates. This is the biggest risk to a condition class (L2 may merge or drop it).
- **Woodland:** NFI Scotland 2024/2025 (OGL), partly derived from Sentinel-2 (VERIFIED in the record), so agreement overstates skill; NWSS for native woodland; FLS subcompartments on the national forest estate.
- **Change events:** NFI edition differences for felling and planting; Peatland ACTION footprints for rewetting; wildfire dates from news plus dNBR.
- **Terrain:** Copernicus GLO-30 (30 m DSM, free licence); Scottish LiDAR where covered.
- **Licences:** all OGL/open except UKCEH LCM (bespoke licence, not for a public repo without agreement) and the Muirburn Peatland Map (non-commercial only).

## Recommendation for L2

Training region: **Galloway / Southern Uplands**, outside the burn box with a buffer. Held-out demo site: **Glen Trool to Loch Doon burn** (bbox about `[-4.55, 55.05, -4.30, 55.30]`), pair 2025-03-19 vs 2025-04-08. Fallback: Cairngorms/Deeside with the 2026 Glenmore burn. Classes to decide in L2 (suggested): bog, woodland, felled/burnt-bare, other; treat degraded-peat as optional pending hand labels.

## Open items before L2/C1

1. Burn perimeters (SFRS, FLS, EFFIS/Copernicus EMS) or derive from dNBR; all fire evidence is currently secondary.
2. LiDAR phase 1/3 extent inside the Galloway box; Deeside LiDAR query unfinished.
3. Peatland ACTION and NFI/NWSS polygons per region (portal queries).
4. Decide whether to use UKCEH LCM given its licence.

## Spot-checks by the orchestrator (2026-11-02)

- CPM2016 download URL returns HTTP 200 (241,904,135 bytes). Confirmed.
- NFI Scotland 2025 record quotes "Sentinel 2 imagery taken during spring/summer 2025". Confirmed.
- LiDAR Phase 6 DTM record licence is OGL v3.0. Confirmed.
- Earth Search STAC for the Galloway box on 2025-04-08 returns six items, cloud 0.001% to 3.8%. Confirmed.
