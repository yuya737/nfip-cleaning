# NFIP Claims: Triangulation and Pluvial Date Correction

Code and derived data for a Scientific Data descriptor documenting a
cleaning pipeline for FEMA's National Flood Insurance Program (NFIP)
claims records. Starting from FEMA's public redacted claims extract, the
pipeline (1) inflation-adjusts monetary fields, (2) triangulates a spatial
uncertainty polygon per claim from three independent, imprecise location
signals, and (3) corrects likely-erroneous reported dates for pluvial
(rain-driven) claims against NOAA's AORC precipitation reanalysis. See
`docs/methods.md` for the full methodology and `docs/data_dictionary.md`
for the output schema.

## Repository layout

```
src/            pipeline scripts, run in dependency order (see run_pipeline.sh)
docs/           methods write-up and data dictionary
data/raw/       downloaded source files (gitignored — see below)
data/interim/   intermediate pipeline outputs (gitignored) — current end-of-pipeline output lives here for now, see below
data/processed/ intended home for the final released dataset; not populated by the default run yet (NSI refinement is disabled — see docs/methods.md)
config.yaml.example   copy to config.yaml and fill in local paths
environment.yml
```

## Setup

```
conda env create -f environment.yml
conda activate nfip-cleaning
cp config.yaml.example config.yaml   # edit paths for your machine
```

`config.yaml` holds machine-local absolute paths (shapefile locations)
and is gitignored; every script reads paths through `src/paths.py`
rather than hardcoding them.

Census block-group and ZCTA shapefiles are not fetched automatically —
download them from the Census Bureau (cartographic boundary files) and
point `config.yaml` at them. AORC and the raw FEMA/FRED extracts *are*
fetched automatically by the pipeline (see `docs/methods.md` for exact
source URLs).

## Running the pipeline

```
src/run_pipeline.sh
```

restricted to pluvial claims (`causeOfDamage == "4"`) by default — that's
a flag, not a hardcoded choice, see below. Or run steps individually —
each reads its inputs from the previous step's output path
(`src/paths.py`), so once those exist a step can be re-run on its own:

1. `download_claims.py` — raw FEMA claims + FRED inflation series
2. `adjust_inflation.py` — inflation-adjust dollar fields
3. `triangulate_claims.py` — triangulate spatial uncertainty polygons
4. `build_aorc_pixel_day_index.py` — index of AORC bilinear-corner pixel/days needed for pluvial claims
5. `fetch_aorc_daily_max.py` — fetch hourly precip for those pixel/days from public AORC
6. `correct_pluvial_dates.py` — bilinearly interpolate the 4 corner pixels, then apply the date correction

`data/interim/claims_pluvial_corrected.parquet` is the current
end-of-pipeline output; `data/processed/` isn't populated by the default
run yet (see `docs/methods.md`'s Known limitations).

### Key flags

Several methodological choices in this pipeline are run-time flags
rather than fixed answers, deliberately — see `docs/methods.md` for why
each exists and the numbers behind the tradeoffs. Full list on any
script via `--help`; the ones worth knowing about:

- `triangulate_claims.py --block-group-strategy` / `--zcta-strategy`
  `{default,closest,most_recent,drop}` — which census boundary vintage a
  claim's block-group/ZIP code is checked against.
- `triangulate_claims.py --cause-of-damage <code>` — restrict to a single
  `causeOfDamage` code (`run_pipeline.sh` passes `4` for pluvial).
- `correct_pluvial_dates.py --min-hourly-precip-mm <value>` — override
  the uniform precipitation threshold for a single run.
- `correct_pluvial_dates.py --threshold-netcdf <path>
  [--threshold-netcdf-var <name>]` — use a spatially-varying threshold
  from a NetCDF file instead of a constant; mutually exclusive with
  `--min-hourly-precip-mm`.

## Why the data isn't redistributed raw

FEMA claims, Census shapefiles, and NOAA AORC are all public with no
redistribution restriction we're aware of, but we don't bundle them in
this repository — they're large, versioned by their own sources, and
`src/download_claims.py` / `docs/methods.md` document exactly where to
get them instead. What *is* bundled/released is the derived output in
`data/processed/`.

## License

The pipeline code in `src/` is released under the MIT License (`LICENSE`).
The derived dataset (`data/processed/`, once populated) is released
separately under CC-BY 4.0, matching Scientific Data's expectations for
openly licensed data. None of the upstream sources (FEMA, Census, NOAA)
require anything more restrictive than that.
