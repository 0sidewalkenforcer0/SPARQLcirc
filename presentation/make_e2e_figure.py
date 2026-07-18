"""r9.5 per-engine END-TO-END exact PQE — the assembled counterpart to draft_r9_5_e2e_<engine>.

Combines, per engine and scale, the REAL construction time (that engine's B/R/N/C matrix, method C =
our circuit) with the REAL, engine-independent compile+WMC (pqe_stages, byte-identical circuit per E10):

    OURS end-to-end   = C construct (this engine)          + shared compile+WMC (compile once)
    NPCS end-to-end   = N construct (how-provenance SELECT) + per-answer compile+WMC (summed)

Honest by construction: construct dominates (compile+WMC is a sliver), and on selective monotone WatDiv
the NPCS end-to-end diamond sits BELOW ours (their construct is cheaper and we don't recoup it) — while
OPTIONAL is marked ✗ (non-monotone: NPCS has no end-to-end at all). One page per source engine, 2 scales.

Data: reference/paper/construction_matrix_{,<engine>_}{10m,100m}.csv + pqe_stages_flat_{10m,100m}.csv.
Output -> figures/final/result_r9_5_e2e_<engine>.{pdf,png}.
"""
import csv, os
import numpy as np
import figstyle as fs
from figstyle import SP_REIFIED, SP_CIRCUIT, SP_NPCS, GRAY, plt
from matplotlib.lines import Line2D

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER = os.path.join(HERE, "..", "reference", "paper")
OUT = os.path.join(HERE, "figures", "final")
CREATOR = "sparqlcirc/presentation/make_e2e_figure.py"
CLASS_ORDER = ["C", "F", "L", "S", "O", "M"]
# engine -> matrix filename stem ("" = GraphDB, no suffix)
ENGINES = {"GraphDB": "", "QLever": "qlever_", "Oxigraph": "oxigraph_", "MillenniumDB": "millenniumdb_"}
CAP_MS = 300_000  # the paper's 300 s construction cap


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def tkey(row):
    c = row["class"]
    return (CLASS_ORDER.index(c) if c in CLASS_ORDER else 9,
            int("".join(ch for ch in row["template"] if ch.isdigit()) or 0))


def load_matrix(stem, scale):
    p = os.path.join(PAPER, f"construction_matrix_{stem}{scale.lower()}.csv")
    if not os.path.exists(p):
        return None
    d = {}
    for r in csv.DictReader(open(p)):
        d.setdefault((r["class"], r["template"]), {})[r["method"]] = r
    return d


def load_pqe(scale):
    p = os.path.join(PAPER, f"pqe_stages_flat_{scale.lower()}.csv")
    d = {}
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            d[(r["class"], r["template"])] = r
    return d


def pqe_covered(row):
    """A template belongs in an END-TO-END PQE figure only if we actually computed its PQE: either our
    shared compile+WMC ran (shared_pqe_ms) or it was flagged non-monotone (⊖, NPCS-cannot)."""
    return bool(_f(row.get("shared_pqe_ms"))) or row.get("perans_status") == "npcs-cannot-represent-nonmonotone"


