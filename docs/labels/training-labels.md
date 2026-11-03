# Training weak labels (M9 prep)

Built by `ml/src/ml/build_data.py` (`scripts/build_training_data.py`). These are weak, national-source labels for training only. The independent hand labels (L3) are the evaluation truth; they are never merged in here.

## Sources and licences

| Source | Used for | Fields read | Licence | Fetched via |
|---|---|---|---|---|
| NFI Scotland 2025 (Forestry Commission) | woodland, felled | `IFT_IOA` (also `CATEGORY`) | OGL v3.0, no access constraints | ArcGIS REST layer `National_Forest_Inventory_Scotland_2025/FeatureServer/0`, bbox query, EPSG:27700 GeoJSON (VERIFIED 2026-11-03, 1182 polygons in the south region) |
| NatureScot CPM2016 | bog prior | `importance` (class 1-5, 0, -1, -2) | OGL v3.0 (catalogue licence field is empty; see `datasets.md` section 1) | NatureScot WFS `renewables:carbonpeatlandmap2016`, bbox query (default), or the national zip `CPM2016_SCOTLAND_SHP_27700.zip` (242 MB, `--cpm-source zip`) |

Not used: UKCEH Land Cover Map (bespoke licence, Sentinel-2 circular) and the Muirburn Peatland Map (UK Non-Commercial Government Licence).

`importance` as the CPM class column was inferred on 2026-11-03 from the WFS attribute list and value pairs (blanket bog polygons carry 1 and 2, mineral 0, non-soil -2, plantation on peat 5). It is not documented by NatureScot in the pages fetched. If a local shapefile uses another name, pass `--cpm-column`.

## Class rules

NFI `IFT_IOA` values (live list, 2026-11-03): Agriculture land, Assumed woodland, Bare area, Broadleaved, Conifer, Coppice, Coppice with standards, Failed, Felled, Grassland, Ground prep, Low density, Mixed mainly broadleaved, Mixed mainly conifer, Open water, Other vegetation, Power line, Quarry, River, Road, Shrub, Urban, Windblow, Windfarm, Young trees.

| NFI group | Values | Result |
|---|---|---|
| woodland | Conifer, Broadleaved, Mixed mainly conifer, Mixed mainly broadleaved, Coppice, Coppice with standards | `woodland` (2), whatever CPM says |
| felled | Felled, Ground prep, Windblow | `felled_or_burnt_bare` (3), whatever CPM says |
| uncertain | Young trees, Assumed woodland, Low density, Shrub, Failed, and any new value | 255 |
| hard other | Open water, River, Urban, Road, Quarry, Windfarm, Power line, Agriculture land | `other` (0) |
| soft other | Grassland, Bare area | `other` if CPM is not bog; 255 where CPM says bog or is ambiguous (the sources disagree) |
| neutral / no polygon | Other vegetation, or outside any NFI polygon | decided by CPM below |

CPM2016 classes: 1 and 2 are bog candidates; 0, 3, 4 and -2 are other; 5 (peat soil without peatland vegetation, mostly afforested or improved) and -1 (unknown soil) are ambiguous and give 255. Where neither source has a polygon (sea, estuary, gaps) the pixel is `other`.

So `bog` = CPM class 1-2 where NFI does not say woodland, felled, uncertain or soft-other. Plantation on mapped deep peat becomes `woodland` (the CPM vegetation layer is from 1988). Pixels are burned at their centre (no `all_touched`), with overlaps resolved so that "uncertain" wins.

Finally, every pixel within 1 km of the Glen Trool box `[-4.55, 55.05, -4.30, 55.30]` is set to 255. The two default regions are already 2 km or more away; the mask protects custom `--bbox` runs.

## Measured class balance (2026-11-03, synthetic grid on the real vectors)

| Region | bbox (W, S, E, N) | other | bog | woodland | felled/bare | unlabelled |
|---|---|---|---|---|---|---|
| galloway-south | -4.52, 54.90, -4.20, 55.03 | 46.0 % | 3.2 % | 22.4 % | 18.5 % | 9.8 % |
| galloway-west | -4.80, 55.05, -4.60, 55.25 | 28.8 % | 3.9 % | 34.2 % | 18.4 % | 14.7 % |

Bog is rare because most CPM bog is under plantation or felled coupes. The U-Net uses inverse-frequency class weights (cap 50). Check the printed class counts before spending GPU time.

## Caveats

- **Circularity:** NFI canopy changes were identified partly on Sentinel-2 imagery. Training on it and scoring on it overstates skill. Report hand-label scores.
- **Edges:** NFI is a 0.5 ha minimum mapping unit and CPM is hectare-scale, so class boundaries are noisy at 10 m. No boundary erosion is applied.
- **Vintage:** NFI 2025 covers 2024-04-01 to 2025-03-31, the default composite window is 2025-04-01 to 2025-05-31, and felling after 31 March 2025 is not in the labels.
- **Season:** the model trains on a spring median composite but the demo uses single-date March and April scenes. Expect a domain shift; M10 should report it.
