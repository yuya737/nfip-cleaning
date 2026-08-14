# Methods

This document describes the cleaning pipeline in the order the code runs
it (`src/run_pipeline.sh`). It is intended as the basis for the Methods
section of the Scientific Data descriptor: each step is described
precisely, and known limitations are stated explicitly rather than
omitted.

## Overview

```
FEMA OpenFEMA claims (raw)
        |
        v
[1] inflation adjustment  (FRED PCE price index -> 2021 dollars)
        |
        v
[2] triangulation  (block group boundary) n (ZCTA boundary) n (lat/lon box)
        |
        v
[3] pluvial date correction  (causeOfDamage == "4" claims only, vs. NOAA AORC precip)
        |
        v
data/interim/claims_pluvial_corrected.parquet
```

## Input data

| Source | What | Access | License / terms |
|---|---|---|---|
| FEMA OpenFEMA | FIMA NFIP Redacted Claims v2 (`FimaNfipClaims.parquet`) | Public bulk download, no authentication | US government work, public domain |
| FRED (St. Louis Fed) | Personal consumption expenditures price index, series `DPCERD3Q086SBEA` | Public CSV endpoint, no authentication | Public |
| US Census Bureau | Cartographic boundary shapefiles: block groups and ZCTAs, assigned per claim decade rather than one file per claim year (see Step 2) | Public bulk download | Public domain |
| NOAA | Analysis of Record for Calibration (AORC) v1.1, 30 arc-second (~800 m) / hourly | Public, `s3://noaa-nws-aorc-v1-1-1km`, no-sign-request | Public |

None of these sources are redistributed in this repository in raw form
(see `README.md`). Each pipeline step downloads or expects a locally
staged copy, documented at the point of use.

## Scope: CONUS only

This dataset covers the 48 contiguous states and the District of
Columbia. NFIP claims also exist for Alaska, Hawaii, Puerto Rico, and
other territories, which are excluded by design rather than dropped as a
side effect.

## Step 1: Inflation adjustment (`adjust_inflation.py`)

Every dollar-denominated claim field (paid amounts, coverage limits,
building/contents value, replacement cost) is rescaled to a fixed target
year's dollars using the FRED PCE price index, matched to each claim's
loss quarter. The default target year is 2021, configurable via
`--target-year`.

## Step 2: Triangulation (`triangulate_claims.py`)

FEMA's public claims extract deliberately coarsens location to protect
policyholder privacy. No street address is provided, and reported
latitude/longitude are rounded. Each claim ships with three independent,
imprecise location signals — census block group FIPS code, ZIP code, and
rounded lat/lon — each of which implies a region the claim must fall
within. Triangulation intersects whichever of these three are available
into a single polygon per claim. This places a strict upper bound on the claim's true
location, typically substantially smaller than any one source's region
alone.

### Block-group and ZCTA shapefile assignment

Census block-group and ZCTA boundaries are redrawn each decennial cycle,
so the default assumption would be to match each claim to the boundary
vintage in effect at its loss year. Testing against the real claims data
does not support that assumption. Matching `censusBlockGroupFips` values
against the official 2010 decennial block-group release yields a 92-100%
match rate for claims in every decade back to the 1970s, including 1978 —
a full decade before block groups existed as a nationwide census
geography. This indicates that FEMA's geocoding retroactively assigns a
recent-vintage boundary to each record at processing time, rather than
using the historical loss-year boundary. There is also no sharp cutover
to the newer (2020) vintage: 77.7% of block-group GEOIDs are identical
between the 2010 and 2020 releases, and among the 23% that changed, the
match rate against the 2020 release increases gradually with loss year
rather than switching at a specific year — even claims with a 2020s loss
year are still approximately 10% exclusively-matched to the 2010 vintage.

