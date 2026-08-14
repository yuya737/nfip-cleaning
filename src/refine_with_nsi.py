"""Refine each claim's spatial uncertainty polygon using NSI structure points.

The triangulated polygon (triangulate_claims.py) is often much larger
than a single building footprint. Where the public National Structure
Inventory (NSI) has one or more structures inside that polygon, we can
shrink the uncertainty region down to just those structures' footprints —
still conservative, but far tighter than the census-geography intersection
alone.

Structures are filtered to the claim's FIRM flood zone family (exact zone
match, falling back to A-family vs V-family vs any in/out-of-SFHA match)
before being used, since a structure of the wrong flood-zone type inside
the polygon is unlikely to be the actual claimed structure. This uses only
the public 2022 NSI release (USACE), which is why buildingPropertyValue
was adjusted to 2021 dollars in adjust_inflation.py — matching NSI's
valuation vintage lets us weight candidate structures by how close their
assessed value is to the claim's reported building value.
"""

import glob
import json
from typing import Dict, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
from shapely import STRtree, points
from shapely.geometry import MultiPoint
from tqdm import tqdm

from paths import NSI_PUBLIC_ROOT, PLUVIAL_CORRECTED_PARQUET, FINAL_CLAIMS_PARQUET_TEMPLATE

CRS_ALBERS = "EPSG:5070"
CRS_WGS84 = "EPSG:4326"
BATCH_SIZE = 5000


def normalize_zone(zone: str) -> str:
    """Collapse a FIRM flood-zone code to a canonical family: AE/AH/AO/A99/AR/A/VE/V/X."""
    if not isinstance(zone, str) or zone.strip() == "":
        return "X"
    z = zone.strip().upper()
    if z == "AE":
        return "AE"
    if z in ("AH", "AHB"):
        return "AH"
    if z in ("AO", "AOB"):
        return "AO"
    if z == "A99":
        return "A99"
    if z == "AR":
        return "AR"
    if z == "VE":
        return "VE"
    if z == "V" or z.startswith("V"):
        return "V"
    if z == "A" or (z.startswith("A") and z != "AREA"):
        return "A"
    return "X"


def _build_nsi_public_parquet() -> pd.DataFrame:
    gpkg_files = glob.glob(str(NSI_PUBLIC_ROOT / "*.gpkg"))
    print(f"Found {len(gpkg_files)} NSI GeoPackage files")

    dfs = []
    for gpkg_file in tqdm(gpkg_files, desc="Loading NSI GeoPackages"):
        gdf = gpd.read_file(gpkg_file, columns=["fd_id", "firmzone", "x", "y", "val_struct"])
        dfs.append(gdf.drop(columns=["geometry"]))
    combined_df = pd.concat(dfs, ignore_index=True)

    transformer = pyproj.Transformer.from_crs(CRS_WGS84, CRS_ALBERS, always_xy=True)
    x_albers, y_albers = transformer.transform(combined_df["x"].values, combined_df["y"].values)
    combined_df["X_ALBERS"] = x_albers
    combined_df["Y_ALBERS"] = y_albers
    return combined_df


def load_nsi_public() -> Tuple[pd.DataFrame, STRtree]:
    cache_path = NSI_PUBLIC_ROOT / "nsi_public.parquet"
    if cache_path.exists():
        print(f"Loading cached NSI table from {cache_path}")
        df = pd.read_parquet(cache_path)
    else:
        df = _build_nsi_public_parquet()
        df.to_parquet(cache_path)

    print("Building NSI spatial index...")
    tree = STRtree(gpd.points_from_xy(df["X_ALBERS"], df["Y_ALBERS"]))
    return df, tree


