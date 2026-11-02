# Label and ancillary datasets (L1 part A)

Task: L1 (part A, datasets). Author: `labels` module agent. Fetch date: 2026-11-02.

Purpose: candidate label and ancillary layers for a Sentinel-2 segmentation model of peatland and woodland condition in Scotland.

## How claims are marked

- **VERIFIED**: the primary page or the official catalogue record was fetched with `curl -sL` during this run on 2026-11-02, and the claim is stated there. The URL is given.
- **UNVERIFIED**: from background knowledge, or the fetch failed. Check before relying on it.

Most metadata below comes from the official catalogue records on data.gov.uk. These are harvested from SpatialData.gov.scot (the Scottish Government INSPIRE catalogue) or published by the Forestry Commission. The data.gov.uk CKAN API was queried as `https://www.data.gov.uk/api/action/package_search?q=<title>`. Those records count as primary metadata because the data owner publishes them.

---

## 1. NatureScot Carbon and Peatland Map 2016 (CPM2016)

Catalogue record: https://www.data.gov.uk/dataset/fb36e642-a7b2-491d-8ad8-24b63e855de9/carbon-and-peatland-2016-map
Owner page: https://www.nature.scot/professional-advice/planning-and-development/planning-and-development-advice/soils/carbon-and-peatland-2016-map (first fetch returned HTTP 403; a retry with a browser user agent returned HTTP 200)

| Field | Value | Status |
|---|---|---|
| What it labels | Likely presence of carbon-rich soil, deep peat and priority peatland habitat. It is a "predictive tool ... at a coarse scale" and a "high-level planning tool". | VERIFIED (catalogue record) |
| Class scheme | Five peat classes. Class 1 and 2 are solely or mainly peat soil with peatland priority habitat. Class 5 is peat soil over 50 cm currently without peatland vegetation. | VERIFIED (owner page) |
| Class scheme detail | Class 3 is mainly not priority peatland habitat but has some peat soil. Class 4 is mainly not peatland habitat, with isolated peat. Class 0 is mineral soil, class -1 is unknown soil, class -2 is non-soil. | UNVERIFIED (the owner-page extract was cut off before these classes) |
| Source / derivation | Built from existing soil and vegetation data: James Hutton Institute soil data at 1:25,000 and 1:250,000 scale, plus **Land Cover Scotland 1988**. | VERIFIED (catalogue record) |
| Resolution | Polygons derived from 1:25k and 1:250k soil maps. The effective minimum mapping unit is coarse (hectares to tens of hectares). | Scale VERIFIED; MMU UNVERIFIED |
| Vintage | Published 2016. The vegetation input dates from 1988. | VERIFIED (title and catalogue notes) |
| Licence | The full CPM record has an empty licence field. The sister "Wind Farm Spatial Framework" record states OGL v3.0 ("Contains SNH information licensed under the Open Government Licence v3.0"). | VERIFIED (both catalogue records); full-map licence UNVERIFIED |
| Access | Shapefile `https://gis-downloads.nature.scot/CPM2016_SCOTLAND_SHP_27700.zip`, file geodatabase `..._GDB_27700.zip`, and WMS/WFS at `https://ogc.nature.scot/geoserver/renewables/ows`. All in EPSG:27700. | VERIFIED (catalogue resources; the SHP zip answered an HTTP HEAD request with 200 and type `binary/octet-stream` on 2026-11-02) |

Caveats:
- It shows where peat probably is, not peat condition. It cannot separate degraded, drained or restored peat.
- The vegetation layer is 1988 aerial-photo land cover. Anything afforested or restored since then is wrong in the habitat part.
- Use it only as a prior or mask, not as pixel-level training labels at 10 m.
- No satellite circularity (it predates Sentinel-2 and uses no satellite input). VERIFIED from the stated inputs.

