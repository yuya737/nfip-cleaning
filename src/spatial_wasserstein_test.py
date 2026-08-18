"""Spatial bias check, sliced Wasserstein distance: do claims dropped by
triangulation occupy a different region of CONUS than claims kept, measured
directly as a transport cost between the two raw point sets?

Third method in this family (spatial_bias_check.py grids and runs Moran's I;
spatial_case_control_test.py runs the Diggle & Chetwynd case-control
D-function). Exact 2D optimal transport between the two groups (46k dropped,
~1.07M kept) needs a cost matrix with tens of billions of entries -- not
tractable. The standard fix is the sliced Wasserstein distance (Rabin et al.
2011): project both point sets onto many random 1D directions, compute the
(cheap, sort-based) 1D Wasserstein/EMD distance on each projection, and
average. It's a Monte-Carlo approximation of the true 2D distance, computed
here with POT (Python Optimal Transport, the standard reference
implementation: ot.sliced_wasserstein_distance), not hand-rolled.

Advantages over the grid-based and D-function approaches: no cell size,
contiguity scheme, or radius grid to choose or defend, and it stays cheap at
this n (~14s per evaluation at 50 projections, vs ~38s for one D-function
pass) because each projection is just two sorts, not a pairwise count.
Reported in real distance units (meters) rather than the D-function's area
units, which makes it directly readable as "the average distance you'd need
to move a claim to reshape the kept distribution into the dropped one."

Uses the same claim_block_group_centroids.parquet as spatial_case_control_test.py
(FEMA's reported lat/lon is 0.1-degree rounded; run claim_centroids.py first
if that file is missing).

Significance is by the same random-labeling permutation design as the
D-function test: reshuffle case/control labels over the fixed pooled points
(preserving group sizes), recompute the statistic, and rank the observed
value against the permutation null.

Standalone diagnostic, not part of the pipeline proper -- run directly
(`python spatial_wasserstein_test.py`) and it writes
data/interim/wasserstein_result.json.

--device cuda uses POT's torch/CUDA backend (~45x faster: 0.32s vs 14.3s per
evaluation at 50 projections, benchmarked on this data) and has TWO separate
correctness issues on this data, confirmed by hand against the CPU backend
on identical permutation indices/seeds:

  1. float32 (torch's default) silently gives answers inflated 3-5x vs. the
     correct (CPU float64) value, at every projection count tested including
     50 -- this is precision loss from the large-magnitude EPSG:5070 Albers
     coordinates (up to ~3.2e6 m), not an algorithm bug. float64 on GPU
     matches CPU to within normal Monte Carlo variance (ratios 0.92-1.12x
     across 5 spot-checked permutations vs. 3.4-5.3x on float32), so this
     script always uses float64 on CUDA regardless of what the CPU path uses.
  2. At >=100 projections (float32 OR float64), POT's CUDA wasserstein_1d
     either silently returns a nonsense value (observed: ~1e155 m, no error
     raised) or crashes with a CUDA device-side assert ("scatter gather
     kernel index out of bounds") inside its own solver_1d.py. This is
     unrelated to the precision issue above and unfixed by float64 --
     confirmed unsafe at every count tried above 50.

Net effect: CUDA is only used here at exactly 50 projections and float64.
Every CUDA result is additionally checked for finiteness (assert_finite) as
a safety net, not trusted just because it ran without error -- issue #1
above proves "ran without error" is not sufficient evidence of correctness
on this backend. CPU (numpy) had neither issue at any projection count tested.
"""

import argparse
import json
import time

import numpy as np
import ot

from paths import INTERIM_DIR
from spatial_case_control_test import load_case_control

N_PROJECTIONS = 50
N_PERMUTATIONS = 199
RANDOM_SEED = 0
MAX_SAFE_CUDA_PROJECTIONS = 50  # see module docstring -- POT's CUDA wasserstein_1d is broken above this


