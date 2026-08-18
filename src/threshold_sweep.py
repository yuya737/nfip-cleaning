"""Sweep the pluvial-correction precip threshold and see how sensitive the
result is to it: does the accepted/corrected/no-qualifying split change
smoothly, and does the direction of date corrections (earlier vs. later than
reported) hold steady across thresholds?

Reuses correct_pluvial_dates.compute_interpolated_daily_max as-is (the
expensive step -- bilinear interpolation of the 4 AORC corner pixels' hourly
series across every candidate day in the search window, over ~1.1M claims)
and runs it exactly once. Classification against a threshold
(accepted/corrected/no_qualifying/no_data) is cheap by comparison, so it's
redone per threshold from the same cached per-day maxes instead of re-running
the whole pipeline once per threshold -- 5 full reruns would just re-pay the
interpolation cost for no reason.

This mirrors classify_batch's logic (see correct_pluvial_dates.py) but
vectorized across thresholds rather than via DataFrame.apply, and does not
write claims_pluvial_corrected.parquet or touch any pipeline output --
standalone diagnostic, not part of the pipeline proper.

Run directly (`python threshold_sweep.py`) and it writes
data/interim/threshold_sweep_result.json.
"""

import argparse
import json

import pandas as pd

from correct_pluvial_dates import compute_interpolated_daily_max, CLAIM_BATCH_SIZE
from paths import AORC_HOURLY_PARQUET, CLAIM_PIXEL_LOOKUP_PARQUET, INTERIM_DIR

DEFAULT_THRESHOLDS = [2.5, 5, 10, 15, 20]


def classify_batch_for_thresholds(lookup_batch: pd.DataFrame, hourly: pd.DataFrame, thresholds):
    """One batch's (status, day-shift) tallies at every threshold, from a
    single compute_interpolated_daily_max call shared across all of them."""
    per_day = compute_interpolated_daily_max(lookup_batch, hourly)
    claims = lookup_batch[["id", "dateOfLoss"]].drop_duplicates("id")

    if per_day.empty:
        per_claim = claims.assign(reported_day_precip_mm=pd.NA, best_date=pd.NaT, best_precip_mm=pd.NA)
    else:
        reported = (
            per_day[per_day["day_offset"] == 0][["id", "precip_mm"]]
            .rename(columns={"precip_mm": "reported_day_precip_mm"})
        )
        best_per_claim = per_day.groupby("id")["precip_mm"].max()
        per_day = per_day.assign(is_best=per_day["precip_mm"] == per_day["id"].map(best_per_claim))
        best_rows = (
            per_day[per_day["is_best"]]
            .assign(abs_offset=lambda d: d["day_offset"].abs())
            .sort_values(["id", "abs_offset", "candidate_date"])
            .drop_duplicates("id", keep="first")[["id", "candidate_date", "precip_mm"]]
            .rename(columns={"candidate_date": "best_date", "precip_mm": "best_precip_mm"})
        )
        per_claim = claims.merge(reported, on="id", how="left").merge(best_rows, on="id", how="left")

    shift_counts = {t: {} for t in thresholds}
    status_counts = {t: {} for t in thresholds}
    for t in thresholds:
        accepted_mask = per_claim["reported_day_precip_mm"].notna() & (per_claim["reported_day_precip_mm"] >= t)
        has_best = per_claim["best_precip_mm"].notna()
        corrected_mask = (~accepted_mask) & has_best & (per_claim["best_precip_mm"] >= t)
        no_qual_mask = (~accepted_mask) & has_best & (per_claim["best_precip_mm"] < t)
        no_data_mask = (~accepted_mask) & (~has_best)

        shift_days = (
            per_claim.loc[corrected_mask, "best_date"] - per_claim.loc[corrected_mask, "dateOfLoss"]
        ).dt.days
        for k, v in shift_days.value_counts().items():
            shift_counts[t][int(k)] = shift_counts[t].get(int(k), 0) + int(v)

        for name, mask in [
            ("accepted_as_reported", accepted_mask),
            ("corrected", corrected_mask),
            ("no_qualifying_precip_in_window", no_qual_mask),
            ("no_aorc_data_in_window", no_data_mask),
        ]:
            status_counts[t][name] = status_counts[t].get(name, 0) + int(mask.sum())

    return shift_counts, status_counts


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--thresholds", type=float, nargs="+", default=DEFAULT_THRESHOLDS,
        help=f"Hourly precip thresholds (mm) to sweep. Default: {DEFAULT_THRESHOLDS}",
    )
    args = parser.parse_args()
    thresholds = args.thresholds

    print(f"Loading claim/pixel lookup from {CLAIM_PIXEL_LOOKUP_PARQUET}...")
    lookup = pd.read_parquet(CLAIM_PIXEL_LOOKUP_PARQUET)
    lookup["dateOfLoss"] = pd.to_datetime(lookup["dateOfLoss"])

    print(f"Loading AORC hourly table from {AORC_HOURLY_PARQUET}...")
    hourly = pd.read_parquet(
        AORC_HOURLY_PARQUET,
        columns=["aorc_pixel_row", "aorc_pixel_col", "date", "iana_timezone", "precip_mm"],
    )
    hourly["date"] = pd.to_datetime(hourly["date"])
    hourly = hourly.dropna(subset=["precip_mm"]).rename(columns={"date": "candidate_date"})

    claim_ids = lookup["id"].drop_duplicates().values
    n_batches = -(-len(claim_ids) // CLAIM_BATCH_SIZE)
    lookup_indexed = lookup.set_index("id", drop=False)
    print(f"Processing {len(claim_ids):,} claims in {n_batches} batch(es) across thresholds {thresholds}...")

    shift_counts = {t: {} for t in thresholds}
    status_counts = {t: {} for t in thresholds}
    for i in range(n_batches):
        batch_ids = claim_ids[i * CLAIM_BATCH_SIZE : (i + 1) * CLAIM_BATCH_SIZE]
        lookup_batch = lookup_indexed.loc[batch_ids].reset_index(drop=True)
        batch_shift, batch_status = classify_batch_for_thresholds(lookup_batch, hourly, thresholds)
        for t in thresholds:
            for k, v in batch_shift[t].items():
                shift_counts[t][k] = shift_counts[t].get(k, 0) + v
            for name, v in batch_status[t].items():
                status_counts[t][name] = status_counts[t].get(name, 0) + v
        if (i + 1) % 5 == 0 or i + 1 == n_batches:
            print(f"  batch {i + 1}/{n_batches} done")

    print()
    for t in thresholds:
        print(f"threshold={t}mm: {status_counts[t]}")

    out = {"thresholds": thresholds, "shift_counts": shift_counts, "status_counts": status_counts}
    out_path = INTERIM_DIR / "threshold_sweep_result.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
