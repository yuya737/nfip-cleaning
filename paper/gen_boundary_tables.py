#!/usr/bin/env python3
"""Regenerates paper/tables/*.tex from data/interim/vintage_hit_rate_*.csv.

Run this after the underlying CSVs change, then rebuild the manuscript.
"""
import csv
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
INTERIM = ROOT.parent / "data" / "interim"
OUT = ROOT / "tables"
OUT.mkdir(exist_ok=True)


def load(fname):
    with open(INTERIM / fname) as f:
        return list(csv.DictReader(f))


def bg_default_selected(year, cutover=2020):
    """Release the `default` strategy picks for block group at this loss year."""
    return "2010" if year < cutover else "2020"


def zcta_default_selected(year):
    """Release the `default` strategy picks for ZCTA at this loss year,
    equivalent to `closest` over the configured releases {2000, 2010,
    2020}, ties broken toward the newer release."""
    if year < 2005:
        return "2000"
    elif year < 2015:
        return "2010"
    else:
        return "2020"


def emit(rows, vintages, label, caption, out_name, selected_fn):
    ncols = 2 + 2 * len(vintages)
    colspec = "rr" + "rr" * len(vintages)

    head1_cells = ["", ""] + [r"\multicolumn{2}{c}{%s}" % v for v in vintages]
    head1 = " & ".join(head1_cells) + r" \\"
    cmid = " ".join(
        r"\cmidrule(lr){%d-%d}" % (3 + 2 * i, 4 + 2 * i) for i in range(len(vintages))
    )
    head2_cells = ["Loss year", "N claims"]
    for _ in vintages:
        head2_cells += ["Match", "Valid"]
    head2 = " & ".join(head2_cells) + r" \\"

    lines = []
    lines.append(r"{\footnotesize\setlength{\tabcolsep}{4pt}")
    lines.append(r"\begin{longtable}{%s}" % colspec)
    lines.append(r"\toprule")
    lines.append(head1)
    lines.append(cmid)
    lines.append(head2)
    lines.append(r"\midrule")
    lines.append(r"\endfirsthead")
    lines.append(r"\multicolumn{%d}{l}{\itshape (continued)}\\" % ncols)
    lines.append(r"\toprule")
    lines.append(head1)
    lines.append(cmid)
    lines.append(head2)
    lines.append(r"\midrule")
    lines.append(r"\endhead")
    lines.append(r"\midrule")
    lines.append(r"\multicolumn{%d}{r}{\itshape continued on next page}\\" % ncols)
    lines.append(r"\endfoot")
    lines.append(r"\endlastfoot")
    for r in rows:
        year = int(r["yearOfLoss"])
        match_vals = [float(r[f"{v}_match_pct"]) for v in vintages]
        valid_vals = [float(r[f"{v}_validated_pct"]) for v in vintages]
        best_match = max(match_vals)
        best_valid = max(valid_vals)
        selected = selected_fn(year)
        cells = [r["yearOfLoss"], f'{int(r["n_claims"]):,}']
        for v, m, val in zip(vintages, match_vals, valid_vals):
            m_str = f"{m:.1f}"
            val_str = f"{val:.1f}"
            if m == best_match:
                m_str = r"\textbf{%s}" % m_str
            if val == best_valid:
                val_str = r"\textbf{%s}" % val_str
            if v == selected:
                m_str = r"\cellcolor{gray!15}%s" % m_str
                val_str = r"\cellcolor{gray!15}%s" % val_str
            cells.append(m_str)
            cells.append(val_str)
        lines.append(" & ".join(cells) + r" \\")
    lines.append(r"\bottomrule")
    lines.append(r"\caption{%s}" % caption)
    lines.append(r"\label{%s}\\" % label)
    lines.append(r"\end{longtable}")
    lines.append(r"}")
    (OUT / out_name).write_text("\n".join(lines) + "\n")


def main():
    bg = load("vintage_hit_rate_block_group_spatial_validation_cause4.csv")
    zc = load("vintage_hit_rate_zcta_spatial_validation_cause4.csv")
    caption_note = (
        r"Match is the raw GEOID match rate against that release's shapefile. "
        r"Valid is the share of matched claims whose polygon also overlaps "
        r"the claim's rounded lat/lon box. For each loss year, the highest "
        r"Match value and the highest Valid value across releases are shown "
        r"in bold (ties bolded in full). Shaded cells mark the release the "
        r"\texttt{default} strategy selects for that loss year."
    )
    emit(
        bg,
        ["1990", "2000", "2010", "2020"],
        "tab:release-match-rate-bg",
        r"Block-group GEOID match rate and spatial-validation rate (\%) by "
        r"loss year and shapefile release, for pluvial (\texttt{causeOfDamage "
        r'== "4"}) claims. ' + caption_note,
        "boundary_release_block_group.tex",
        bg_default_selected,
    )
    emit(
        zc,
        ["2000", "2010", "2020"],
        "tab:release-match-rate-zcta",
        r"ZCTA GEOID match rate and spatial-validation rate (\%) by loss "
        r"year and shapefile release, for pluvial (\texttt{causeOfDamage == "
        r'"4"}) claims. ' + caption_note,
        "boundary_release_zcta.tex",
        zcta_default_selected,
    )


if __name__ == "__main__":
    main()
