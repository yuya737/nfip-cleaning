#!/usr/bin/env python3
"""Generates paper/figures/day_shift_histogram.png and
paper/tables/day_shift_histogram.tex from bin counts of
(correctedDateOfLoss - dateOfLoss), in days, among corrected claims
only, swept over the same six constant precipitation thresholds as
gen_threshold_sweep.py (2.5, 5, 7.62, 10, 15, 20 mm; 7.62 mm is the
released default), i.e.
df.loc[df.pluvialCorrectionStatus == "corrected"]
  .assign(shift=lambda d: (d.correctedDateOfLoss - d.dateOfLoss).dt.days)
  .shift.value_counts(), one threshold per row.

The figure shows shift shares as a percentage of that threshold's own
corrected claims (N varies by threshold). The table additionally
expresses every cell as a percentage of the full released population
(TOTAL_CLAIMS, constant across thresholds), with the 0 column holding
the complement: claims that were NOT corrected at that threshold
(accepted_as_reported + no_qualifying_precip_in_window + no AORC
data), so each table row sums to 100%.

A shift of 0 days does not occur among corrected claims by
construction (status == "corrected" means a different day was found),
and the search window is +/-7 days, so every corrected claim's shift
falls in [-7, 7] excluding 0.

Bin counts are hardcoded below rather than read from a CSV since this
is a one-off, pre-aggregated summary. If the underlying counts change,
update the `shift_counts` dict and rerun.
"""
import pathlib
import sys

import matplotlib.pyplot as plt

OUT = pathlib.Path(__file__).resolve().parent / "figures"
OUT.mkdir(exist_ok=True)
TABLE_OUT = pathlib.Path(__file__).resolve().parent / "tables"
TABLE_OUT.mkdir(exist_ok=True)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gen_threshold_sweep import TOTAL as TOTAL_CLAIMS, status_counts, DEFAULT_THRESHOLD

BAR_COLOR = "#8c8c8c"
DAYS = [-7, -6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6, 7]
TABLE_DAYS = [-7, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7]

thresholds = [2.5, 5, 7.62, 10, 15, 20]

# {threshold: {shift_days: count}}
shift_counts = {
    2.5: {-1: 36097, -2: 18316, 1: 14733, 2: 11850, -3: 11623, -4: 10315,
          7: 9485, 6: 9041, -5: 8796, -6: 8277, -7: 8129, 5: 6815, 4: 5739, 3: 5573},
    5: {-1: 56733, -2: 21676, 1: 20220, -3: 13094, -4: 11079, 2: 11069,
        7: 9784, 6: 9335, 5: 9135, -5: 8878, -7: 8410, -6: 7471, 4: 5750, 3: 5546},
    7.62: {-1: 65918, 1: 32245, -2: 21482, -3: 13663, -4: 10697, 6: 8992,
           7: 8854, 2: 8802, -5: 8196, -7: 8361, -6: 6971, 3: 5405, 4: 5585, 5: 5317},
    10: {-1: 68090, 1: 41313, -2: 20424, -3: 14209, -4: 11825, 7: 10555,
         2: 9354, 6: 8477, -7: 8060, -5: 8027, -6: 7379, 3: 5246, 4: 5338, 5: 4645},
    15: {-1: 65527, 1: 56043, -2: 16738, -3: 14894, -4: 15294, 7: 8913,
         2: 8104, -7: 6464, -5: 6331, -6: 5891, 5: 4604, 6: 4539, 4: 4858, 3: 4166},
    20: {1: 61165, -1: 57635, -4: 16168, -2: 14428, -3: 13298, 7: 6905,
         2: 6684, -7: 5009, -5: 4902, -6: 4147, 4: 3958, 5: 3627, 6: 3284, 3: 3300},
}


