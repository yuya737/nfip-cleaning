"""Spatial bias check, case-control D-function: do claims dropped by
triangulation cluster differently than claims kept, tested directly on the
raw claim locations rather than a gridded surface?

Companion to spatial_bias_check.py, which grids dropped/kept onto a shared
cell surface and runs (bivariate/rate-adjusted) Moran's I. This script skips
gridding entirely and runs Diggle & Chetwynd's (1991, Biometrics) case-control
D-function test: dropped claims are "cases," kept claims are "controls,"
both drawn from the same pooled set of claim locations. D(r) = Khat_cases(r)
- Khat_controls(r) is the difference of (uncorrected) Ripley's K estimates at
radius r. Significance is by random-labeling permutation: repeatedly
reshuffle which pooled points are labeled "case" vs "control" (preserving
group sizes), holding locations fixed, and recompute D(r) each time. Edge
effects need not be separately corrected for, because both K estimates in the
difference are evaluated over the identical fixed point set and window
(Diggle & Chetwynd 1991) -- this is what makes the test tractable directly on
raw points instead of requiring a grid/weights-matrix choice.

No off-the-shelf Python implementation exists (pointpats has Ripley's K/L/G/J
CSR tests and a Jacquez space-time test, but not this case-control D-function),
so it's hand-built here on scipy.spatial.cKDTree.count_neighbors, which counts
pairs within a radius via tree traversal rather than enumerating them --
required at this n (~1.15M claims): enumerating all pairs within a few tens
of km would blow up in dense metro cells.

Points are projected to EPSG:5070 (NAD83 / Conus Albers Equal Area) before
distance computation, since a fixed-degree radius is not a fixed physical
distance across CONUS's latitude range (a degree of longitude spans ~111km
at the equator vs. ~72km at 49.5N).

Uses claim_block_group_centroids.parquet (see claim_centroids.py) instead of
FEMA's reported lat/lon -- the reported coordinates are rounded to 0.1
degrees, which collapses 2.7M claims onto 25,550 unique points (up to 62,115
stacked on one point, in New Orleans) and both distorts small-r K-function
estimates and makes count_neighbors intractably slow on the resulting
extreme duplication. Block-group centroids get that down to 103,320 unique
points / max 1,919 stacked -- run claim_centroids.py first if that file is
missing.

Standalone diagnostic, not part of the pipeline proper -- run directly
(`python spatial_case_control_test.py`) and it writes
data/interim/case_control_dfunction_result.json.
"""

import argparse
import json
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree, ConvexHull

from paths import INTERIM_DIR

CONUS_ALBERS_EPSG = 5070
RADII_KM = np.array([1, 2, 5, 10, 15, 20, 25, 30])
N_PERMUTATIONS = 199
RANDOM_SEED = 0
CENTROIDS_PARQUET = INTERIM_DIR / "claim_block_group_centroids.parquet"


def load_case_control():
    centroids = pd.read_parquet(CENTROIDS_PARQUET)
    gdf = gpd.GeoDataFrame(
        centroids, geometry=gpd.points_from_xy(centroids["longitude"], centroids["latitude"]), crs="EPSG:4326"
    ).to_crs(epsg=CONUS_ALBERS_EPSG)
    xy = np.column_stack([gdf.geometry.x.to_numpy(), gdf.geometry.y.to_numpy()])
    return xy[~centroids["kept"].to_numpy()], xy[centroids["kept"].to_numpy()]


def khat(coords, radii_m, area_m2):
    n = len(coords)
    tree = cKDTree(coords)
    counts = tree.count_neighbors(tree, radii_m)  # includes n self-pairs at every radius
    return area_m2 * (counts - n) / (n * n)


def d_function(case_xy, control_xy, radii_m, area_m2):
    return khat(case_xy, radii_m, area_m2) - khat(control_xy, radii_m, area_m2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--permutations", type=int, default=N_PERMUTATIONS)
    args = parser.parse_args()

    case_xy, control_xy = load_case_control()
    n_cases, n_controls = len(case_xy), len(control_xy)
    print(f"n cases (dropped): {n_cases}, n controls (kept): {n_controls}")

    pooled = np.vstack([case_xy, control_xy])
    hull_area_m2 = ConvexHull(pooled).volume  # 2D ConvexHull: .volume is the enclosed area
    radii_m = RADII_KM * 1000.0
    print(f"pooled convex hull area: {hull_area_m2/1e6:.0f} km^2")

    case_idx = np.arange(n_cases)
    control_idx = np.arange(n_cases, n_cases + n_controls)

    t0 = time.time()
    d_obs = d_function(pooled[case_idx], pooled[control_idx], radii_m, hull_area_m2)
    print(f"observed D(r) computed in {time.time()-t0:.1f}s: {d_obs}")
    u_obs = float(np.sum(d_obs ** 2))
    print(f"observed summary statistic u = sum D(r)^2 = {u_obs:.6f}")

    rng = np.random.default_rng(RANDOM_SEED)
    n_total = n_cases + n_controls
    u_perm = np.empty(args.permutations)
    d_perm = np.empty((args.permutations, len(radii_m)))
    t0 = time.time()
    for p in range(args.permutations):
        perm_case_idx = rng.choice(n_total, size=n_cases, replace=False)
        mask = np.ones(n_total, dtype=bool)
        mask[perm_case_idx] = False
        d_p = d_function(pooled[perm_case_idx], pooled[mask], radii_m, hull_area_m2)
        d_perm[p] = d_p
        u_perm[p] = np.sum(d_p ** 2)
        if (p + 1) % 20 == 0:
            elapsed = time.time() - t0
            print(f"  permutation {p+1}/{args.permutations} ({elapsed:.0f}s elapsed, ~{elapsed/(p+1)*args.permutations:.0f}s total est.)")

    p_sim = (1 + np.sum(u_perm >= u_obs)) / (1 + args.permutations)
    print()
    print("=== Case-control D-function test (Diggle & Chetwynd 1991) ===")
    print(f"radii (km): {RADII_KM.tolist()}")
    print(f"D(r) observed: {d_obs.tolist()}")
    print(f"u = sum D(r)^2 observed: {u_obs:.6f}")
    print(f"u permutation mean/std: {u_perm.mean():.6f} / {u_perm.std():.6f}")
    print(f"p_sim ({args.permutations} permutations): {p_sim:.4f}")

    out = {
        "n_cases_dropped": int(n_cases),
        "n_controls_kept": int(n_controls),
        "hull_area_km2": hull_area_m2 / 1e6,
        "radii_km": RADII_KM.tolist(),
        "d_observed": d_obs.tolist(),
        "u_observed": u_obs,
        "u_perm_mean": float(u_perm.mean()),
        "u_perm_std": float(u_perm.std()),
        "u_perm": u_perm.tolist(),
        "d_perm": d_perm.tolist(),
        "p_sim": p_sim,
        "permutations": args.permutations,
        "seed": RANDOM_SEED,
    }
    out_path = INTERIM_DIR / "case_control_dfunction_result.json"
    with open(out_path, "w") as f:
        json.dump(out, f)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