Related: the **Muirburn Peatland Map** (NatureScot, 2025) is a 50 m raster with the classes Peatland (peat 50 cm or deeper), Uncertain (under 50 cm), Non-peatland and Non-soil. It comes from JHI spatial disaggregation of the 1:250k soil map plus Soil25K V10d. Licence: **UK Non-Commercial Government Licence v2**. Download: `https://gis-downloads.nature.scot/MPM2025_SCOTLAND_GDB_27700.zip`. VERIFIED from https://www.data.gov.uk/dataset/788362ae-c13f-4b64-a590-923479f17450/muirburn-peatland-map. Caveat: the non-commercial licence matters if the repo is public or reused commercially.

---

## 2. National Forest Inventory (NFI) woodland map, Forestry Commission

Catalogue record (Scotland 2025): https://www.data.gov.uk/dataset/c50cddf4-cdf0-42fb-8ac1-eae0cb6ef5ad/national-forest-inventory-scotland-2025
Earlier edition: https://www.data.gov.uk/dataset/2e3526a4-5393-41ea-8a6f-2ef9e0eeea94/national-forest-inventory-scotland-2024

| Field | Value | Status |
|---|---|---|
| What it labels | All forest and woodland over 0.5 ha with at least 20% canopy cover (or the potential to reach it) and at least 20 m wide. **Includes new planting, clearfell, windblow and restock.** Assumed woodland and low-density areas can be as small as 0.1 ha. | VERIFIED |
| Class scheme | Woodland interpretation (IFT) categories such as conifer, broadleaved, mixed, young trees, felled, ground prep, windblow and low density. | UNVERIFIED (attribute list not fetched) |
| Resolution | Vector. Minimum mapping unit 0.5 ha (0.1 ha for some categories). Minimum width 20 m. Boundaries are referenced to OS MasterMap. | VERIFIED |
| Vintage | Updated every year. The 2025 edition covers 2024-04-01 to 2025-03-31; the 2024 edition covers 2023-04-01 to 2024-03-31. | VERIFIED |
| Licence | Open Government Licence. "No public access constraints." | VERIFIED |
| Access | Forestry Commission open data hub: https://data-forestry.opendata.arcgis.com/datasets/de94c77f724345e69283238f75c69848/about (Scotland 2025) | VERIFIED (link in record); download not tested |

Caveats:
- **Satellite circularity: VERIFIED.** The record says canopy changes were identified "on Sentinel 2 imagery taken during spring/summer 2025 or colour orthorectified aerial photographs". The woodland and felled labels are therefore partly derived from the same sensor we train on. Agreement with NFI overstates skill, so spatially held-out hand labels (SPEC D5) are essential.
- The 20 m width and 0.5 ha minimum mean small woods, shelterbelts and scattered trees are missing. At 10 m pixels, edges will be noisy.
- "Woodland" includes felled and ground-prep land with no current canopy. To get a canopy class, drop the felled and ground-prep categories.

---

## 3. Peatland ACTION restoration records (NatureScot)

Catalogue records:
- Completed restoration footprints: https://www.data.gov.uk/dataset/cfd2d7e1-49ad-4584-b3b6-9815c73f6cbd/peatland-action-completed-restoration-footprints
- Completed restoration site centroids: https://www.data.gov.uk/dataset/37d796eb-7219-4bc8-8370-98f72f063ee1/peatland-action-completed-restoration-project-site-centroids
- Feasibility study polygons: https://www.data.gov.uk/dataset/9f7ebc24-5f3e-4bfa-8bca-9233ac312b47/peatland-action-feasibility-study-polygons
- Peat depth / condition points: https://www.data.gov.uk/dataset/ddda0dff-5213-4e77-b527-e42eeb6dd413 (record name `peatland-action-peat-depth-condition`)

