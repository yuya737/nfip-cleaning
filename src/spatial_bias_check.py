"""Spatial bias check: do claims dropped by triangulation cluster differently
than claims kept?

triangulate_claims.py drops a claim (empty_geometry) when its block-group
polygon, ZCTA polygon, and reported lat/lon box each validate individually but
don't all overlap each other. That's ~46k/1.16M pluvial claims (see
docs/methods.md). Before treating the retained 1.1M as representative, we
need evidence the drop isn't spatially concentrated in a way that would bias
downstream flood-exposure conclusions.

This script bins both groups (dropped vs. kept) onto a shared CONUS grid and
runs a bivariate Moran's I (Wartenberg's formulation, via the PySAL reference
implementation: esda.Moran_BV) with permutation-based inference. Moran's I
needs both variables measured at the same locations — that's exactly what the
shared grid gives it. Significance is via 999 label-preserving permutations
against a Queen-contiguity (edge/vertex-sharing) row-standardized spatial
weights matrix built from the actual grid-cell polygons, not the asymptotic
normal approximation.

Queen contiguity needs real cell footprints, not centroids, so each nonzero
cell is built as an explicit shapely box from its bin edges. The grid has
holes (most CONUS-bounding-box cells hold zero claims — ocean, unpopulated
land, or just empty at this resolution), so some populated cells end up with
no populated queen-neighbor and are reported as islands (zero-weight rows);
esda/libpysal handle islands natively in Moran_BV, contributing 0 to that
cell's local term.

Standalone diagnostic, not part of the pipeline proper — run directly
(`python spatial_bias_check.py`) and it writes
data/interim/moran_bv_result.json.
"""

import argparse
import json

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import box
from libpysal.weights import Queen
from esda.moran import Moran_BV, Moran_Rate

from conus import is_conus_block_group_fips
from paths import INFLATION_ADJUSTED_PARQUET, INTERIM_DIR, PLUVIAL_CAUSE_CODE, TRIANGULATED_PARQUET

LAT_MIN, LAT_MAX = 24.0, 49.5
LON_MIN, LON_MAX = -125.0, -66.5
CELL_DEG = 0.1
N_PERMUTATIONS = 999
RANDOM_SEED = 0


def build_dropped_kept(cause_of_damage):
    claims = pd.read_parquet(
        INFLATION_ADJUSTED_PARQUET,
        columns=["id", "causeOfDamage", "censusBlockGroupFips", "reportedZipCode", "latitude", "longitude", "yearOfLoss"],
    )
    if cause_of_damage is not None:
        claims = claims[claims["causeOfDamage"] == cause_of_damage]
    claims = claims.dropna(subset=["censusBlockGroupFips", "reportedZipCode"])
    is_conus = claims["censusBlockGroupFips"].map(is_conus_block_group_fips)
    claims = claims[is_conus].copy()
    claims[["latitude", "longitude"]] = claims[["latitude", "longitude"]].astype(float)

    # merge, not set()/isin() -- id is a binary/bytes column and set-membership
    # across separately-read parquet files silently gives wrong results for it.
    kept_ids = pd.read_parquet(TRIANGULATED_PARQUET, columns=["id"])
    kept_ids["kept"] = True
    merged = claims.merge(kept_ids, on="id", how="left")
    merged["kept"] = merged["kept"].fillna(False).astype(bool)  # object dtype before this bites ~ as bitwise-NOT
    return merged.loc[~merged["kept"]], merged.loc[merged["kept"]]


def bin_to_grid(dropped, kept):
    lat_bins = np.arange(LAT_MIN, LAT_MAX + CELL_DEG, CELL_DEG)
    lon_bins = np.arange(LON_MIN, LON_MAX + CELL_DEG, CELL_DEG)
    d_counts, _, _ = np.histogram2d(dropped["latitude"], dropped["longitude"], bins=[lat_bins, lon_bins])
    k_counts, _, _ = np.histogram2d(kept["latitude"], kept["longitude"], bins=[lat_bins, lon_bins])

    nonzero = (d_counts + k_counts) > 0
    rows, cols = np.nonzero(nonzero)
    # exact bin edges per populated cell, not centroids -- Queen contiguity is
    # defined on shared polygon edges/vertices, not point proximity.
    polygons = [
        box(lon_bins[j], lat_bins[i], lon_bins[j + 1], lat_bins[i + 1])
        for i, j in zip(rows, cols)
    ]
    gdf = gpd.GeoDataFrame(geometry=polygons, crs="EPSG:4326")
    return gdf, d_counts[nonzero], k_counts[nonzero], d_counts, k_counts, lat_bins, lon_bins


