"""Defensibility table: per-year GEOID match rate against every candidate vintage.

triangulate_claims.py's "default" matching strategy picks one block-group
vintage and one ZCTA vintage per claim year (config.yaml's
block_group_vintages/zcta_vintages declare what's available;
--block-group-strategy/--zcta-strategy pick how) — see its docstring and
docs/methods.md for the reasoning. This script is the evidence behind the
default strategy specifically: for every single loss *year* (not just
decade bins), it reports what fraction of that year's real
censusBlockGroupFips / reportedZipCode values are found in each candidate
vintage's GEOID set, so the claim "using the 2010/2020 vintages already
covers nearly everything, and 1990/2000 barely move the needle" is
checkable against a table rather than taken on faith.

This is a standalone diagnostic, not part of the pipeline proper —
run it directly (`python vintage_hit_rate_table.py`) and it writes
data/interim/vintage_hit_rate_by_year_{block_group,tract,zcta}.csv.

Census tract candidate vintages aren't loaded separately: censusTract is
always the first 11 characters of censusBlockGroupFips (verified 100%
consistent across all 2.57M claims that have both fields), and a tract
GEOID is structurally just a block-group GEOID with the block-group digit
dropped, so each tract vintage is derived by truncating the matching
block-group vintage's GEOID set to 11 characters. Census's PREVGENZ tract
archive (tr90shp/tr00shp) doesn't go back any further than the block-group
one does either — both floor out at the 1990 census — so there's nothing
a separately-downloaded tract file would add.

Candidate block-group vintages (all official Census releases):
  1990  PREVGENZ bg90shp (bg{state}_d90_shp.zip), GEOID field directly.
  2000  PREVGENZ bg00shp (bg{state}_d00_shp.zip), GEOID reconstructed from
        separate STATE/COUNTY/TRACT/BLKGROUP fields (this vintage doesn't
        ship one; TRACT is inconsistently zero-padded across states, so it
        has to be zfill(6)'d before concatenating).
  2010  GENZ2010's original "gz_" release (gz_{state}_150_00_500k.zip),
        GEO_ID field prefixed with the summary-level code ("1500000US..."),
        last 12 characters are the GEOID.
  2020  Modern cb_2020_{state}_bg_500k.zip, GEOID field directly.

Candidate ZCTA vintages:
  2000  tl_2009_us_zcta500.zip, ZCTA5CE00 field -- full TIGER/Line
        resolution (median 1,198 vertices/ZCTA). Matches config.yaml's
        real "2000" vintage. (Originally PREVGENZ z500shp,
        zt{state}_d00_shp.zip -- same 2000-census ZCTA delineation but
        500k-scale generalized to a median 58 vertices/ZCTA, which cost
        ~12.6pts of spatial-validation rate for no reason tied to the
        actual ZIP boundaries; see docs/methods.md. Superseded here, not
        kept for comparison.)
  2010  cb_2019_us_zcta510_500k.zip, ZCTA5CE10 field.
  2020           cb_2020_us_zcta520_500k.zip, ZCTA5CE20 field.
"""

import argparse
import glob

import geopandas as gpd
import pandas as pd

from conus import is_conus_block_group_fips
from paths import SHAPEFILE_ROOT, INFLATION_ADJUSTED_PARQUET, INTERIM_DIR


def load_geoids(glob_pattern) -> set:
    codes = set()
    for f in glob.glob(str(SHAPEFILE_ROOT / glob_pattern)):
        columns = gpd.read_file(f, rows=0).columns
        if "GEOID" in columns:
            df = gpd.read_file(f, columns=["GEOID"], ignore_geometry=True)
            codes.update(df["GEOID"].tolist())
        elif "GEO_ID" in columns:
            df = gpd.read_file(f, columns=["GEO_ID"], ignore_geometry=True)
            codes.update(df["GEO_ID"].str[-12:].tolist())
        else:
            raise KeyError(f"{f}: no GEOID or GEO_ID column found ({list(columns)})")
    return codes


def load_block_group_2000_vintage() -> set:
    codes = set()
    for f in glob.glob(str(SHAPEFILE_ROOT / "bg*_d00_shp.zip")):
        df = gpd.read_file(f, columns=["STATE", "COUNTY", "TRACT", "BLKGROUP"], ignore_geometry=True)
        codes.update((df["STATE"] + df["COUNTY"] + df["TRACT"].str.zfill(6) + df["BLKGROUP"]).tolist())
    return codes


def load_zcta(glob_pattern, field) -> set:
    codes = set()
    for f in glob.glob(str(SHAPEFILE_ROOT / glob_pattern)):
        df = gpd.read_file(f, columns=[field], ignore_geometry=True)
        codes.update(df[field].tolist())
    return codes


