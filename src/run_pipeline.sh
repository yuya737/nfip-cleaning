#!/usr/bin/env bash
# Runs the full pipeline end to end, in dependency order. Each step reads
# the previous step's output via paths.py / config.yaml, so steps can also
# be re-run individually once their inputs exist.
set -euo pipefail
cd "$(dirname "$0")"

echo "== 1/5: download raw claims + inflation series =="
python download_claims.py

echo "== 2/5: adjust claim dollar fields for inflation =="
python adjust_inflation.py

echo "== 3/5: triangulate claim geometries =="
python triangulate_claims.py

echo "== 4/5: build AORC bilinear-corner request index (pluvial claims only) =="
python build_aorc_pixel_day_index.py

echo "== 4/5b: fetch AORC hourly precip for requested pixel/days =="
python fetch_aorc_daily_max.py

echo "== 5/5: correct pluvial claim dates against AORC (bilinear interpolation + daily max) =="
python correct_pluvial_dates.py

# NSI structure refinement (refine_with_nsi.py) is dropped from the
# default run for now — re-add the line below once it's ready to run
# again. Until then, PLUVIAL_CORRECTED_PARQUET (data/interim/) is the
# current end-of-pipeline output, not data/processed/.
# python refine_with_nsi.py

echo "Done. Current output: ../data/interim/claims_pluvial_corrected.parquet"
echo "(NSI refinement is disabled for now; data/processed/ won't be populated until it's re-enabled.)"