| Field | Value | Status |
|---|---|---|
| What it labels | Where peatland restoration was **completed**. The footprints are "created by buffering spatial data from restoration techniques applied on site". Attributes include the financial year end and the project area. | VERIFIED |
| Feasibility polygons | Either the survey area or the restoration footprint, with the year the study was completed and the hectares. | VERIFIED |
| Peat depth / condition | A consolidated set of peat depth measurements from Peatland ACTION surveys. Surveys were done "at a spatial resolution of 100m" (point grid). | VERIFIED |
| Class scheme | No land-cover classes. Each record is an event with a date (restored in financial year X). The restoration technique may be an attribute (for example ditch blocking, reprofiling, forest-to-bog). | Event/date VERIFIED; technique attribute UNVERIFIED |
| Resolution | Vector polygons buffered from works lines and points, so boundaries are approximate. | VERIFIED (buffering stated) |
| Vintage | Programme-wide (Peatland ACTION has run since about 2012). The footprint and centroid records were updated 2026-08-07. | Update date VERIFIED; programme start UNVERIFIED |
| Licence | OGL v3.0 ("Available under the Open Government Licence"). | VERIFIED |
| Access | `https://gis-downloads.nature.scot/PACRPG_SCOTLAND_SHP_27700.zip` (footprints), `PACRPT_...` (centroids), `PAFSPG_...` (feasibility), and WFS `https://ogc.nature.scot/geoserver/peatlandaction/wfs` | VERIFIED (catalogue resources; the PACRPG zip answered HEAD with 200 on 2026-11-02) |

Caveats:
- This is the best open source of **rewetting change events with dates**, which suits change-pair selection (L2).
- A footprint marks where works happened, not a condition class. Restored bog can look spectrally unchanged for years, and buffering pulls in surrounding land.
- No satellite circularity (based on field works records). VERIFIED from the stated derivation.
- The NatureScot owner page https://www.nature.scot/climate-change/nature-based-solutions/peatland-action returned HTTP 403 in this run, so the programme facts are UNVERIFIED.

---

## 4. Native Woodland Survey of Scotland (NWSS)

Catalogue record: https://www.data.gov.uk/dataset/da3f8548-a130-4a0d-8ddd-45019adcf1f3/native-woodland-survey-of-scotland-nwss
Owner page: https://www.forestry.gov.scot/forests-environment/biodiversity/native-woodlands/native-woodland-survey-of-scotland-nwss (HTTP 200, but the content is rendered by JavaScript and no text was extracted, so it is not used as a source)

| Field | Value | Status |
|---|---|---|
| What it labels | A baseline survey of all native woodland, nearly-native woodland and PAWS (Plantations on Ancient Woodland Sites) in Scotland, giving the type, extent and **condition** of those woods. | VERIFIED |
| Class scheme | Several layers. **Nativeness**: percentage of native canopy, in 5% classes. **Habitat**: priority woodland type plus NVC woodland community; open land typed by UK BAP broad habitat. **Canopy cover**: 10% classes; at least 20% is needed to count as woodland. Also canopy structure, and others not listed here. | VERIFIED |
| Resolution | Field-survey polygons (vector). Minimum polygon size not checked. | Vector VERIFIED; MMU UNVERIFIED |
| Vintage | Fieldwork about 2006-2013, published 2014. Record update frequency is "notPlanned", so it is a one-off baseline. | Frequency VERIFIED; fieldwork years UNVERIFIED |
| Licence | "No limitations on public access". Needs OS attribution (Licence number 100043970). The licence field does not name OGL explicitly. | VERIFIED (record); OGL status UNVERIFIED |
| Access | Scottish Forestry open data hub https://open-data-scottishforestry.hub.arcgis.com/ and WMS `https://ogc.nature.scot/geoserver/scottishforestry/ows` | VERIFIED (links in record) |

Caveats:
- This is the only national source that **labels woodland condition and nativeness** from field survey. That makes it valuable for a native broadleaf vs conifer plantation split.
- It is 10 to 20 years old, so felling and planting since then is not captured. Cross-check with NFI 2025.
- No satellite circularity (field survey). VERIFIED from "baseline survey".

---

## 5. UKCEH Land Cover Map (LCM2020 to LCM2024)

