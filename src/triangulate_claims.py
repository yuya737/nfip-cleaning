"""Triangulate a spatial uncertainty polygon for each claim.

Each NFIP claim record ships with three independent, imprecise location
signals: a census block group FIPS code, a reported ZIP code, and a
lat/lon pair (itself deliberately coarsened by FEMA to 1 decimal place
for privacy). None of these alone pins down a claim's location; each
implies a region the claim must fall within. We intersect whichever of
the three are available for a given claim into a single polygon
("triangulation"), which is a strict upper bound on the claim's true
location and is usually far smaller than any single source's region alone.

Which shapefile for which decade?
------------------------------------
`config.yaml`'s `block_group_shapefiles` / `zcta_shapefiles` map each
claim *decade* (`yearOfLoss // 10 * 10`) explicitly to a shapefile. A
decade with no entry means that source is skipped entirely for claims in
it — this is why `zcta_shapefiles` has nothing before 2000: ZCTAs did not
exist as a Census product yet, so there's nothing to check a pre-2000 ZIP
against.

Several decades in `block_group_shapefiles` intentionally point at the
same file: checking real `censusBlockGroupFips` values against the 2010
block-group release shows a 92-100% match rate for claims from *every*
decade back to the 1970s regardless of loss year — including 1978, a
decade before block groups existed as a nationwide census geography at
all. FEMA's geocoding is retroactively assigning a roughly-current
vintage to every record, not the historical loss-year boundary, so
there's little benefit to a different block-group file per decade before
2020 (where the match rate does drop, since the 2020 census redrew a
meaningful share of block groups). ZCTAs are less lopsided — vintage
choice barely moves the match rate either way — so each decade from 2000
on just uses its own contemporaneous ZCTA release out of methodological
cleanliness, not because it recovers meaningfully more claims. See
docs/methods.md for the numbers behind both of these.

Validating matches against the lat/lon box
---------------------------------------------
FEMA's lat/lon fields are rounded to 1 decimal place, which means the
claim's true location is guaranteed to fall within a 0.1 x 0.1 degree box
centered on the reported point (create_lat_lon_rect). That's a genuine
spatial constraint, not just another imprecise source, so we use it to
validate block-group/ZIP matches rather than trusting a GEOID string
match blindly: a code that matches the shapefile's GEOID list but whose
polygon does *not* overlap the lat/lon box is treated as spatially
inconsistent and excluded from that claim's intersection, rather than
combined in to produce an empty or misleading polygon.
"""

import argparse
import glob
from typing import Dict, Optional, Tuple

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from tqdm import tqdm

from conus import is_conus_block_group_fips
from paths import (
    SHAPEFILE_ROOT,
    BLOCK_GROUP_SHAPEFILES_BY_DECADE,
    ZCTA_SHAPEFILES_BY_DECADE,
    INFLATION_ADJUSTED_PARQUET,
    TRIANGULATED_PARQUET,
)

GeometryByCode = Dict[str, BaseGeometry]
GeometryByDecade = Dict[int, GeometryByCode]


def create_lat_lon_rect(lat: float, lon: float, buffer_degrees: float = 0.05):
    """Rectangular box around a lat/lon point, in WGS84 (EPSG:4326).

    buffer_degrees=0.05 gives a 0.1-degree box, matching FEMA's stated
    1-decimal-place rounding of the reported coordinates (assuming
    round-to-nearest; FEMA's data dictionary doesn't specify truncation
    vs. rounding, and we assume the former as the more standard practice).
    """
    return box(
        lon - buffer_degrees,
        lat - buffer_degrees,
        lon + buffer_degrees,
        lat + buffer_degrees,
    )


def decade_for_year(year) -> Optional[int]:
    if pd.isna(year):
        return None
    return int(year) // 10 * 10


def _spec_files(spec):
    if "glob" in spec:
        return glob.glob(str(SHAPEFILE_ROOT / spec["glob"]))
    return [str(SHAPEFILE_ROOT / spec["file"])]


def load_block_group_spec(spec) -> GeometryByCode:
    files = _spec_files(spec)
    if not files:
        raise FileNotFoundError(f"No block-group files matched {spec}")
    geoms = {}
    for f in tqdm(files, desc=f"  block groups ({spec.get('glob') or spec.get('file')})"):
        gdf = gpd.read_file(f)
        if "GEOID" in gdf.columns:
            geoids = gdf["GEOID"]
        else:
            # GENZ2010's old "gz_" naming ships GEO_ID like
            # "1500000US060014057002" (summary-level prefix + 12-digit GEOID).
            geoids = gdf["GEO_ID"].str[-12:]
        geoms.update(zip(geoids, gdf["geometry"]))
    return geoms


def load_zcta_spec(spec) -> GeometryByCode:
    files = _spec_files(spec)
    if not files:
        raise FileNotFoundError(f"No ZCTA files matched {spec}")
    geoms = {}
    for f in files:
        gdf = gpd.read_file(f, columns=[spec["field"]])
        geoms.update(zip(gdf[spec["field"]], gdf["geometry"]))
    return geoms


def load_by_decade(shapefiles_by_decade: dict, load_one_spec) -> GeometryByDecade:
    """Load each distinct shapefile spec once, even if multiple decades share it."""
    cache = {}
    result = {}
    for decade, spec in shapefiles_by_decade.items():
        key = tuple(sorted(spec.items()))
        if key not in cache:
            print(f"  loading for decade {decade}s: {spec}")
            cache[key] = load_one_spec(spec)
            print(f"    {len(cache[key]):,} geometries")
        result[decade] = cache[key]
    return result


