# ADR 0003: Region, classes, demo site and date pair

Status: accepted (2026-11-02, user confirmed "Galloway" in chat). Evidence: `docs/labels/candidates.md` and its part files.

## Decision
- **Training region:** Galloway / Southern Uplands, a few hundred km2 chosen outside the demo box with a buffer so no training tile overlaps the burn. S2 tiles 30UUF, 30UVF, 30UUG, 30UVG (four-tile corner, needs a mosaic).
- **Held-out demo site:** Glen Trool to Loch Doon burn, bbox about `[-4.55, 55.05, -4.30, 55.30]` (wildfire 3-7 Apr 2025, about 65 km2).
- **Demo date pair:** 2025-03-19 (8.1% cloud) vs 2025-04-08 (0-4% cloud; 2025-05-16 as a second "after").
- **Classes (initial, 4):** bog (peatland), woodland, felled or burnt-bare, other. Degraded peat is optional and only added if hand labels (L3) support it, because no open national condition map exists.
- **Label sources:** peat extent from HabMoS NVC polygons with CPM2016 as a coarse prior; woodland from NFI Scotland 2024/2025 (partly Sentinel-2-derived, so evaluate on hand labels); felled/burnt-bare from NFI differences, Peatland ACTION dates and dNBR; terrain from Copernicus GLO-30 (Galloway LiDAR phases 1 and 3 only partly known). UKCEH LCM is not used (licence).

## Fallback
Cairngorms / Deeside, with the 2026 Glenmore/Abernethy burn (2026-07-08 vs 2026-10-18) held out. Flow Country is rejected for cloud and lack of woodland classes.

## Open items
Burn perimeter from a primary source or dNBR; Peatland ACTION and NFI/NWSS polygons inside the region; LiDAR extent in the demo box. Class list is finalised in C1.
