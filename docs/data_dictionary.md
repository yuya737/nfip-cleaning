# Data dictionary

Current end-of-pipeline output: `data/interim/claims_pluvial_corrected.parquet`.

All original columns from the FEMA OpenFEMA `FimaNfipClaims` extract are
preserved unchanged (see FEMA's own data dictionary for those:
https://www.fema.gov/openfema-data-page/fima-nfip-redacted-claims-v2).
The columns below are the ones this pipeline adds, grouped by the step
that produces them — see `docs/methods.md` for what each step does.

## Added by inflation adjustment (`adjust_inflation.py`)

| Column | Type | Description |
|---|---|---|
| `{field}Real2021` | float | Each of the 15 dollar fields listed in `DOLLAR_FIELDS` (paid amounts, coverage limits, building/contents value, replacement cost), rescaled to 2021 dollars via the FRED PCE price index. |
| `amountPaid` | float | Sum of the three `amountPaidOn*Claim` fields, nominal dollars. |
| `amountPaidReal2021` | float | Same sum, 2021 dollars. |
| `damageAmount` | float | `buildingDamageAmount + contentsDamageAmount`, nominal dollars. |
| `damageAmountReal2021` | float | Same sum, 2021 dollars. |

## Added by triangulation (`triangulate_claims.py`)

| Column | Type | Description |
|---|---|---|
| `geometry` | polygon (EPSG:5070) | Intersection of whichever of {block-group boundary, ZCTA boundary, lat/lon box} were available *and spatially validated* for the claim (see `docs/methods.md`) — a strict upper bound on the claim's true location. |
| `n_geometry_sources` | int | How many of the three sources contributed (0-3). Claims with 0, or whose sources intersect to an empty polygon, are dropped before this file is written. |
| `geometry_sources` | string | Which sources contributed, e.g. `"block_group+zip+latlon"`, `"block_group+latlon"`. |
| `geometry_is_empty` | bool or null | True if the sources in `geometry_sources` individually validated against the lat/lon box but don't overlap *each other* — see `docs/methods.md`. Rows where this is true are excluded from the file; kept here only for QA tooling run on the pre-drop intermediate table. |
| `decade_used` | int | The claim's decade bucket (`yearOfLoss // 10 * 10`, e.g. `2000`, `2010`, `2020`) — the lookup key into `config.yaml`'s `block_group_shapefiles` / `zcta_shapefiles`. Note this is *not* "which vintage" in the singular: block group and ZIP each look up this same decade in their own separate config table, so for e.g. `decade_used = 2000` the block-group lookup actually uses the 2010 shapefile while the ZIP lookup uses the 2000 shapefile (see `docs/methods.md`). Always populated, regardless of whether either source actually matched. |
| `block_group_match_status` | string | `not_found`, `validated`, `unvalidated_no_latlon`, `spatially_inconsistent`, or `no_shapefile_for_decade` (no `config.yaml` entry for this claim's decade) — see `select_validated_geometry` in `triangulate_claims.py`. |
| `zip_match_status` | string | Same status values as `block_group_match_status`. `no_shapefile_for_decade` is how pre-2000 claims are recorded, since `zcta_shapefiles` has no entry before 2000 — no ZCTA existed yet, so the ZIP source isn't attempted at all. |

## Added by pluvial date correction (`build_aorc_pixel_day_index.py` / `fetch_aorc_daily_max.py` / `correct_pluvial_dates.py`)

Only meaningful for `causeOfDamage == "4"` claims; see `docs/methods.md`
for why that code is used and its limits as a pluvial-claim filter.

| Column | Type | Description |
|---|---|---|
| `correctedDateOfLoss` | date | Reported `dateOfLoss` if it already coincided with meaningful precipitation, the wettest nearby day if a correction was made, otherwise unchanged from `dateOfLoss`. Always populated (equals `dateOfLoss` when not corrected or not evaluated). |
| `pluvialCorrectionStatus` | string | One of: `not_pluvial`, `before_aorc_coverage`, `outside_aorc_grid_extent`, `accepted_as_reported`, `corrected`, `no_qualifying_precip_in_window`, `no_aorc_data_in_window`. |
| `pluvialCorrectionMaxPrecipMm` | float | Max daily precipitation (mm) found at the claim's location on the date `correctedDateOfLoss` refers to. `NaN` when no AORC data was available in the search window. |

## Coordinate reference systems

- `geometry` is in **EPSG:5070** (Albers Equal Area), meters — the CRS
  used throughout the pipeline for area calculations.
- Original FEMA `latitude`/`longitude` fields remain in WGS84 (EPSG:4326)
  degrees, unchanged.