Catalogue records:
- LCM2023 10 m pixels: https://www.data.gov.uk/dataset/f22069c3-8caf-49e0-8651-c409aefddb54/land-cover-map-2023-10m-classified-pixels-gb and https://catalogue.ceh.ac.uk/documents/7727ce7d-531e-4d77-b756-5cc59ff016bd
- LCM2024 10 m pixels: https://www.data.gov.uk/dataset/11b7c1de-6443-4948-afe3-3319421092dd/land-cover-map-2024-10m-classified-pixels-gb
- LCM2023 25 m rasterised parcels: https://catalogue.ceh.ac.uk/documents/ab10ea4a-1788-4d25-a6df-f1aff829dfff
- Product overview: https://www.ceh.ac.uk/data/ukceh-land-cover-maps

| Field | Value | Status |
|---|---|---|
| What it labels | Land cover for all of Great Britain. | VERIFIED |
| Class scheme | **21 UKCEH classes** based on UK BAP Broad Habitats. The classes include bog, heather, heather grassland, broadleaved woodland and coniferous woodland. | 21 classes / BAP VERIFIED; class names UNVERIFIED (list not fetched this run) |
| Resolution | 10 m pixels (LCM2020 onwards). A two-band GeoTIFF: band 1 is the most likely class, band 2 is a per-pixel probability from 0 to 100. Also 25 m rasterised parcels and vector parcels. LCM2017-2019 were 20 m. | VERIFIED |
| Vintage | Annual: 2020, 2022, 2023 and 2024 are each listed as 10 m pixel products. | VERIFIED |
| Licence | **Not OGL.** "Licence terms and conditions apply". The vector parcels have "bespoke licensing conditions" and the UKCEH Data Licensing team negotiates a licence. Non-commercial vector use carries a £300 + VAT admin fee. Commercial use needs a paid licence. Whether a public portfolio repo may include derived 10 m pixel products is not confirmed. | VERIFIED (catalogue + ceh.ac.uk page); exact 10 m pixel terms UNVERIFIED |
| Access | EIDC order or download (`https://data-package.ceh.ac.uk/sd/7727ce7d-531e-4d77-b756-5cc59ff016bd.zip` for LCM2023 10 m) | VERIFIED (link in record). The zip answered an anonymous HEAD request with 200 `application/octet-stream` on 2026-11-02. Whether the server serves the full file without a licence step, and whether OGL-like reuse is allowed, is UNVERIFIED. |

Caveats:
- **Satellite circularity: high.** The 25 m record says LCM uses automated classification of seasonal surface-reflectance composites plus context layers (height, aspect, slope and more). For LCM2020 onwards the sensor is believed to be Sentinel-2 (UNVERIFIED this run). Training a Sentinel-2 U-Net on LCM labels mostly teaches it to copy LCM. Use it as a weak label or for comparison, never as the only test truth.
- The probability band can filter out low-confidence pixels, which gives cleaner weak labels.
- Bog and heather confusion is a known weakness of satellite land cover in upland Scotland (UNVERIFIED for LCM specifically).
- The licence is the main blocker for a public repo. Raise it with the orchestrator before use.

---

## 6. Scottish Remote Sensing Portal: Scottish Public Sector LiDAR (Phases 1 to 6)

Portal: https://remotesensingdata.gov.scot/ (the `/data` list is rendered by JavaScript; https://remotesensingdata.gov.scot/about was fetched)