def draw(ax, mat, pqe, keys, nonmono_keys):
    x = np.arange(len(keys))
    construct, compile_wmc = [], []          # OURS stages
    ours_total, npcs_total = [], []          # end-to-end totals
    nonmono_x, toolarge_x = [], []
    for i, k in enumerate(keys):
        cell = mat.get(k, {})
        c_ok = cell.get("C", {}).get("status") == "ok"
        c_ms = _f(cell.get("C", {}).get("median_ms")) if c_ok else None
        pr = pqe.get(k, {})
        cw = _f(pr.get("shared_pqe_ms"))                     # our compile+WMC (engine-independent)
        construct.append(c_ms if c_ms else np.nan)
        compile_wmc.append(cw if (c_ms and cw) else np.nan)
        ours_total.append(c_ms + cw if (c_ms and cw) else np.nan)
        if not c_ok:
            toolarge_x.append(i)
        # non-monotone (⊖) is a STRUCTURAL property of the query — scale-independent: mark it wherever
        # the template is flagged at ANY scale, and never draw an NPCS end-to-end there (they cannot).
        if k in nonmono_keys:
            nonmono_x.append(i)
            continue
        n_ok = cell.get("N", {}).get("status") == "ok"
        n_ms = _f(cell.get("N", {}).get("median_ms")) if n_ok else None
        if n_ms and _f(pr.get("perans_pqe_ms")):
            npcs_total.append((i, n_ms + _f(pr["perans_pqe_ms"])))

    fs.grouped_bars(ax, x, [construct, compile_wmc], [SP_REIFIED, SP_CIRCUIT],
                    ["construct (engine)", "compile + WMC (client)"], width=0.30, timeout=CAP_MS)
    ax.scatter(x, ours_total, color="#222222", marker="D", s=11, zorder=5, label="ours end-to-end")
    if npcs_total:
        nx, ny = zip(*npcs_total)
        ax.scatter(nx, ny, facecolors="none", edgecolors=SP_NPCS, linewidths=0.9, marker="D",
                   s=13, zorder=5, label="NPCS end-to-end")
    if nonmono_x:
        ax.scatter(nonmono_x, [CAP_MS * 0.5] * len(nonmono_x), marker="x", s=20, color=SP_CIRCUIT,
                   zorder=6, label="NPCS ✗ (non-monotone)")
    if toolarge_x:
        ax.scatter(toolarge_x, [1.4] * len(toolarge_x), marker="v", s=16, color=GRAY, zorder=6,
                   label="construct too-large ▼")
    ax.set_yscale("log"); ax.set_ylim(1, CAP_MS * 2.2)
    ax.set_xlim(-0.7, len(x) - 0.3)
    ax.set_xticks(x, [t for _c, t in keys], rotation=90, fontsize=5.4)
    ax.set_ylabel("Runtime (ms)")


def build(engine, stem):
    scales = [s for s in ("10M", "100M") if load_matrix(stem, s)]
    if not scales:
        return
    pqe = {s: load_pqe(s) for s in scales}
    # fixed x across scales = every template for which we computed PQE at any scale (complete columns)
    keys = sorted({k for s in scales for k, row in pqe[s].items() if pqe_covered(row)},
                  key=lambda k: tkey({"class": k[0], "template": k[1]}))
    nonmono_keys = {k for s in scales for k, row in pqe[s].items()
                    if row.get("perans_status") == "npcs-cannot-represent-nonmonotone"}
    fig, axes = plt.subplots(len(scales), 1, figsize=(fs.FIG_WIDTH, 1.9 * len(scales) + 0.6),
                             sharex=True, squeeze=False)
    axes = axes[:, 0]
    for ax, scale in zip(axes, scales):
        draw(ax, load_matrix(stem, scale), pqe[scale], keys, nonmono_keys)
        ax.set_title(f"WatDiv {scale}", fontsize=7.4, pad=2.5)
    axes[-1].set_xlabel("Query template")
    for ax in axes[:-1]:
        ax.tick_params(axis="x", labelbottom=False)
    fs.suptitle(fig, f"{engine} — end-to-end exact PQE (construct + compile + WMC)")
    handles = [fs.patch(SP_REIFIED, "construct (engine)"), fs.patch(SP_CIRCUIT, "compile + WMC (client)"),
               Line2D([0], [0], color="#222222", marker="D", linestyle="none", label="ours end-to-end"),
               Line2D([0], [0], markerfacecolor="none", markeredgecolor=SP_NPCS, marker="D",
                      linestyle="none", label="NPCS end-to-end"),
               Line2D([0], [0], color=SP_CIRCUIT, marker="x", linestyle="none", label="NPCS ✗ (non-monotone)"),
               fs.timeout_handle("300 s construct cap")]
    fs.top_legend(fig, handles, [h.get_label() for h in handles], ncol=6, y=0.93)
    fs.footer(fig, "Real construct (this engine's B/R/N/C matrix) + engine-independent compile+WMC (byte-identical "
                   "circuit, E10). Construct dominates; on selective monotone WatDiv NPCS end-to-end is lower — but "
                   "OPTIONAL is ✗ (non-monotone: NPCS has no end-to-end). Grouped, not stacked, on a log axis.")
    fig.subplots_adjust(left=0.09, right=0.995, bottom=0.16 if len(scales) == 2 else 0.24,
                        top=0.80, hspace=0.22)
    fs.save(fig, f"result_r9_5_e2e_{engine.lower()}", OUT, creator=CREATOR)
    print(f"  {engine}: {scales}")


def main():
    for engine, stem in ENGINES.items():
        build(engine, stem)


if __name__ == "__main__":
    main()