def load_zcta_geometry(glob_pattern, field) -> dict:
    """Code -> polygon, for the spatial-validation check (unlike load_zcta,
    keeps geometry rather than discarding it)."""
    geoms = {}
    for f in glob.glob(str(SHAPEFILE_ROOT / glob_pattern)):
        gdf = gpd.read_file(f, columns=[field])
        geoms.update(zip(gdf[field], gdf.geometry))
    return geoms


def load_block_group_geometry(glob_pattern) -> dict:
    """Code -> polygon for a GEOID/GEO_ID-bearing block-group vintage
    (1990/2010/2020) — the geometry-preserving counterpart to load_geoids."""
    geoms = {}
    for f in glob.glob(str(SHAPEFILE_ROOT / glob_pattern)):
        columns = gpd.read_file(f, rows=0).columns
        gdf = gpd.read_file(f)
        if "GEOID" in columns:
            geoms.update(zip(gdf["GEOID"], gdf.geometry))
        elif "GEO_ID" in columns:
            geoms.update(zip(gdf["GEO_ID"].str[-12:], gdf.geometry))
        else:
            raise KeyError(f"{f}: no GEOID or GEO_ID column found ({list(columns)})")
    return geoms


def load_block_group_2000_vintage_geometry() -> dict:
    """Code -> polygon for the 2000 vintage — the geometry-preserving
    counterpart to load_block_group_2000_vintage."""
    geoms = {}
    for f in glob.glob(str(SHAPEFILE_ROOT / "bg*_d00_shp.zip")):
        gdf = gpd.read_file(f, columns=["STATE", "COUNTY", "TRACT", "BLKGROUP"])
        geoid = gdf["STATE"] + gdf["COUNTY"] + gdf["TRACT"].str.zfill(6) + gdf["BLKGROUP"]
        geoms.update(zip(geoid, gdf.geometry))
    return geoms


def derive_tract_vintages(bg_vintages: dict) -> dict:
    """Tract GEOID = first 11 characters of a block-group GEOID.

    A handful of shapefile rows have a null/non-string GEOID (e.g. the
    lone stray NaN in one 1990-vintage state file found earlier) — skip
    those rather than crash on them.
    """
    return {
        label: {code[:11] for code in codes if isinstance(code, str)}
        for label, codes in bg_vintages.items()
    }


def create_lat_lon_rect(lat: float, lon: float, buffer_degrees: float = 0.05):
    """Same 0.1x0.1 degree box as triangulate_claims.py — see its docstring
    for why (FEMA rounds reported coordinates to 1 decimal place)."""
    from shapely.geometry import box

    return box(lon - buffer_degrees, lat - buffer_degrees, lon + buffer_degrees, lat + buffer_degrees)


def spatial_validation_by_year(claims: pd.DataFrame, code_col: str, geoms_by_vintage: dict) -> pd.DataFrame:
    """For each year and vintage: of the claims whose code_col value
    *matches* that vintage's GEOID/ZCTA list, what fraction also spatially
    validate — the matched polygon overlaps the claim's lat/lon box (see
    triangulate_claims.py's select_validated_geometry). A code match alone
    doesn't guarantee the polygon is anywhere near the claim; this is the
    same spatial check the real pipeline applies, broken out by year here.
    Used for both censusBlockGroupFips and reportedZipCode — callers
    should pre-filter claims (e.g. to real 5-digit ZIPs) as needed.
    """
    df = claims[["yearOfLoss", code_col, "latitude", "longitude"]].dropna(
        subset=["yearOfLoss", code_col, "latitude", "longitude"]
    )
    boxes = gpd.GeoSeries(
        [create_lat_lon_rect(lat, lon) for lat, lon in zip(df["latitude"], df["longitude"])],
        index=df.index,
        crs="EPSG:4326",
    )

    per_vintage = {}
    for label, geom_by_code in geoms_by_vintage.items():
        matched_geom = df[code_col].map(geom_by_code)
        is_matched = matched_geom.notna()

        validated = pd.Series(False, index=df.index)
        matched_geoms = gpd.GeoSeries(matched_geom[is_matched].tolist(), crs="EPSG:4326")
        matched_boxes = gpd.GeoSeries(boxes[is_matched].tolist(), crs="EPSG:4326")
        validated[is_matched] = matched_geoms.intersects(matched_boxes).values

        year = df["yearOfLoss"]
        match_pct = (is_matched.groupby(year).mean() * 100).round(2)
        # Of the matches, what fraction also validate spatially:
        validated_of_matched_pct = (
            validated[is_matched].groupby(year[is_matched]).mean() * 100
        ).round(2)
        per_vintage[f"{label}_match_pct"] = match_pct
        per_vintage[f"{label}_validated_pct"] = validated_of_matched_pct

    table = pd.DataFrame(per_vintage)
    table.insert(0, "n_claims", df.groupby("yearOfLoss").size())
    return table