def full_grid_gdf(d_counts, k_counts, lat_bins, lon_bins):
    """Every grid cell, populated or not (unlike bin_to_grid's Moran/Queen
    input, which only carries the ~19k populated cells) -- for plotting, so
    the map covers the full CONUS extent and empty cells read as a real 0
    instead of being omitted entirely. difference_pct is then exactly 0
    (not NaN/missing) wherever both groups are empty, which is what centers
    a diverging colorbar at 0 instead of it being set by whatever nonzero
    cells happened to survive the populated-only filter.
    """
    n_rows, n_cols = d_counts.shape
    lon_grid, lat_grid = np.meshgrid(lon_bins[:-1], lat_bins[:-1])
    polygons = shapely.box(lon_grid.ravel(), lat_grid.ravel(), lon_grid.ravel() + CELL_DEG, lat_grid.ravel() + CELL_DEG)
    gdf = gpd.GeoDataFrame(geometry=polygons, crs="EPSG:4326")
    gdf["dropped_pct"] = (d_counts / d_counts.sum() * 100).ravel()
    gdf["kept_pct"] = (k_counts / k_counts.sum() * 100).ravel()
    gdf["difference_pct"] = gdf["dropped_pct"] - gdf["kept_pct"]
    return gdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cause-of-damage",
        default=PLUVIAL_CAUSE_CODE,
        help=f"Restrict to a single causeOfDamage code before splitting dropped/kept (default: {PLUVIAL_CAUSE_CODE}, pluvial). Pass empty string for no filter.",
    )
    args = parser.parse_args()
    cause_of_damage = args.cause_of_damage or None

    dropped, kept = build_dropped_kept(cause_of_damage)
    breakpoint()
    gdf, dropped_counts, kept_counts, d_counts_full, k_counts_full, lat_bins, lon_bins = bin_to_grid(dropped, kept)
    dropped_pct = dropped_counts / dropped_counts.sum() * 100
    kept_pct = kept_counts / kept_counts.sum() * 100

    w = Queen.from_dataframe(gdf, use_index=False)
    n_islands = len(w.islands)
    w.transform = "r"

    np.random.seed(RANDOM_SEED)
    result = Moran_BV(dropped_pct, kept_pct, w, permutations=N_PERMUTATIONS)

    # Moran_BV compares two densities each normalized to their own total, but
    # dropped and kept are a ~4%/96% split of the SAME underlying claims pool
    # (46,424 vs 1,109,049) -- both densities track overall local claim volume,
    # so a positive Moran_BV is close to guaranteed regardless of whether
    # dropping is spatially biased, and dropped_pct is measured on ~24x fewer
    # points per cell than kept_pct, so it's far noisier. Moran_Rate (Assuncao
    # & Reis 1999, esda.Moran_Rate) is built for exactly this: an event count
    # (dropped) over a population-at-risk (dropped+kept) with wildly uneven
    # denominators per areal unit, with an empirical-Bayes adjustment for the
    # excess variance small-count cells would otherwise contribute. This asks
    # the more direct question -- is the LOCAL DROP RATE spatially clustered --
    # without the shared-density confound baked into Moran_BV above.
    np.random.seed(RANDOM_SEED)
    rate_result = Moran_Rate(dropped_counts, dropped_counts + kept_counts, w, permutations=N_PERMUTATIONS)

    # plot the percentages on the grid -- full grid, not just the populated
    # cells bin_to_grid returns for Moran/Queen, so empty cells plot as a
    # real 0 instead of being omitted, and the diff colorbar centers at 0.
    plot_gdf = full_grid_gdf(d_counts_full, k_counts_full, lat_bins, lon_bins)
    diff_max_abs = plot_gdf["difference_pct"].abs().max()

    from matplotlib import pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(20, 4))
    plot_gdf.plot(column="dropped_pct", ax=axes[0], legend=True,
                legend_kwds={"label": "Dropped claims (%)"})
    plot_gdf.plot(column="kept_pct", ax=axes[1], legend=True,
                legend_kwds={"label": "Kept claims (%)"})
    plot_gdf.plot(column="difference_pct", ax=axes[2], legend=True,
                legend_kwds={"label": "diff (%)"}, cmap="RdBu_r", vmin=-diff_max_abs, vmax=diff_max_abs)
    axes[0].set_title("Dropped claims density")
    axes[1].set_title("Kept claims density")
    axes[2].set_title("diff density")
    plt.tight_layout()
    plt.savefig(INTERIM_DIR / "dropped_kept_density.png")

    print(f"n dropped claims: {len(dropped)}")
    print(f"n kept claims:    {len(kept)}")
    print(f"n grid cells (nonzero, {CELL_DEG}deg): {len(gdf)}")
    print(f"Queen contiguity weights, row-standardized ({n_islands} islands / zero-neighbor cells)")
    print()
    print("=== Bivariate Moran's I: dropped-density vs kept-density ===")
    print(f"I      = {result.I:.4f}")
    print(f"E[I]   = {result.EI_sim:.4f}  (under {N_PERMUTATIONS} permutations)")
    print(f"z_sim  = {result.z_sim:.3f}")
    print(f"p_sim  = {result.p_sim:.4f}")
    print()
    print("=== Rate-adjusted Moran's I (Assuncao & Reis 1999): local drop rate ===")
    print(f"I      = {rate_result.I:.4f}")
    print(f"E[I]   = {rate_result.EI_sim:.4f}  (under {N_PERMUTATIONS} permutations)")
    print(f"z_sim  = {rate_result.z_sim:.3f}")
    print(f"p_sim  = {rate_result.p_sim:.4f}")

    out = {
        "cause_of_damage": cause_of_damage,
        "n_dropped": int(len(dropped)),
        "n_kept": int(len(kept)),
        "grid": {"lat_min": LAT_MIN, "lat_max": LAT_MAX, "lon_min": LON_MIN, "lon_max": LON_MAX, "cell_deg": CELL_DEG},
        "weights": {"method": "Queen", "transform": "row-standardized", "n_islands": n_islands},
        "moran_bv": {
            "I": float(result.I),
            "EI_sim": float(result.EI_sim),
            "z_sim": float(result.z_sim),
            "p_sim": float(result.p_sim),
            "permutations": N_PERMUTATIONS,
            "seed": RANDOM_SEED,
        },
        "moran_rate": {
            "I": float(rate_result.I),
            "EI_sim": float(rate_result.EI_sim),
            "z_sim": float(rate_result.z_sim),
            "p_sim": float(rate_result.p_sim),
            "permutations": N_PERMUTATIONS,
            "seed": RANDOM_SEED,
        },
    }
    out_path = INTERIM_DIR / "moran_bv_result.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