| Phase | Capture dates | Area | Catalogue record | Status |
|---|---|---|---|---|
| 1 | Mar 2011 to May 2012 | 10 sites, 11,845 km2 | https://www.data.gov.uk/dataset/a4411d01-c95d-44d5-9db0-db8c2b94bc5a/lidar-for-scotland-phase-1-dtm | VERIFIED |
| 2 | 29 Nov 2012 to 18 Apr 2014 | 66 sites, 3,516 km2 | https://www.data.gov.uk/dataset/98ea775e-ca28-4378-b6ab-bd6f70511da2/lidar-for-scotland-phase-2-dtm | VERIFIED |
| 3 | Mar to May 2020 | not stated in the extract | https://www.data.gov.uk/dataset/f5055655-3b61-4767-9796-ab80f53909fb/lidar-for-scotland-phase-3-dtm | Dates VERIFIED |
| 4 | 2017 to 2019 (captured for SP Energy Networks, bought by Scottish Government in 2020) | 17,945 km2; 4 points per m2 | https://www.data.gov.uk/dataset/c55d7eb9-2222-4804-827a-b00a9d0565c3/lidar-for-scotland-phase-4-dtm | VERIFIED |
| 5 | 28 May 2020 to 12 Apr 2021 | not stated in the extract | https://www.data.gov.uk/dataset/78daa005-99d9-4f9f-ac0a-29dd52c59d66/lidar-for-scotland-phase-5-dtm | Dates VERIFIED |
| 6 | 1 Apr 2021 to 31 Mar 2022 | not stated in the extract | https://www.data.gov.uk/dataset/3f2900f6-46bd-4769-911c-135b39b6a8a2/lidar-for-scotland-phase-6-dtm | Dates VERIFIED |

| Field | Value | Status |
|---|---|---|
| What it gives | DTM, DSM and LAS point clouds. Ancillary data (terrain, canopy height from DSM minus DTM), not class labels. | VERIFIED (products named in records) |
| Resolution | Phase 1-2 DTM about 1 m (or 0.5 m), later phases 0.5 m. | UNVERIFIED |
| Licence | "All data is made available under the Open Government Licence v3 unless otherwise stated" (portal About page). Each phase needs its own attribution, for example "Crown copyright Scottish Government and Fugro (2020)". The Phase 2 LAS record says "Fugro retain the commercial copyright". | VERIFIED |
| Access | Portal tile download, plus WMS `https://srsp-ows.jncc.gov.uk/ows` | VERIFIED (links in record) |
| Coverage | **None of the phases is national.** Every record says "does not have full national coverage". | VERIFIED |

Caveats:
- Before fixing the training region, check that it falls inside a LiDAR phase footprint. Phase 4 follows SPEN power lines, so its coverage is patchy and linear.
- DSM minus DTM gives a canopy height model, which is a strong **independent** (non-Sentinel) check for woodland vs open ground, and for felling between phases.
- The LiDAR capture dates do not match the Sentinel-2 dates. Use LiDAR only for slow-changing things (terrain, drainage ditches) or as a dated snapshot.

---

## 7. Copernicus DEM GLO-30

Sources: https://registry.opendata.aws/copernicus-dem/ and https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/DEM.html

| Field | Value | Status |
|---|---|---|
| What it gives | A global digital surface model (elevation). Ancillary data for slope and TWI (task P6), not labels. | VERIFIED (DEM) |
| Resolution | 30 m (GLO-30). GLO-90 is used where GLO-30 Public tiles are missing. Voids are infilled with ASTER, SRTM, ALOS World 3D and others. | VERIFIED (CDSE docs) |
| Vintage | **2021 release** on AWS. Static, with no updates "except GLO-30 Public can be updated if the public tile list changes". | VERIFIED (AWS registry) |
| Licence | "GLO-30 Public and GLO-90 are available on a free basis for the general public under the terms and conditions of the Licence" (Copernicus DEM licence). | VERIFIED (AWS registry) |
| Access | Cloud-Optimized GeoTIFFs on AWS Open Data with no authentication (`s3://copernicus-dem-30m/`). Through Sentinel Hub, COPERNICUS_30 is restricted to authorised user categories. Use AWS. | COG on AWS VERIFIED; bucket name UNVERIFIED; Sentinel Hub restriction VERIFIED |

Caveats:
- It is a surface model (X-band radar, TanDEM-X), so forest canopy raises the elevation. Slope and TWI under plantations are biased. Underlying acquisitions date from about 2011-2015 (UNVERIFIED).
- At 30 m it misses peat drainage ditches and hagging. LiDAR is far better where it exists.

