#!/usr/bin/env python3
"""Generates paper/figures/polygon_area_histogram.png and
paper/tables/polygon_area_histogram.tex from triangulated polygon_area
(km^2) bin counts, i.e. pd.cut(polygon_area, bins=bins).value_counts().

Bin edges and counts are hardcoded below rather than read from a CSV since
this is a one-off, pre-aggregated summary. If the underlying counts change,
update the `bins` list and rerun.
"""
import pathlib

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

OUT = pathlib.Path(__file__).resolve().parent / "figures"
OUT.mkdir(exist_ok=True)
TABLE_OUT = pathlib.Path(__file__).resolve().parent / "tables"
TABLE_OUT.mkdir(exist_ok=True)

BAR_COLOR = "#8c8c8c"

RELEASED_TOTAL = 1_149_288

# (bin_low, bin_high, count), km^2. Bins beyond 115 km^2 are empty and are
# dropped rather than plotted as visually meaningless trailing zero bars.
bins = [
    (0, 5, 910194),
    (5, 10, 97544),
    (10, 15, 40798),
    (15, 20, 24614),
    (20, 25, 14917),
    (25, 30, 11324),
    (30, 35, 9667),
    (35, 40, 7565),
    (40, 45, 6218),
    (45, 50, 4358),
    (50, 55, 4231),
    (55, 60, 3395),
    (60, 65, 2829),
    (65, 70, 2349),
    (70, 75, 2232),
    (75, 80, 1585),
    (80, 85, 900),
    (85, 90, 1267),
    (90, 95, 714),
    (95, 100, 1069),
    (100, 105, 694),
    (105, 110, 766),
    (110, 115, 58),
]
bins.sort(key=lambda b: b[0])


def write_table():
    total = sum(c for _, _, c in bins)
    unaccounted = RELEASED_TOTAL - total
    half = (len(bins) + 1) // 2
    left, right = bins[:half], bins[half:]

    def row(b):
        lo, hi, n = b
        return f"{lo}--{hi} & {n:,} & {100*n/RELEASED_TOTAL:.1f}\\%"

    lines = []
    lines.append(r"\begin{table}[h]")
    lines.append(r"\centering")
    lines.append(r"\begin{tabular}{rrr@{\hspace{2em}}rrr}")
    lines.append(r"\toprule")
    lines.append(
        r"Bin (km$^2$) & $n$ claims & \% & Bin (km$^2$) & $n$ claims & \% \\"
    )
    lines.append(r"\midrule")
    for i in range(half):
        l = row(left[i])
        r = row(right[i]) if i < len(right) else " & & "
        lines.append(f"{l} & {r} \\\\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    if unaccounted > 0:
        outside_range_note = (
            f" {unaccounted:,} claims "
            f"({100*unaccounted/RELEASED_TOTAL:.2f}\\%) fall outside the "
            r"0--115 km$^2$ range shown."
        )
    else:
        outside_range_note = (
            r" All fall within the 0--115 km$^2$ range shown."
        )
    lines.append(
        r"\caption{Triangulated \texttt{polygon\_area} bin counts (km$^2$) "
        f"across the released file's {RELEASED_TOTAL:,} claims."
        f"{outside_range_note}}}"
    )
    lines.append(r"\label{tab:polygon-area-histogram}")
    lines.append(r"\end{table}")
    out_path = TABLE_OUT / "polygon_area_histogram.tex"
    out_path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out_path}")


def main():
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 11.5,
        "axes.edgecolor": "#999999",
        "axes.labelcolor": "#2b2b2b",
        "text.color": "#2b2b2b",
        "xtick.color": "#555555",
        "ytick.color": "#555555",
    })

    lows = [b[0] for b in bins]
    highs = [b[1] for b in bins]
    counts = [b[2] for b in bins]
    widths = [h - l for l, h in zip(lows, highs)]
    total = sum(counts)
    unaccounted = RELEASED_TOTAL - total
    below_5 = bins[0][2]
    below_5_pct = 100 * below_5 / total

    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#e6e6e6", linewidth=0.8, zorder=0)

    ax.bar(lows, counts, width=widths, align="edge",
           edgecolor="white", linewidth=0.7, color=BAR_COLOR, zorder=3)

    ax.set_xlabel(r"Triangulated polygon area (km$^2$)", labelpad=8)
    ax.set_ylabel("N claims", labelpad=8)
    ax.set_xlim(0, 115)
    ax.set_ylim(0, max(counts) * 1.12)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.text(
        0.995, 1.03, f"N = {total:,} claims (0–115 km$^2$)",
        transform=ax.transAxes, ha="right", va="bottom",
        fontsize=9.5, color="#777777",
    )
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#aaaaaa")
    ax.tick_params(length=0)

    fig.tight_layout()
    out_path = OUT / "polygon_area_histogram.png"
    fig.savefig(out_path, dpi=220, facecolor="white")
    print(f"Wrote {out_path}")
    print(f"Total binned claims: {total:,}")
    print(f"Unaccounted (outside 0-115 km^2 or NaN): {unaccounted:,} "
          f"({100*unaccounted/RELEASED_TOTAL:.3f}%)")
    print(f"0-5 km^2 bin: {below_5:,} ({below_5_pct:.1f}% of binned claims)")
    write_table()


if __name__ == "__main__":
    main()