def assert_finite(value, context):
    if not np.isfinite(value):
        raise RuntimeError(
            f"Non-finite sliced Wasserstein distance ({value}) at {context} -- "
            "this is the known POT CUDA correctness bug, not a real result. Rerun on CPU."
        )
    return value


def to_device(case_xy, control_xy, device):
    if device == "cpu":
        return case_xy, control_xy
    import torch

    # float64, not torch's default float32 -- see module docstring issue #1:
    # float32 silently inflates results 3-5x on these large EPSG:5070 coordinates.
    return (
        torch.tensor(case_xy, device=device, dtype=torch.float64),
        torch.tensor(control_xy, device=device, dtype=torch.float64),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projections", type=int, default=N_PROJECTIONS)
    parser.add_argument("--permutations", type=int, default=N_PERMUTATIONS)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    if args.device == "cuda" and args.projections > MAX_SAFE_CUDA_PROJECTIONS:
        parser.error(
            f"--projections {args.projections} > {MAX_SAFE_CUDA_PROJECTIONS} on --device cuda: "
            "POT's CUDA backend gives silently wrong results (nan/huge garbage) or crashes above this "
            "on our data -- see module docstring. Use --device cpu for more projections."
        )

    case_xy, control_xy = load_case_control()
    n_cases, n_controls = len(case_xy), len(control_xy)
    print(f"n cases (dropped): {n_cases}, n controls (kept): {n_controls}, device={args.device}")

    case_d, control_d = to_device(case_xy, control_xy, args.device)

    t0 = time.time()
    swd_obs = float(ot.sliced_wasserstein_distance(case_d, control_d, n_projections=args.projections, seed=RANDOM_SEED))
    assert_finite(swd_obs, "observed")
    print(f"observed sliced Wasserstein distance: {swd_obs:.1f} m ({time.time()-t0:.1f}s, {args.projections} projections)")

    pooled = np.vstack([case_xy, control_xy])
    n_total = n_cases + n_controls
    rng = np.random.default_rng(RANDOM_SEED)

    swd_perm = np.empty(args.permutations)
    t0 = time.time()
    for p in range(args.permutations):
        perm_case_idx = rng.choice(n_total, size=n_cases, replace=False)
        mask = np.ones(n_total, dtype=bool)
        mask[perm_case_idx] = False
        perm_case_d, perm_control_d = to_device(pooled[perm_case_idx], pooled[mask], args.device)
        swd_perm[p] = float(
            ot.sliced_wasserstein_distance(
                perm_case_d, perm_control_d, n_projections=args.projections, seed=RANDOM_SEED + p + 1
            )
        )
        assert_finite(swd_perm[p], f"permutation {p+1}")
        if (p + 1) % 20 == 0:
            elapsed = time.time() - t0
            print(f"  permutation {p+1}/{args.permutations} ({elapsed:.0f}s elapsed, ~{elapsed/(p+1)*args.permutations:.0f}s total est.)")

    p_sim = (1 + np.sum(swd_perm >= swd_obs)) / (1 + args.permutations)
    print()
    print("=== Sliced Wasserstein distance test (Rabin et al. 2011) ===")
    print(f"n_projections: {args.projections}")
    print(f"observed SWD: {swd_obs:.1f} m")
    print(f"permutation-null SWD mean/std: {swd_perm.mean():.1f} / {swd_perm.std():.1f} m")
    print(f"p_sim ({args.permutations} permutations): {p_sim:.4f}")

    out = {
        "n_cases_dropped": int(n_cases),
        "n_controls_kept": int(n_controls),
        "device": args.device,
        "n_projections": args.projections,
        "swd_observed_m": float(swd_obs),
        "swd_perm_mean_m": float(swd_perm.mean()),
        "swd_perm_std_m": float(swd_perm.std()),
        "swd_perm": swd_perm.tolist(),
        "p_sim": p_sim,
        "permutations": args.permutations,
        "seed": RANDOM_SEED,
    }
    out_path = INTERIM_DIR / "wasserstein_result.json"
    with open(out_path, "w") as f:
        json.dump(out, f)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