A per-claim membership test against every available shapefile release
(using whichever release matches) would recover more matches than any
decade-based rule, since the transition between releases is gradual
rather than sharp. This approach was prototyped and confirmed to recover
matches that a decade-based rule misses in every decade. An explicit
per-decade shapefile mapping was used instead. `config.yaml`'s
`block_group_shapefiles` maps each claim decade (`yearOfLoss // 10 * 10`)
directly to one shapefile, with no fallback to another release if the
assigned decade's shapefile does not match. This is a deliberate
precision/auditability tradeoff: an explicit per-decade table can be
stated and audited in a few lines ("claims from the 1970s through the
2010s use the 2010 Census block-group release. 2020s claims use the 2020
release"), whereas per-claim membership testing requires justifying a
data-driven selection with no documented counterpart in FEMA's own
process. The measured cost of this choice is that testing both the 2010
and 2020 releases per claim would recover approximately 88,000 additional
CONUS claims (3.4%) that match only the release the decade table does not
assign to their era. `decade_used` (see `docs/data_dictionary.md`)
records the decade bucket applied to each claim. This value is a lookup
key into `block_group_shapefiles` and `zcta_shapefiles` independently,
not a single shared vintage: for a claim with `decade_used = 2000`, the
block-group lookup uses the 2010 release while the ZIP lookup uses the
2000 release, since the two configuration tables are not required to
agree.

Most decades in `block_group_shapefiles` point at the same file — the
official 2010 decennial release (Census's GENZ2010 release, in the
legacy "gz_" naming convention) — with only the 2020s decade assigned to
the 2020 release. Together, these two releases leave 1,017 CONUS claims
(0.04%) matching neither, a residual accepted without further
investigation. Adding the 1990 and 2000 census-cycle releases was tested
and found to reduce this residual by only a few thousand claims, not
justifying the added pipeline complexity.

Once a decade's shapefile is selected, the match is validated against
FEMA's rounded lat/lon rather than accepted on GEOID string match alone.
FEMA rounds reported coordinates to one decimal place, which guarantees
the claim's true location falls within a 0.1 x 0.1 degree box centered on
the reported point (the same box used as the third triangulation source —
see `create_lat_lon_rect`). A code that matches the decade's shapefile
GEOID but whose polygon does not overlap that box is treated as
spatially inconsistent and excluded from the claim's intersection.
`block_group_match_status` and `zip_match_status` record this outcome per
claim.

ZCTAs exist only from the 2000 census onward. There is no pre-2000 ZCTA
release, official or otherwise. This was verified directly: Census's
`PREVGENZ` archive contains no pre-2000 ZIP-tabulation product, and the
pre-ZCTA-era `TIGER1992` files contain raw street-segment network data
with a ZIP attribute per segment rather than assembled area polygons — a
legacy format Census's own documentation recommends against using
directly, pointing instead to NHGIS (https://www.nhgis.org) for
historical boundary needs. Unlike `censusBlockGroupFips` and
`censusTract`, `reportedZipCode` is also not a field FEMA's geocoding
service assigns. Per FEMA's own data dictionary it is raw data "as
reported by WYO partners," so the retroactive-geocoding rationale used
for the block-group vintage above does not apply to ZIP-vintage
selection. `zcta_shapefiles` accordingly has no entry before the year
2000. Pre-2000 claims skip the ZIP source entirely
(`zip_match_status = "no_shapefile_for_decade"`) and fall back to block
group and lat/lon only. This is recorded transparently per claim via
`geometry_sources` / `n_geometry_sources`.

Unlike the block-group case, ZCTA-release choice has little effect on
match rate: testing `reportedZipCode` against the 2000, 2010, and 2020
ZCTA releases shows all three track each other closely within any given
decade, and a strict per-decade rule loses only approximately 1,900
claims relative to testing all three — negligible relative to the
block-group tradeoff above. `zcta_shapefiles` nonetheless assigns each
decade from 2000 onward its own contemporaneous release (for example,
2000s claims use the 2000 release) for methodological consistency, not
because it materially increases match rate. The larger, unrelated finding
for 1970s and 1980s claims is that a substantial share of
`reportedZipCode` values are empty strings — frequently paired with
`state == "UN"` (unknown) — rather than real ZIP codes that failed to
match a ZCTA. This is a data-completeness gap in the source records, not
a boundary-matching problem.

A claim's three location sources can each individually pass validation
against the lat/lon box while failing to overlap one another — for
example, a block group and a ZCTA that are each individually plausible
given the rounded coordinates but that do not share any area. This is a
distinct failure mode from the vintage-mismatch problem described above.
`geometry_is_empty` flags this case explicitly rather than allowing a
zero-area polygon to be published silently.

## Step 3: Pluvial date correction (`build_aorc_pixel_day_index.py` / `fetch_aorc_daily_max.py` / `correct_pluvial_dates.py`)

NFIP's `causeOfDamage` field uses code `"4"` ("Other") as a catch-all
that in practice captures most rain-driven (pluvial) flood claims, but it
is a coarse proxy rather than a clean pluvial-only label. Some
non-pluvial claims may be coded `"4"` for unrelated reasons. This code is
used as the best available filter, not as ground truth, and this
limitation applies to the correction described below rather than being
resolved by it.

For each claim coded `"4"`, the reported `dateOfLoss` is checked against
whether it coincides with a day of meaningful precipitation (maximum
hourly APCP exceeding `min_hourly_precip_mm`, default 5.0 mm) at the
claim's triangulated location, using NOAA's public AORC v1.1 archive
(30 arc-second / ~800 m grid, hourly, CONUS, 1979-present). If it does not, a
`search_window_days` window (default +/- 7 days) is searched for the
wettest day, and if that day clears the threshold it is treated as the
corrected date. The original `dateOfLoss` is never overwritten.
`correctedDateOfLoss` and `pluvialCorrectionStatus` record the outcome
per claim (see `docs/data_dictionary.md` for status values), allowing
downstream users to decide whether to apply the correction.

Day boundaries are evaluated in each claim's own local time zone rather
than a single national reference clock. `timezone_utils.py` resolves the
IANA time zone for every AORC pixel referenced by a claim (via
`timezonefinder`, computed once per unique pixel), and
`fetch_aorc_daily_max.py` converts each local calendar day to the correct
UTC hour range using that zone, correctly handling daylight saving
transitions (a local day spans 23 or 25 hours, not always 24, across a
spring-forward or fall-back date) rather than assuming a fixed offset. A
local day's hours can also span two different UTC calendar years, since
every US time zone is behind UTC and a December 31 local day's final
hours fall in UTC on January 1 of the following year. The fetch step
groups by the UTC year each hour actually falls in, rather than by the
request's local date, so these hours are not dropped.

**Coverage.** AORC begins 1979-01-01 for CONUS. Pluvial claims before
this date are not evaluated (`pluvialCorrectionStatus =
"before_aorc_coverage"`) and retain their reported date rather than being
dropped from the dataset. Pluvial correction is also restricted to CONUS:
claims whose location falls outside the AORC CONUS grid extent
(approximately 20-55N, 130-60W — including Puerto Rico and other
territories) receive `pluvialCorrectionStatus =
"outside_aorc_grid_extent"` and are likewise retained with their reported
date.

**Data volume.** Rather than downloading AORC's full hourly CONUS
archive (on the order of multiple terabytes for the full 1979-present
period of record), the pipeline computes the substantially smaller set of
distinct (pixel, day) pairs actually referenced by any pluvial claim's
search window (`build_aorc_pixel_day_index.py`), fetches only those
values via vectorized reads against the public Zarr store
(`fetch_aorc_daily_max.py`), and retains only the daily maximum rather
than the full hourly series. Claims sharing a flood event collapse onto
overlapping pixel/day requests, so the deduplicated index is
substantially smaller than `n_claims * window_size`. The achieved
reduction is printed at run time.

## Known limitations

- **CONUS only.** Alaska, Hawaii, Puerto Rico, and other territories are
  excluded (see "Scope: CONUS only" above) because shapefile and AORC
  coverage do not extend there, not because their claims are lower
  quality.
- A small residual of CONUS claims with a block-group FIPS (0.04%, 1,017
  claims) matches neither the 2010 nor the 2020 block-group release, even
  after restricting to CONUS.  Reasosns TBD.
- `causeOfDamage == "4"` is a coarse proxy for "pluvial," not a clean
  label (see Step 3).
- Pre-2000 triangulation cannot use a ZCTA boundary (see Step 2).
- Pre-1979 claims are not eligible for pluvial date correction, since
  AORC coverage begins in 1979 (see Step 3).
