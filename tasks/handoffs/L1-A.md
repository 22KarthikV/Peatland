# L1-A handoff (label and ancillary datasets)

**Branch / worktree:** `worktree-agent-a0e89d2177f9a2c69` at `D:\Projects\Peatland\.claude\worktrees\agent-a0e89d2177f9a2c69`

**Changed:** Added `docs/labels/datasets.md`. It covers nine dataset groups: CPM2016 (plus the Muirburn Peatland Map), NFI, Peatland ACTION, NWSS, UKCEH LCM, the Scottish LiDAR phases 1-6, Copernicus GLO-30, the FLS / Scottish Forestry felling and planting records, and HabMoS. For each it gives what it labels, the class scheme, resolution, vintage, licence, access URL and caveats, with every claim marked VERIFIED or UNVERIFIED. It ends with a ranked recommendation per class and a licence summary.

**Verified:**
- On 2026-11-02 I fetched the data.gov.uk CKAN catalogue records (harvested from SpatialData.gov.scot or published by the Forestry Commission or EIDC), the AWS Open Data registry, the CDSE DEM docs, catalogue.ceh.ac.uk, ceh.ac.uk and the remotesensingdata.gov.scot About page with `curl -sL`.
- An HTTP HEAD request returned 200 for the CPM, Peatland ACTION and HabMoS zips, the NFI and Scottish Forestry hubs, and the LCM2023 zip.
- No lint, type-check or tests apply: docs-only task, and the module has no code.
- The task's verify step is the orchestrator spot-checking 3 links.

**Decisions / gotchas:**
- nature.scot pages sometimes return HTTP 403 to curl. A browser user agent fixed it once, but the Peatland ACTION page still returned 403.
- forestry.gov.scot and remotesensingdata.gov.scot/data render with JavaScript, so curl gets no text. data.gov.uk metadata was used instead.
- **NFI is partly derived from Sentinel-2** (stated in the record). **LCM is derived from satellite imagery**, Sentinel-2 assumed (UNVERIFIED). Agreement with either one overstates skill, which makes the hand-labelled set (D5) essential.
- No open national **peatland condition** map was found. The degraded vs intact class is the biggest label risk for L2.
- None of the LiDAR phases is national. L2 must check that the region falls inside a phase footprint.

**Dependency or contract requests:**
- None for code.
- Licence decision needed before ML uses UKCEH LCM, which has bespoke, non-OGL terms. The public repo should not redistribute LCM-derived rasters until the licence is confirmed.
- The Muirburn Peatland Map is non-commercial only.

**Next:**
- L1 part B: regions, Sentinel-2 cloud cover and change events. Merge it with this file into `docs/labels/candidates.md`.
- Check the NFI attribute (IFT category) list and the LCM 21-class names.
- Pick a region inside LiDAR Phase 1/3/5/6 coverage that also has Peatland ACTION footprints and NFI felling.