def hit_rate_by_year(claims: pd.DataFrame, code_col: str, vintages: dict) -> pd.DataFrame:
    df = claims[["yearOfLoss", code_col]].dropna()
    for label, codes in vintages.items():
        df[label] = df[code_col].isin(codes)
    df["any"] = df[list(vintages)].any(axis=1)

    table = df.groupby("yearOfLoss")[list(vintages) + ["any"]].mean().round(4) * 100
    table.insert(0, "n_claims", df.groupby("yearOfLoss").size())
    # Which single candidate vintage wins that year — the column, not "any".
    table["best_vintage"] = table[list(vintages)].idxmax(axis=1)
    return table


def actual_vs_ceiling_table(claims: pd.DataFrame, bg_vintages: dict) -> pd.DataFrame:
    """What triangulate_claims.py's "default" strategy actually achieves vs.
    the ceiling of testing every candidate vintage and taking whichever
    matches.

    "Actual" calls triangulate_claims.block_group_default_vintage directly
    (rather than re-implementing the cutover rule here) so this can't drift
    out of sync with the real pipeline. "Ceiling" is the union across every
    vintage this script loaded.
    """
    from paths import BLOCK_GROUP_DEFAULT_CUTOVER_YEAR
    from triangulate_claims import block_group_default_vintage

    # bg_vintages here is keyed by string label ("1990".."2020"); the
    # pipeline function works in int years and only checks which years are
    # present as keys, so a placeholder value is fine.
    vintage_years = {int(label): None for label in bg_vintages}

    df = claims[["yearOfLoss", "censusBlockGroupFips"]].dropna().copy()

    df["default_vintage"] = df["yearOfLoss"].apply(
        lambda y: str(block_group_default_vintage(y, vintage_years, BLOCK_GROUP_DEFAULT_CUTOVER_YEAR))
    )
    df["actual_hit"] = [
        code in bg_vintages[vintage] for code, vintage in zip(df["censusBlockGroupFips"], df["default_vintage"])
    ]

    ceiling = pd.Series(False, index=df.index)
    for codes in bg_vintages.values():
        ceiling |= df["censusBlockGroupFips"].isin(codes)
    df["ceiling_hit"] = ceiling

    table = df.groupby("yearOfLoss").agg(
        n_claims=("actual_hit", "size"),
        actual_pct=("actual_hit", lambda s: round(100 * s.mean(), 2)),
        ceiling_pct=("ceiling_hit", lambda s: round(100 * s.mean(), 2)),
    )
    table["gap_pct"] = (table["ceiling_pct"] - table["actual_pct"]).round(2)
    return table


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cause-of-damage",
        default=None,
        help='Restrict to a single causeOfDamage code, e.g. "4" for pluvial claims. '
        "Output filenames get a _causeN suffix so they don't clobber the full-population run.",
    )
    args = parser.parse_args()
    suffix = f"_cause{args.cause_of_damage}" if args.cause_of_damage else ""

    print(f"Loading claims from {INFLATION_ADJUSTED_PARQUET}...")
    claims = pd.read_parquet(
        INFLATION_ADJUSTED_PARQUET,
        columns=[
            "yearOfLoss", "censusBlockGroupFips", "censusTract", "reportedZipCode",
            "latitude", "longitude", "causeOfDamage",
        ],
    )
    claims = claims[claims["censusBlockGroupFips"].map(is_conus_block_group_fips).fillna(False)]
    claims[["latitude", "longitude"]] = claims[["latitude", "longitude"]].astype(float)
    if args.cause_of_damage is not None:
        claims = claims[claims["causeOfDamage"] == args.cause_of_damage]
        print(f"Restricted to causeOfDamage == {args.cause_of_damage!r}")
    print(f"{len(claims):,} CONUS claims")

    print("\nLoading candidate block-group vintages...")
    bg_vintages = {
        "1990": load_geoids("bg*_d90_shp.zip"),
        "2000": load_block_group_2000_vintage(),
        "2010": load_geoids("gz_2010_*_150_00_500k.zip"),
        "2020": load_geoids("cb_2020_*_bg_500k.zip"),
    }
    for label, codes in bg_vintages.items():
        print(f"  {label}: {len(codes):,} GEOIDs")

    tract_vintages = derive_tract_vintages(bg_vintages)

    print("\nLoading candidate ZCTA vintages...")
    zcta_vintages = {
        "2000": load_zcta("zt*_d00_shp.zip", "ZCTA"),
        "2010": load_zcta("cb_2019_us_zcta510_500k.zip", "ZCTA5CE10"),
        "2020": load_zcta("cb_2020_us_zcta520_500k.zip", "ZCTA5CE20"),
    }
    for label, codes in zcta_vintages.items():
        print(f"  {label}: {len(codes):,} ZCTAs")

    print("\nComputing block-group hit rate by year...")
    bg_table = hit_rate_by_year(claims, "censusBlockGroupFips", bg_vintages)
    bg_out = INTERIM_DIR / f"vintage_hit_rate_by_year_block_group{suffix}.csv"
    bg_table.to_csv(bg_out)
    pd.set_option("display.width", 160)
    print(bg_table.to_string())
    print(f"\nWrote {bg_out}")

    print("\nLoading candidate block-group vintages WITH geometry (for spatial validation)...")
    bg_geoms = {
        "1990": load_block_group_geometry("bg*_d90_shp.zip"),
        "2000": load_block_group_2000_vintage_geometry(),
        "2010": load_block_group_geometry("gz_2010_*_150_00_500k.zip"),
        "2020": load_block_group_geometry("cb_2020_*_bg_500k.zip"),
    }
    print("\nComputing block-group match rate vs. spatial-validation rate by year "
          "(of the matches, what fraction also overlap the claim's lat/lon box)...")
    bg_validation_table = spatial_validation_by_year(claims, "censusBlockGroupFips", bg_geoms)
    bg_validation_out = INTERIM_DIR / f"vintage_hit_rate_block_group_spatial_validation{suffix}.csv"
    bg_validation_table.to_csv(bg_validation_out)
    print(bg_validation_table.to_string())
    print(f"\nWrote {bg_validation_out}")

    print("\nComputing census tract hit rate by year (tract = block-group GEOID[:11])...")
    tract_table = hit_rate_by_year(claims, "censusTract", tract_vintages)
    tract_out = INTERIM_DIR / f"vintage_hit_rate_by_year_tract{suffix}.csv"
    tract_table.to_csv(tract_out)
    print(tract_table.to_string())
    print(f"\nWrote {tract_out}")

    print("\nComputing ZCTA hit rate by year (real 5-digit ZIPs only)...")
    zip_claims = claims[claims["reportedZipCode"].str.len() == 5]
    zcta_table = hit_rate_by_year(zip_claims, "reportedZipCode", zcta_vintages)
    zcta_out = INTERIM_DIR / f"vintage_hit_rate_by_year_zcta{suffix}.csv"
    zcta_table.to_csv(zcta_out)
    print(zcta_table.to_string())
    print(f"\nWrote {zcta_out}")

    print("\nLoading candidate ZCTA vintages WITH geometry (for spatial validation)...")
    zcta_geoms = {
        # config.yaml's "2000" vintage as of the PREVGENZ-to-TIGER switch --
        # full TIGER/Line resolution (median 1,198 vertices/ZCTA), not
        # PREVGENZ's 500k-scale generalization (median 58 -- that alone cost
        # ~12.6pts of spatial-validation rate for no reason tied to the
        # actual ZIP boundaries; see docs/methods.md for the before/after).
        "2000": load_zcta_geometry("tl_2009_us_zcta500.zip", "ZCTA5CE00"),
        "2010": load_zcta_geometry("cb_2019_us_zcta510_500k.zip", "ZCTA5CE10"),
        "2020": load_zcta_geometry("cb_2020_us_zcta520_500k.zip", "ZCTA5CE20"),
    }
    print("\nComputing ZCTA match rate vs. spatial-validation rate by year "
          "(of the matches, what fraction also overlap the claim's lat/lon box)...")
    zcta_validation_table = spatial_validation_by_year(zip_claims, "reportedZipCode", zcta_geoms)
    zcta_validation_out = INTERIM_DIR / f"vintage_hit_rate_zcta_spatial_validation{suffix}.csv"
    zcta_validation_table.to_csv(zcta_validation_out)
    print(zcta_validation_table.to_string())
    print(f"\nWrote {zcta_validation_out}")

    print("\nComputing actual (decade-rule, as deployed) vs. ceiling (test every vintage) block-group coverage...")
    actual_table = actual_vs_ceiling_table(claims, bg_vintages)
    actual_out = INTERIM_DIR / f"vintage_hit_rate_actual_vs_ceiling{suffix}.csv"
    actual_table.to_csv(actual_out)
    print(actual_table.to_string())
    print(
        f"\nOverall actual coverage: {actual_table['n_claims'].dot(actual_table['actual_pct']) / actual_table['n_claims'].sum():.2f}%"
    )
    print(
        f"Overall ceiling coverage: {actual_table['n_claims'].dot(actual_table['ceiling_pct']) / actual_table['n_claims'].sum():.2f}%"
    )
    print(f"Wrote {actual_out}")


if __name__ == "__main__":
    main()