---

## 8. Forestry felling and planting data (FLS, Scottish Forestry, Forestry Commission)

| Dataset | What it labels | Licence / access | Status |
|---|---|---|---|
| **Felling Permissions and Licences (2012 onwards)**, Scottish Forestry. https://www.data.gov.uk/dataset/f67d104f-b313-48d9-b56b-853f34e82c2a/felling-permissions-and-licences-2012-onwards | Areas with felling permission (polygons) | "No limitations on public access", OS attribution required. Scottish Forestry hub https://open-data-scottishforestry.hub.arcgis.com/ | VERIFIED (record). Caveat: a permission is **not** proof that felling happened or when it did. |
| **Felling Licence Applications (1998-2011)**. https://www.data.gov.uk/dataset/28b6cc26-d5ae-4f3f-a7e8-635e32ecdeed/felling-licence-applications-1998-2011 | Older felling applications | As above | VERIFIED (record) |
| **National Forest Estate Subcompartments (Scotland)**, the FLS sub-compartment database (SCDB) and stock maps. https://www.data.gov.uk/dataset/49646283-243a-4616-a8a4-f00cacb84e79/national-forest-estate-subcompartments-scotland-2019 and the GB version https://www.data.gov.uk/dataset/3d3e3c5a-d19b-498b-8e14-d8aca26ae34b/national-forest-estate-subcompartments-gb | FLS's authoritative inventory of its own estate. Per-subcompartment attributes probably include species, planting year and land use (felled, open). | "No limitations on public access", OS attribution required | Record VERIFIED; attribute list UNVERIFIED; "Scotland 2019" is a dated edition, and whether a newer Scottish edition exists is UNVERIFIED |
| **SFGS Sub-Compartments** (Scottish Forestry Grant Scheme) and **FGS** woodland creation. https://www.data.gov.uk/dataset/a3d2d5a2-9d2f-45d4-be7f-d141c924dabf/sfgs-sub-compartments | Grant-funded planting areas | "No limitations on public access" | VERIFIED (record); FGS creation record not fetched (UNVERIFIED) |
| **New planting & restocking 1976-2020**, Forestry Commission statistics. https://www.data.gov.uk/dataset/935abbbc-805f-4a58-b88f-26f14884237b/new-planting-restocking-1976-to-2020 | National **tables** only (XLSX), not spatial | OGL v3.0 | VERIFIED. Not usable as pixel labels. |

Caveats:
- FLS subcompartments cover only the national forest estate (publicly managed land), so private forestry is absent. The estate is large in Scotland.
- Grant and permission polygons are plans. Pair them with NFI "felled" categories or Sentinel-2 / LiDAR evidence before treating them as events.
- No satellite circularity in the administrative records.

---

## 9. Habitat Map of Scotland (HabMoS), NatureScot

Catalogue records:
- HabMoS: https://www.data.gov.uk/dataset/e934a9ff-0aca-4bf1-a3c4-b64180d0ac60/habitat-map-of-scotland-habmos
- NVC polygons, non-overlapping, single dominant code: https://www.data.gov.uk/dataset/c2fb8003-4e54-4c75-8f91-6f7408e0d419/habmos-nvc-habitat-polygons-non-overlapping-no-mosaics
- Mountain woodland restoration sites (128 sites, 10,844 ha): https://www.data.gov.uk/dataset/f6b35b73-7504-40b7-aefa-bc77a0cfc5df/habmos-mountain-woodland-restoration-projects

