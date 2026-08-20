#!/usr/bin/env python3
"""Generates paper/figures/threshold_sweep.png and
paper/tables/threshold_sweep.tex from pluvialCorrectionStatus counts
under six constant precipitation thresholds (2.5, 5, 7.62, 10, 15, 20
mm), i.e. re-running the date-correction pipeline once per threshold and
tabulating df['pluvialCorrectionStatus'].value_counts() each time.
7.62 mm (0.30 in) is the released default.

Counts are hardcoded below rather than read from a CSV since this is a
one-off, pre-aggregated summary. If the underlying counts change, update
the `status_counts` dict and rerun.
"""
import pathlib

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

OUT = pathlib.Path(__file__).resolve().parent / "figures"
OUT.mkdir(exist_ok=True)
TABLE_OUT = pathlib.Path(__file__).resolve().parent / "tables"
TABLE_OUT.mkdir(exist_ok=True)

TOTAL = 1_149_288
# before_aorc_coverage is evaluated independently of the threshold (the
# claim's dateOfLoss predates AORC's start, so no precipitation lookup is
# ever attempted), so it is constant across every threshold and folded
# into "no AORC data" alongside no_aorc_data_in_window below.
BEFORE_AORC_COVERAGE = 12_425

thresholds = [2.5, 5, 7.62, 10, 15, 20]
DEFAULT_THRESHOLD = 7.62

# {threshold: {status: count}}, excluding the threshold-invariant
# before_aorc_coverage.
status_counts = {
    2.5: {"accepted_as_reported": 931400, "corrected": 164789,
          "no_qualifying_precip_in_window": 28470, "no_aorc_data_in_window": 12204},
    5: {"accepted_as_reported": 849615, "corrected": 198180,
        "no_qualifying_precip_in_window": 76864, "no_aorc_data_in_window": 12204},
    7.62: {"accepted_as_reported": 762284, "corrected": 210488,
           "no_qualifying_precip_in_window": 151887, "no_aorc_data_in_window": 12204},
    10: {"accepted_as_reported": 679102, "corrected": 222942,
         "no_qualifying_precip_in_window": 222615, "no_aorc_data_in_window": 12204},
    15: {"accepted_as_reported": 528372, "corrected": 222366,
         "no_qualifying_precip_in_window": 373921, "no_aorc_data_in_window": 12204},
    20: {"accepted_as_reported": 420962, "corrected": 204510,
         "no_qualifying_precip_in_window": 499187, "no_aorc_data_in_window": 12204},
}

# (status_key, latex_label, plot_label, color), stack order bottom to top.
STATUSES = [
    ("accepted_as_reported", r"accepted\_as\_reported", "accepted_as_reported", "#178C6E"),
    ("corrected", "corrected", "corrected", "#B85A1E"),
    ("no_qualifying_precip_in_window", r"no\_qualifying\_precip\_in\_window",
     "no_qualifying_precip_in_window", "#635A9E"),
    ("no_aorc_data", "no AORC data", "no AORC data", "#A83267"),
]


def combined(t):
    d = dict(status_counts[t])
    d["no_aorc_data"] = d.pop("no_aorc_data_in_window") + BEFORE_AORC_COVERAGE
    return d


def write_table():
    lines = []
    lines.append(r"\begin{table}[h]")
    lines.append(r"\centering")
    lines.append(r"\small")
    col_widths = ["2.9cm", "2.0cm", "3.6cm", "2.3cm"]
    cols = "r" + "".join(f">{{\\centering\\arraybackslash}}p{{{w}}}" for w in col_widths)
    lines.append(r"\begin{tabular}{" + cols + "}")
    lines.append(r"\toprule")
    plain_labels = ["accepted as reported", "corrected",
                     "no qualifying precip in window", "no AORC data"]
    header = "Threshold (mm)"
    for label in plain_labels:
        header += f" & {label}"
    lines.append(header + r" \\")
    lines.append(r"\midrule")
    for t in thresholds:
        d = combined(t)
        is_default = (t == DEFAULT_THRESHOLD)
        label = f"{t:g}" + (r"\textsuperscript{*}" if is_default else "")
        row = (r"\rowcolor{gray!15}" if is_default else "") + label
        for key, _, _, _ in STATUSES:
            n = d[key]
            cell = f"{100*n/TOTAL:.1f}\\%"
            row += f" & {cell}"
        lines.append(row + r" \\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(
        r"\caption{\texttt{pluvialCorrectionStatus} share of the "
        f"released file's {TOTAL:,} claims, swept over six constant "
        r"precipitation thresholds. Columns correspond to "
        r"\texttt{accepted\_as\_reported}, \texttt{corrected}, "
        r"\texttt{no\_qualifying\_precip\_in\_window}, and, combined, "
        r"\texttt{no\_aorc\_data\_in\_window} with the threshold-invariant "
        r"\texttt{before\_aorc\_coverage}. \textsuperscript{*}"
        f"{DEFAULT_THRESHOLD:g}"
        r" mm (shaded) is the released default. Table~\ref{tab:pluvial-correction-status} "
        f"gives the full $n$ claims breakdown at this default threshold.}}"
    )
    lines.append(r"\label{tab:threshold-sweep}")
    lines.append(r"\end{table}")
    out_path = TABLE_OUT / "threshold_sweep.tex"
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

    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#e6e6e6", linewidth=0.8, zorder=0)

    x = range(len(thresholds))
    bottoms = [0.0] * len(thresholds)
    for key, _, plot_label, color in STATUSES:
        pct = [100 * combined(t)[key] / TOTAL for t in thresholds]
        ax.bar(x, pct, bottom=bottoms, width=0.6, color=color,
               edgecolor="white", linewidth=0.8, zorder=3, label=plot_label)
        bottoms = [b + p for b, p in zip(bottoms, pct)]

    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{t:g}" for t in thresholds])
    ax.set_xlabel("Constant precipitation threshold (mm)", labelpad=8)
    ax.set_ylabel("Share of claims (%)", labelpad=8)
    ax.set_ylim(0, 100)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v)}"))
    ax.text(
        0.995, 1.03, f"N = {TOTAL:,} claims per threshold",
        transform=ax.transAxes, ha="right", va="bottom",
        fontsize=9.5, color="#777777",
    )
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#aaaaaa")
    ax.tick_params(length=0)
    ax.legend(
        loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2,
        frameon=False, fontsize=9.5, handlelength=1.2, handleheight=1.2,
    )

    fig.tight_layout()
    out_path = OUT / "threshold_sweep.png"
    fig.savefig(out_path, dpi=220, facecolor="white", bbox_inches="tight")
    print(f"Wrote {out_path}")
    for t in thresholds:
        d = combined(t)
        corrected_pct = 100 * d["corrected"] / TOTAL
        print(f"threshold={t}mm: corrected={d['corrected']:,} ({corrected_pct:.2f}%)")
    write_table()


if __name__ == "__main__":
    main()
