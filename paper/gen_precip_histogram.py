#!/usr/bin/env python3
"""Generates paper/figures/precip_histogram.png from reportedDateMaxPrecipMm bin
counts (maximum hourly precipitation, mm, at each pluvial claim's triangulated
location on the reported dateOfLoss).

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
THRESHOLD = 7.62  # released default

# (bin_low, bin_high, count), sorted by bin_low. The last configured bin
# extends to 1000 mm to absorb the long tail rather than truncating it.
bins = [
    (0, 5, 274843),
    (5, 10, 170513),
    (10, 15, 150730),
    (15, 20, 107410),
    (20, 25, 79226),
    (25, 30, 58470),
    (30, 35, 50487),
    (35, 40, 36867),
    (40, 45, 28181),
    (45, 50, 21384),
    (50, 55, 17603),
    (55, 60, 16471),
    (60, 65, 17392),
    (65, 70, 17623),
    (70, 75, 15914),
    (75, 80, 17559),
    (80, 85, 13515),
    (85, 90, 7533),
    (90, 95, 5483),
    (95, 100, 4253),
    (100, 105, 2588),
    (105, 110, 2301),
    (110, 115, 1261),
    (115, 120, 596),
    (120, 125, 511),
    (125, 130, 650),
    (130, 135, 625),
    (135, 140, 791),
    (140, 145, 615),
    (145, 150, 760),
    (150, 2000, 2303),
]
bins.sort(key=lambda b: b[0])


def write_table():
    total = sum(c for _, _, c in bins)
    ncols = 3
    half = (len(bins) + 1) // 2
    left, right = bins[:half], bins[half:]

    def row(b):
        lo, hi, n = b
        rng = f"{lo}--{hi}" if hi < 1000 else f"{lo}+"
        return f"{rng} & {n:,} & {100*n/total:.1f}\\%"

    lines = []
    lines.append(r"\begin{table}[h]")
    lines.append(r"\centering")
    lines.append(r"\begin{tabular}{rrr@{\hspace{2em}}rrr}")
    lines.append(r"\toprule")
    lines.append(
        r"Bin (mm) & $n$ claims & \% & Bin (mm) & $n$ claims & \% \\"
    )
    lines.append(r"\midrule")
    for i in range(half):
        l = row(left[i])
        r = row(right[i]) if i < len(right) else " & & "
        lines.append(f"{l} & {r} \\\\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(
        r"\caption{\texttt{reportedDateMaxPrecipMm} bin counts across the "
        f"{total:,} pluvial claims with a defined "
        r"\texttt{reportedDateMaxPrecipMm}.}"
    )
    lines.append(r"\label{tab:precip-histogram}")
    lines.append(r"\end{table}")
    out_path = TABLE_OUT / "precip_histogram.tex"
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
    # The final bin (150, 1000] is far wider than the rest (5 mm), so it is
    # given the same nominal 5 mm display width as its neighbors rather than
    # its true 850 mm width, which would otherwise render as a bar far too
    # wide to fit alongside the others. Its label is set explicitly below to
    # make clear this bar is an open-ended aggregate, not a 5 mm-wide bin.
    widths = [h - l if h < 1000 else 5 for l, h in zip(lows, highs)]
    total = sum(counts)
    # Bins are quantized to 5 mm, so THRESHOLD (7.62 mm) does not fall on a
    # bin edge. This is a coarse lower-bound approximation (count through the
    # last fully-below-threshold bin edge) for a console sanity check only;
    # the manuscript's "at or below default threshold" figure is computed
    # exactly from per-claim reportedDateMaxPrecipMm, not from these bins.
    below_5 = sum(c for l, h, c in bins if h <= THRESHOLD)
    below_5_pct = 100 * below_5 / total
    peak = max(counts)

    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#e6e6e6", linewidth=0.8, zorder=0)

    ax.bar(lows, counts, width=widths, align="edge",
           edgecolor="white", linewidth=0.7, color=BAR_COLOR, zorder=3)

    ax.set_xlabel("Maximum hourly precipitation on reported dateOfLoss (mm)", labelpad=8)
    ax.set_ylabel("N claims", labelpad=8)
    ax.set_xlim(0, 155)
    ax.set_xticks([0, 20, 40, 60, 80, 100, 120, 140, 150])
    ax.set_xticklabels(["0", "20", "40", "60", "80", "100", "120", "140", "150+"])
    ax.set_ylim(0, peak * 1.14)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.text(
        0.995, 1.03, f"N = {total:,} pluvial claims",
        transform=ax.transAxes, ha="right", va="bottom",
        fontsize=9.5, color="#777777",
    )

    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#aaaaaa")
    ax.tick_params(length=0)

    fig.tight_layout()
    out_path = OUT / "precip_histogram.png"
    fig.savefig(out_path, dpi=220, facecolor="white")
    print(f"Wrote {out_path}")
    print(f"Total claims: {total:,}")
    print(f"At or below {THRESHOLD:.1f} mm bin: {below_5:,} ({below_5_pct:.1f}%)")
    write_table()


if __name__ == "__main__":
    main()
