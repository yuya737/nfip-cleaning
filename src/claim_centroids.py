"""Precise per-claim point locations for spatial analysis, replacing FEMA's
rounded lat/lon with the centroid of each claim's matched census block-group
polygon.

FEMA's reported lat/lon is coarsened to 0.1-degree precision (see
triangulate_claims.py's docstring) -- confirmed empirically: 2,721,780 claims
collapse onto only 25,550 unique coordinate pairs, with up to 62,115 claims
stacked on a single point (New Orleans). That's fine for the grid-based
Moran's I checks in spatial_bias_check.py (cell size >= 0.1 degree already
matches the data's real resolution), but it breaks any point-pattern method
that assumes continuous, non-duplicated coordinates -- e.g. the case-control
D-function in spatial_case_control_test.py, where the mass of exact-duplicate
points both distorts small-r estimates (they mostly measure within-lattice-
point stacking, not real clustering) and blows up count_neighbors runtime.

Block-group polygons are far smaller than 0.1 degrees (typically ~1-3km
across, sub-km in cities), so using their centroid as a claim's location
gives real sub-lattice spatial resolution. Both dropped and kept claims get
the SAME kind of geometry (block-group only, not the tighter final
triangulated intersection kept claims have available) so the two groups are
compared on equal footing -- using the tighter geometry for kept claims only
would manufacture a precision asymmetry between the two groups that isn't a
real spatial signal.

Always uses the block-group "default" strategy (2010 vintage for
yearOfLoss < the configured cutover, 2020 vintage after -- see
triangulate_claims.py), matching what actually produced triangulated_claims.parquet
in this pipeline (run_pipeline.sh passes no --block-group-strategy override).
Kept claims' vintage/match-status are already recorded in
triangulated_claims.parquet, so those are looked up directly; dropped claims
aren't in that file at all (that's what makes them dropped), so their
vintage is re-derived here with the same default-strategy logic.

Standalone diagnostic, not part of the pipeline proper -- run directly
(`python claim_centroids.py`) and it writes
data/interim/claim_block_group_centroids.parquet (id, kept, latitude,
longitude -- the block-group centroid in EPSG:4326).
"""

import argparse

import geopandas as gpd
import pandas as pd

from paths import BLOCK_GROUP_DEFAULT_CUTOVER_YEAR, INTERIM_DIR, PLUVIAL_CAUSE_CODE, TRIANGULATED_PARQUET, SHAPEFILE_ROOT
from triangulate_claims import block_group_default_vintage, load_block_group_spec
from spatial_bias_check import build_dropped_kept

BLOCK_GROUP_VINTAGES_NEEDED = {
    2010: {"glob": "gz_2010_*_150_00_500k.zip"},
    2020: {"glob": "cb_2020_*_bg_500k.zip"},
}


def load_needed_vintages():
    print("Loading block-group vintages needed by the 'default' strategy (2010, 2020 only)...")
    return {year: load_block_group_spec(spec) for year, spec in BLOCK_GROUP_VINTAGES_NEEDED.items()}


def centroids_for_kept(block_group_vintages):
    kept = pd.read_parquet(
        TRIANGULATED_PARQUET, columns=["id", "censusBlockGroupFips", "block_group_vintage_used", "block_group_match_status"]
    )
    kept = kept[kept["block_group_match_status"] == "validated"].copy()
    geoms = [
        block_group_vintages[vintage].get(fips)
        for fips, vintage in zip(kept["censusBlockGroupFips"], kept["block_group_vintage_used"])
    ]
    kept["geometry"] = geoms
    return kept[kept["geometry"].notna()][["id", "geometry"]]


def centroids_for_dropped(dropped, block_group_vintages):
    vintage = dropped["yearOfLoss"].map(
        lambda y: block_group_default_vintage(y, block_group_vintages, BLOCK_GROUP_DEFAULT_CUTOVER_YEAR)
    )
    geoms = [
        block_group_vintages[v].get(fips) for fips, v in zip(dropped["censusBlockGroupFips"], vintage)
    ]
    out = dropped[["id"]].copy()
    out["geometry"] = geoms
    return out[out["geometry"].notna()][["id", "geometry"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cause-of-damage", default=PLUVIAL_CAUSE_CODE)
    args = parser.parse_args()
    cause_of_damage = args.cause_of_damage or None

    dropped, _ = build_dropped_kept(cause_of_damage)
    print(f"n dropped claims (need re-derived vintage): {len(dropped)}")

    block_group_vintages = load_needed_vintages()

    kept_geoms = centroids_for_kept(block_group_vintages)
    kept_geoms["kept"] = True
    print(f"kept claims with a validated block-group geometry: {len(kept_geoms)}")

    dropped_geoms = centroids_for_dropped(dropped, block_group_vintages)
    dropped_geoms["kept"] = False
    print(f"dropped claims with a validated block-group geometry: {len(dropped_geoms)}")

    combined = pd.concat([kept_geoms, dropped_geoms], ignore_index=True)
    gdf = gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326")
    centroid = gdf.to_crs(epsg=5070).geometry.centroid.to_crs(epsg=4326)
    gdf["longitude"] = centroid.x
    gdf["latitude"] = centroid.y

    out_path = INTERIM_DIR / "claim_block_group_centroids.parquet"
    gdf[["id", "kept", "latitude", "longitude"]].to_parquet(out_path)
    print(f"\nWrote {out_path}: {len(gdf)} rows ({gdf['kept'].sum()} kept, {(~gdf['kept']).sum()} dropped)")

    dup = gdf.groupby(["latitude", "longitude"]).size()
    print(f"unique (lat,lon) pairs: {len(dup)} of {len(gdf)} rows (max stacked at one point: {dup.max()})")


if __name__ == "__main__":
    main()