def write_table():
    lines = []
    lines.append(r"\begin{table}[h]")
    lines.append(r"\centering")
    lines.append(r"\footnotesize")
    lines.append(r"\setlength{\tabcolsep}{3.5pt}")
    lines.append(r"\begin{tabular}{r" + "r" * len(TABLE_DAYS) + "}")
    lines.append(r"\toprule")
    lines.append(
        "Threshold (mm) & "
        + " & ".join(("$0$" if d == 0 else f"${d:+d}$") for d in TABLE_DAYS)
        + r" \\"
    )
    lines.append(r"\midrule")
    for t in thresholds:
        d = shift_counts[t]
        assert sum(d.values()) == status_counts[t]["corrected"]
        accepted = status_counts[t]["accepted_as_reported"]
        is_default = (t == DEFAULT_THRESHOLD)
        label = f"{t:g}" + (r"\textsuperscript{*}" if is_default else "")
        row = (r"\rowcolor{gray!15}" if is_default else "") + label
        for day in TABLE_DAYS:
            n = accepted if day == 0 else d.get(day, 0)
            row += f" & {100*n/TOTAL_CLAIMS:.1f}\\%"
        lines.append(row + r" \\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(
        r"\caption{Share of the released file's "
        f"{TOTAL_CLAIMS:,} claims by shift in "
        r"\texttt{correctedDateOfLoss} away from the originally reported "
        r"\texttt{dateOfLoss} (days, negative = earlier), swept over six "
        r"constant precipitation thresholds, with every column sharing "
        f"the same {TOTAL_CLAIMS:,}-claim denominator. "
        r"\textsuperscript{*}"
        f"{DEFAULT_THRESHOLD:g}"
        r" mm (shaded) is the released default. The $0$ column "
        r"holds \texttt{accepted\_as\_reported} claims, for which "
        r"\texttt{correctedDateOfLoss} equals \texttt{dateOfLoss} by "
        r"definition (shift $=0$); the $\pm1$ through $\pm7$ columns hold "
        r"\texttt{corrected} claims. Claims with "
        r"\texttt{no\_qualifying\_precip\_in\_window} or no AORC data are "
        r"never assigned a shift and so are outside this table's scope, "
        r"which is why rows do not sum to 100\%. "
        r"Table~\ref{tab:threshold-sweep} gives the full "
        r"\texttt{pluvialCorrectionStatus} breakdown per threshold.}"
    )
    lines.append(r"\label{tab:day-shift-histogram}")
    lines.append(r"\end{table}")
    out_path = TABLE_OUT / "day_shift_histogram.tex"
    out_path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out_path}")


def main():
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 11,
        "axes.edgecolor": "#999999",
        "axes.labelcolor": "#2b2b2b",
        "text.color": "#2b2b2b",
        "xtick.color": "#555555",
        "ytick.color": "#555555",
    })

    fig, axes = plt.subplots(len(thresholds), 1, figsize=(8, 1.2 * len(thresholds)),
                              sharex=True, sharey=True)

    global_max = max(
        100 * d[day] / sum(d.values())
        for d in shift_counts.values() for day in DAYS
    )

    for ax, t in zip(axes, thresholds):
        d = shift_counts[t]
        total = sum(d.values())
        pct = [100 * d[day] / total for day in DAYS]

        ax.set_axisbelow(True)
        ax.grid(axis="y", color="#e6e6e6", linewidth=0.8, zorder=0)
        ax.bar(DAYS, pct, width=0.75, color=BAR_COLOR,
               edgecolor="white", linewidth=0.6, zorder=3)
        ax.axvline(0, color="#cccccc", linewidth=0.8, zorder=2)

        ax.set_ylabel("% of\ncorrected", fontsize=9.5, labelpad=6)
        ax.set_xlim(-7.75, 7.75)
        ax.set_ylim(0, global_max * 1.08)
        ax.set_xticks(DAYS)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color("#aaaaaa")
        ax.tick_params(length=0)
        ax.text(
            0.995, 1.05, f"{t:g} mm threshold, N = {total:,} corrected claims",
            transform=ax.transAxes, ha="right", va="bottom",
            fontsize=9.5, color="#777777",
        )

    axes[-1].set_xlabel(
        "Shift in correctedDateOfLoss from reported dateOfLoss (days)",
        labelpad=8,
    )

    fig.tight_layout()
    out_path = OUT / "day_shift_histogram.png"
    fig.savefig(out_path, dpi=220, facecolor="white")
    print(f"Wrote {out_path}")
    write_table()


if __name__ == "__main__":
    main()