def refine_claim_with_nsi(claim: pd.Series, nsi_df: pd.DataFrame, nsi_indices: np.ndarray) -> Dict:
    claim_polygon = claim["geometry"]
    original_area_km2 = claim_polygon.area / 1_000_000

    def _unrefined(method="original"):
        return {
            "refined_geometry": claim_polygon,
            "refinement_method": method,
            "n_structures_found": 0,
            "original_area_km2": original_area_km2,
            "refined_area_km2": original_area_km2,
            "candidate_structures_json": None,
        }

    if len(nsi_indices) == 0:
        return _unrefined()

    claim_zone = claim["normalizedZone"]
    nsi_zones = nsi_df["normalizedZone"].values[nsi_indices]

    exact_mask = nsi_zones == claim_zone
    if exact_mask.sum() > 0:
        nsi_indices = nsi_indices[exact_mask]
    else:
        if claim_zone in ("AE", "AH", "AO", "A99", "AR", "A"):
            family_mask = np.isin(nsi_zones, ["AE", "AH", "AO", "A99", "AR", "A"])
        elif claim_zone in ("VE", "V"):
            family_mask = np.isin(nsi_zones, ["VE", "V"])
        else:
            family_mask = nsi_zones == "X"
        if family_mask.sum() > 0:
            nsi_indices = nsi_indices[family_mask]
        else:
            claim_in_zone = claim["isInFloodZone"]
            nsi_in_zone = nsi_df["isInFloodZone"].values[nsi_indices]
            nsi_indices = nsi_indices[nsi_in_zone if claim_in_zone else ~nsi_in_zone]

    n_structures = len(nsi_indices)
    if n_structures == 0:
        return _unrefined()

    x = nsi_df["X_ALBERS"].values[nsi_indices]
    y = nsi_df["Y_ALBERS"].values[nsi_indices]
    structure_points = points(x, y)

    if n_structures == 1:
        refined_geom = structure_points[0].buffer(50)
        method = "single_structure_buffer"
    elif n_structures <= 3:
        refined_geom = MultiPoint(structure_points).buffer(50)
        method = "multipoint_buffer"
    else:
        refined_geom = MultiPoint(structure_points).convex_hull.buffer(25)
        method = "convex_hull"
    refined_geom = refined_geom.intersection(claim_polygon)

    candidate_json = None
    building_value = claim.get("buildingPropertyValueReal2021")
    if building_value is not None and not np.isnan(building_value):
        nsi_values = nsi_df["val_struct"].values[nsi_indices]
        distance = np.abs(nsi_values - building_value)
        inverse_distance = 1.0 / (distance + 1e-8)
        probability = inverse_distance / inverse_distance.sum()
        candidate_json = json.dumps(
            [
                {"x": xi, "y": yi, "prob": p, "val": v}
                for xi, yi, p, v in zip(x.tolist(), y.tolist(), probability.tolist(), nsi_values.tolist())
            ]
        )

    return {
        "refined_geometry": refined_geom,
        "refinement_method": method,
        "n_structures_found": n_structures,
        "original_area_km2": original_area_km2,
        "refined_area_km2": refined_geom.area / 1_000_000,
        "candidate_structures_json": candidate_json,
    }


def process_claims(claims_df: gpd.GeoDataFrame, nsi_df: pd.DataFrame, nsi_tree: STRtree) -> gpd.GeoDataFrame:
    print(f"Batch-querying spatial index for {len(claims_df):,} claims...")
    claim_idx, nsi_idx = nsi_tree.query(claims_df["geometry"].values, predicate="intersects")
    print(f"Found {len(nsi_idx):,} claim/structure intersections")

    claim_to_nsi = {}
    for c_idx, n_idx in zip(claim_idx, nsi_idx):
        claim_to_nsi.setdefault(c_idx, []).append(n_idx)

    keep_cols = [c for c in claims_df.columns if c != "geometry"]
    batches, results, errors = [], [], 0
    for i in tqdm(range(len(claims_df)), desc="Refining claims"):
        claim = claims_df.iloc[i]
        nsi_indices = np.array(claim_to_nsi.get(i, []), dtype=int)
        claim_dict = {col: claim[col] for col in keep_cols}
        try:
            refinement = refine_claim_with_nsi(claim, nsi_df, nsi_indices)
        except Exception as e:
            errors += 1
            if errors <= 10:
                print(f"\nError refining claim {i}: {e}")
            refinement = {
                "refined_geometry": claim["geometry"],
                "refinement_method": "error",
                "n_structures_found": 0,
                "original_area_km2": claim["geometry"].area / 1_000_000,
                "refined_area_km2": claim["geometry"].area / 1_000_000,
                "candidate_structures_json": None,
            }
        results.append({**claim_dict, **refinement})
        if len(results) >= BATCH_SIZE:
            batches.append(gpd.GeoDataFrame(results, geometry="refined_geometry", crs=CRS_ALBERS))
            results = []
    if results:
        batches.append(gpd.GeoDataFrame(results, geometry="refined_geometry", crs=CRS_ALBERS))
    if errors:
        print(f"\n{errors} claims errored and kept their unrefined geometry")

    return gpd.GeoDataFrame(pd.concat(batches, ignore_index=True), geometry="refined_geometry", crs=CRS_ALBERS)


def main():
    print(f"Loading claims from {PLUVIAL_CORRECTED_PARQUET}...")
    claims_gdf = gpd.read_parquet(PLUVIAL_CORRECTED_PARQUET)

    claims_gdf["normalizedZone"] = claims_gdf["floodZoneCurrent"].map(normalize_zone)
    claims_gdf["isInFloodZone"] = claims_gdf["normalizedZone"] != "X"

    nsi_df, nsi_tree = load_nsi_public()
    nsi_df["normalizedZone"] = nsi_df["firmzone"].map(normalize_zone)
    nsi_df["isInFloodZone"] = nsi_df["normalizedZone"] != "X"

    claims_gdf["year"] = pd.to_datetime(claims_gdf["dateOfLoss"]).dt.year
    for year, year_claims in claims_gdf.groupby("year"):
        print(f"\n{'-' * 80}\nYear {year}: {len(year_claims):,} claims\n{'-' * 80}")
        refined = process_claims(year_claims.copy(), nsi_df, nsi_tree)
        out_path = FINAL_CLAIMS_PARQUET_TEMPLATE.format(year=year)
        refined.to_parquet(out_path)
        print(f"Wrote {len(refined):,} rows to {out_path}")


if __name__ == "__main__":
    main()