def select_validated_geometry(
    code, geometry_by_code: GeometryByCode, latlon_box
) -> Tuple[Optional[BaseGeometry], str]:
    """Look up a code, spatially validated against the claim's lat/lon box.

    Returns (geometry, status), where status is one of:
      not_found                code isn't in the decade's shapefile
      validated                the polygon overlaps latlon_box
      unvalidated_no_latlon    a match exists but there's no lat/lon to check it against
      spatially_inconsistent   the polygon doesn't overlap latlon_box
    """
    geom = geometry_by_code.get(code)
    if geom is None:
        return None, "not_found"
    if latlon_box is None:
        return geom, "unvalidated_no_latlon"
    if geom.intersects(latlon_box):
        return geom, "validated"
    return None, "spatially_inconsistent"


def triangulate_geometry(
    row, block_groups_by_decade: GeometryByDecade, zctas_by_decade: GeometryByDecade
):
    latlon_box = None
    if pd.notna(row["latitude"]) and pd.notna(row["longitude"]):
        latlon_box = create_lat_lon_rect(row["latitude"], row["longitude"])

    decade = decade_for_year(row.get("yearOfLoss"))
    geometries, sources = [], []

    if decade in block_groups_by_decade:
        bg_geom, bg_status = select_validated_geometry(
            row["censusBlockGroupFips"], block_groups_by_decade[decade], latlon_box
        )
    else:
        bg_geom, bg_status = None, "no_shapefile_for_decade"
    if bg_geom is not None:
        geometries.append(bg_geom)
        sources.append("block_group")

    if decade in zctas_by_decade:
        zip_geom, zip_status = select_validated_geometry(
            row["reportedZipCode"], zctas_by_decade[decade], latlon_box
        )
    else:
        zip_geom, zip_status = None, "no_shapefile_for_decade"
    if zip_geom is not None:
        geometries.append(zip_geom)
        sources.append("zip")

    if latlon_box is not None:
        geometries.append(latlon_box)
        sources.append("latlon")

    if len(geometries) == 0:
        geometry, is_empty = None, None
    else:
        geometry = shapely.intersection_all(geometries)
        is_empty = geometry.is_empty

    return pd.Series(
        {
            "geometry": geometry,
            "n_geometry_sources": len(geometries),
            "geometry_sources": "+".join(sources),
            "geometry_is_empty": is_empty,
            "decade_used": decade,
            "block_group_match_status": bg_status,
            "zip_match_status": zip_status,
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(INFLATION_ADJUSTED_PARQUET))
    parser.add_argument("--output", default=str(TRIANGULATED_PARQUET))
    args = parser.parse_args()

    print(f"Loading claims from {args.input}...")
    claims_df = pd.read_parquet(args.input)
    print(f"Loaded {len(claims_df):,} claims")

    claims_df = claims_df.dropna(subset=["censusBlockGroupFips", "reportedZipCode"])
    print(f"Filtered to {len(claims_df):,} claims with a block-group FIPS and ZIP code")

    is_conus = claims_df["censusBlockGroupFips"].map(is_conus_block_group_fips)
    print(
        f"Excluding {(~is_conus).sum():,} non-CONUS claims (Alaska, Hawaii, Puerto Rico, "
        f"other territories — see conus.py); keeping {is_conus.sum():,}"
    )
    claims_df = claims_df[is_conus]

    print("\nLoading block-group shapefiles by decade...")
    block_groups_by_decade = load_by_decade(BLOCK_GROUP_SHAPEFILES_BY_DECADE, load_block_group_spec)
    print("Loading ZCTA shapefiles by decade...")
    zctas_by_decade = load_by_decade(ZCTA_SHAPEFILES_BY_DECADE, load_zcta_spec)

    claims_df[["latitude", "longitude"]] = claims_df[["latitude", "longitude"]].astype(float)

    print("\nTriangulating claim geometries...")
    tqdm.pandas()
    tri_result = claims_df.progress_apply(
        triangulate_geometry,
        args=(block_groups_by_decade, zctas_by_decade),
        axis=1,
    )
    claims_df[tri_result.columns] = tri_result

    successful = claims_df["geometry"].notna().sum()
    print(
        f"\nTriangulated {successful:,} / {len(claims_df):,} claims "
        f"({100 * successful / len(claims_df):.1f}%)"
    )
    print("\nSource-combination breakdown:")
    for combo, count in claims_df["geometry_sources"].value_counts().items():
        label = combo if combo else "(none)"
        print(f"  {label}: {count:,} ({100 * count / len(claims_df):.1f}%)")

    n_empty = claims_df["geometry_is_empty"].fillna(False).sum()
    if n_empty:
        print(
            f"\n{n_empty:,} claims had a non-empty source list but an EMPTY final "
            "intersection (sources validated individually against lat/lon but don't "
            "overlap each other) — see docs/methods.md"
        )
    print("\nBlock-group match status breakdown:")
    print(claims_df["block_group_match_status"].value_counts())
    print("\nZIP match status breakdown:")
    print(claims_df["zip_match_status"].value_counts())

    has_geometry = claims_df["geometry"].notna() & ~claims_df["geometry_is_empty"].fillna(True)
    claims_gdf = (
        gpd.GeoDataFrame(claims_df[has_geometry], geometry="geometry")
        .set_crs("EPSG:4326")
        .to_crs("EPSG:5070")
    )

    print(f"\nMean claim area:   {claims_gdf.geometry.area.mean() / 1e6:.3f} km^2")
    print(f"Median claim area: {claims_gdf.geometry.area.median() / 1e6:.3f} km^2")

    claims_gdf.to_parquet(args.output)
    print(f"\nWrote {len(claims_gdf):,} rows to {args.output}")


if __name__ == "__main__":
    main()