| Field | Value | Status |
|---|---|---|
| What it labels | A compilation of NVC survey conversions, the saltmarsh, sand dune and shingle surveys, NWSS extracts, NFI, SG IACS and HES Historic Land-use Assessment. Habitats are coded as EUNIS and Annex I. | VERIFIED |
| Class scheme | EUNIS and Annex I codes, plus NVC in the NVC polygon products. Each habitat has a proportion value; mosaics are handled in separate "with mosaics" / "no mosaics" products. | VERIFIED |
| Resolution | Vector polygons from many surveys of mixed scale and date. | VERIFIED (mixed sources); scale UNVERIFIED |
| Vintage | NVC polygon products are "as of October 2023". The source surveys span decades. | VERIFIED (Oct 2023) |
| Licence | OGL v3.0 ("Contains NatureScot information licensed under the Open Government Licence v3.0"). | VERIFIED |
| Access | `https://gis-downloads.nature.scot/HABMOS_SCOTLAND_GPKG_27700.zip` (also SHP, GDB, GML), WMS/WFS `https://ogc.nature.scot/geoserver/habitatsandspecies/` | VERIFIED (links in record) |

Caveats:
- This is the best open source of field-surveyed **blanket bog vs wet heath vs dry heath** (NVC M17, M18, M19, M15 and others) where coverage exists. Coverage is patchy, with designated sites over-represented (UNVERIFIED).
- Survey dates vary from site to site. Check the date attribute for each polygon.
- No direct satellite circularity, although NFI and IACS inputs carry some.

---

## 10. Ranked recommendation per class

Assumed candidate classes (L2 decides the final list): **intact or near-natural peatland (bog)**, **degraded or modified peatland** (drained, eroding, heather-dominated), **woodland** (possibly split into conifer plantation and native broadleaf), **felled or recently disturbed woodland**, and **other** (grassland, arable, urban, water).

| Class | 1st choice | 2nd | 3rd | Notes |
|---|---|---|---|---|
| Peatland extent (bog vs not) | HabMoS NVC polygons (bog communities), where surveyed | CPM2016 classes 1-2, as a prior or mask | LCM 10 m "bog" class with high probability, as a weak label | CPM is coarse and uses 1988 vegetation. LCM is circular with Sentinel-2. Hand labels (D5) are the final test. |
| Peatland condition (degraded vs near-natural) | Hand labels from aerial and LiDAR (ditches and hags are visible in the LiDAR DTM) | Peatland ACTION footprints (restored, with date) | CPM2016 class 5 (peat soil without peatland vegetation) | **No open national condition map was found.** This class is the main label risk, and L2 may need to drop or merge it. |
| Woodland (all canopy) | NFI 2024 or 2025, excluding felled and ground-prep categories | LiDAR canopy height (DSM minus DTM) where covered | LCM 10 m woodland classes | NFI is partly made from Sentinel-2, so test on hand labels. |
| Conifer vs broadleaf / native | NFI interpretation categories | NWSS (nativeness, NVC woodland type) | FLS subcompartments (species) on the national estate | NWSS is old. Cross-check against NFI. |
| Felling (change event) | NFI year-on-year differences (felled category, 2024 vs 2025 editions) | FLS subcompartments plus Scottish Forestry felling permissions, confirmed on imagery | LiDAR canopy height between phases | Permissions are not events. |
| Planting / woodland gain (change) | NFI new-planting differences | SFGS / FGS grant polygons, confirmed on imagery | HabMoS mountain woodland restoration sites | Young planting is spectrally invisible for years. |
| Rewetting (change) | Peatland ACTION completed footprints (with financial year) | Peatland ACTION feasibility polygons (planned sites) | none | Spectral change may be small. Use for event dates, not pixel truth. |
| Other / background | LCM 10 m (grassland, arable, urban, water) | OS / NFI exclusion masks | none | Circularity matters less for the background class. |
| Ancillary (terrain) | Scottish LiDAR DTM where covered | Copernicus GLO-30 (AWS, free licence) | none | GLO-30 is a DSM, so canopy is included. |

Licence summary: **OGL or open**: NFI, Peatland ACTION, HabMoS, CPM (wind-farm variant stated OGL), the LiDAR portal, the Scottish Forestry datasets (open access with OS attribution), and Copernicus DEM (free licence). **Restricted**: UKCEH LCM (bespoke licence) and the Muirburn Peatland Map (non-commercial only).
