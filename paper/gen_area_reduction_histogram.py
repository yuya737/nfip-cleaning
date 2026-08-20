#!/usr/bin/env python3
"""Generates paper/figures/area_reduction_histogram.png and
paper/tables/area_reduction_histogram.tex from bin counts of the
fractional area reduction between the 0.1x0.1 degree lat/lon box source
area and the final triangulated polygon_area, i.e.
pd.cut(reduction_fraction, bins=19).value_counts(), where
reduction_fraction = 1 - (polygon_area / latlon_box_area).

Bin edges and counts are hardcoded below rather than read from a CSV since
this is a one-off, pre-aggregated summary. If the underlying counts change,
update the `bins` list and rerun. The first/last bin edges from pd.cut's
raw output (-0.01 and 1.01, a small buffer beyond the true [0, 1] range)
are clipped to 0 and 1 here for display.
"""
import pathlib

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

OUT = pathlib.Path(__file__).resolve().parent / "figures"
OUT.mkdir(exist_ok=True)
TABLE_OUT = pathlib.Path(__file__).resolve().parent / "tables"
TABLE_OUT.mkdir(exist_ok=True)

BAR_COLOR = "#8c8c8c"

# (bin_low, bin_high, count), as a fraction in [0, 1], 19 approximately
# equal-width bins (~0.0526 wide). Displayed on a 0-100% axis.
bins = [
    (0, 0.0526, 1959),
    (0.0526, 0.105, 1268),
    (0.105, 0.158, 894),
    (0.158, 0.211, 1382),
    (0.211, 0.263, 1265),
    (0.263, 0.316, 2718),
    (0.316, 0.368, 2381),
    (0.368, 0.421, 2826),
    (0.421, 0.474, 4048),
    (0.474, 0.526, 4323),
    (0.526, 0.579, 5820),
    (0.579, 0.632, 6792),
    (0.632, 0.684, 9839),
    (0.684, 0.737, 11287),
    (0.737, 0.789, 14909),
    (0.789, 0.842, 21492),
    (0.842, 0.895, 39238),
    (0.895, 0.947, 92900),
    (0.947, 1.0, 923947),
]
bins.sort(key=lambda b: b[0])


def write_table():
    total = sum(c for _, _, c in bins)
    half = (len(bins) + 1) // 2
    left, right = bins[:half], bins[half:]

    def row(b):
        lo, hi, n = b
        return f"{100*lo:.1f}--{100*hi:.1f} & {n:,} & {100*n/total:.1f}\\%"

    lines = []
    lines.append(r"\begin{table}[h]")
    lines.append(r"\centering")
    lines.append(r"\begin{tabular}{rrr@{\hspace{2em}}rrr}")
    lines.append(r"\toprule")
    lines.append(
        r"Bin (\%) & $n$ claims & \% & Bin (\%) & $n$ claims & \% \\"
    )
    lines.append(r"\midrule")
    for i in range(half):
        l = row(left[i])
        r = row(right[i]) if i < len(right) else " & & "
        lines.append(f"{l} & {r} \\\\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(
        r"\caption{Bin counts of the fractional area reduction between "
        r"the 0.1$^\circ$$\times$0.1$^\circ$ lat/lon box source area and "
        f"the final triangulated \\texttt{{polygon\\_area}}, across the "
        f"released file's {total:,} claims.}}"
    )
    lines.append(r"\label{tab:area-reduction-histogram}")
    lines.append(r"\end{table}")
    out_path = TABLE_OUT / "area_reduction_histogram.tex"
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

    lows = [100 * b[0] for b in bins]
    highs = [100 * b[1] for b in bins]
    counts = [b[2] for b in bins]
    widths = [h - l for l, h in zip(lows, highs)]
    total = sum(counts)
    top_bin = bins[-1][2]
    top_bin_pct = 100 * top_bin / total

    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#e6e6e6", linewidth=0.8, zorder=0)

    ax.bar(lows, counts, width=widths, align="edge",
           edgecolor="white", linewidth=0.7, color=BAR_COLOR, zorder=3)

    ax.set_xlabel("Area reduction, lat/lon box to triangulated polygon (%)", labelpad=8)
    ax.set_ylabel("N claims", labelpad=8)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, max(counts) * 1.12)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.text(
        0.995, 1.03, f"N = {total:,} claims",
        transform=ax.transAxes, ha="right", va="bottom",
        fontsize=9.5, color="#777777",
    )
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#aaaaaa")
    ax.tick_params(length=0)

    fig.tight_layout()
    out_path = OUT / "area_reduction_histogram.png"
    fig.savefig(out_path, dpi=220, facecolor="white")
    print(f"Wrote {out_path}")
    print(f"Total binned claims: {total:,}")
    top_lo_pct = 100 * bins[-1][0]
    print(f"{top_lo_pct:.1f}-100% bin: {top_bin:,} ({top_bin_pct:.1f}% of binned claims)")
    write_table()


if __name__ == "__main__":
    main()
