"""Live R9.2 progress monitor. Prints a text dashboard by default; `--html PATH` also renders a
self-contained progress page (published as an Artifact).

Answers "is it running or stalled, and how far?" from three signals:
  1. is a paper_construction_matrix.py process alive (and on which engine/scale)?
  2. how many (engine,scale,class,method) cells are DONE in construction_brnc.csv vs expected (from the
     frozen manifest), broken down by status (ok / timeout / unsupported / not-run / err);
  3. how long since the last result row was written (CSV mtime) -> recent => healthy, old => on a slow cell.

  python3 progress.py                 # text dashboard
  python3 progress.py --html out.html # + HTML dashboard for the Artifact
"""
import os, sys, csv, time, glob, subprocess, argparse, html, json

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "construction_brnc.csv")
MANIFEST = os.path.join(HERE, "workload_manifest.csv")
METHODS = ["B", "R", "N", "C"]
CLASS_ORDER = ["L", "S", "F", "C", "O", "M"]

def procs():
    r = subprocess.run(["pgrep", "-af", "paper_construction_matrix.py"], capture_output=True, text=True)
    out = []
    for ln in r.stdout.splitlines():
        if "pgrep" in ln or "progress.py" in ln:
            continue
        if "paper_construction_matrix.py" not in ln or "/bin/bash" in ln:
            continue                                          # keep only the python worker, not the bash wrapper
        parts = ln.split(None, 1)
        # trim to the informative tail (drop the interpreter path)
        cmd = parts[1] if len(parts) > 1 else ln
        i = cmd.find("paper_construction_matrix.py")
        out.append(cmd[i:] if i >= 0 else cmd)
    return sorted(set(out))                                   # dedup parent + per-cell killable worker child

def manifest_cells():
    """expected (scale, class, template) instances from the frozen manifest."""
    cells = {}
    with open(MANIFEST) as fh:
        for row in csv.DictReader(fh):
            cells.setdefault(row["scale"], {}).setdefault(row["class"], set()).add(row["template"])
    return cells

def read_results():
    if not os.path.exists(CSV):
        return [], None
    with open(CSV) as fh:
        rows = list(csv.DictReader(fh))
    return rows, os.path.getmtime(CSV)

def snapshot():
    running = procs()
    man = manifest_cells()
    rows, mtime = read_results()
    # index results by (engine,scale,class,template,method) -> status
    done = {}
    for r in rows:
        done[(r["engine"], r["scale"], r["class"], r["template"], r["method"])] = r["status"]
    scales = sorted(man)
    engines = sorted({r["engine"] for r in rows}) or ["graphdb"]
    per = {}                                                   # (engine,scale) -> {status: count}, plus totals
    grid = {}                                                  # (engine,scale,class,method) -> status char
    for eng in engines:
        for scale in scales:
            tot = sum(len(man[scale][c]) for c in man[scale]) * len(METHODS)
            counts = {}
            for cls in man[scale]:
                for tmpl in man[scale][cls]:
                    for m in METHODS:
                        st = done.get((eng, scale, cls, tmpl, m))
                        key = st or "pending"
                        counts[key] = counts.get(key, 0) + 1
                        grid[(eng, scale, cls, m)] = grid.get((eng, scale, cls, m), "")
            per[(eng, scale)] = dict(total=tot, counts=counts)
    return dict(running=running, mtime=mtime, per=per, rows=len(rows), engines=engines,
                scales=scales, man=man, done=done, now=time.time())

def ago(mtime, now):
    if not mtime: return "n/a"
    s = int(now - mtime)
    return f"{s}s ago" if s < 90 else f"{s//60}m{s%60}s ago"

def text(s):
    print("=" * 64)
    if s["running"]:
        print(f"STATUS: ● RUNNING  ({len(s['running'])} process)")
        for p in s["running"]:
            print(f"   {p[:90]}")
    else:
        print("STATUS: ○ IDLE  (no R9.2 harness process alive)")
    print(f"last result row written: {ago(s['mtime'], s['now'])}   |   total rows: {s['rows']}")
    if s["running"] and s["mtime"] and s["now"] - s["mtime"] > 300:
        print("   (>5min since last row -> likely on a slow unbound C/M cell, not stalled)")
    print("-" * 64)
    for (eng, scale), d in sorted(s["per"].items()):
        c = d["counts"]; okc = c.get("ok", 0); tot = d["total"]
        pend = c.get("pending", 0); doneN = tot - pend
        bar_n = 24; fill = int(bar_n * doneN / tot) if tot else 0
        bar = "█" * fill + "░" * (bar_n - fill)
        extra = " ".join(f"{k}={v}" for k, v in sorted(c.items()) if k not in ("ok", "pending"))
        print(f"{eng:9} {scale:5} [{bar}] {doneN:3}/{tot:<3} ({100*doneN//tot if tot else 0:3}%)  "
              f"ok={okc} pending={pend} {extra}")
    print("=" * 64)

# ---------------- HTML dashboard (self-contained; published via Artifact) ----------------
def render_html(s):
    ST = {"ok": ("#1f9d55", "done"), "timeout": ("#c9820a", "timeout"), "unsupported": ("#8a5cf6", "unsup"),
          "not-run": ("#6b7280", "not-run"), "pending": ("#2b3240", "pending")}
    def cell_status(eng, scale, cls, m):
        tmpls = sorted(s["man"][scale][cls])
        sts = [s["done"].get((eng, scale, cls, t, m), "pending") for t in tmpls]
        if all(x == "ok" for x in sts): return "ok"
        if any(x == "pending" for x in sts) and any(x != "pending" for x in sts): return "partial"
        if all(x == "pending" for x in sts): return "pending"
        # mixed terminal statuses -> worst non-ok
        for bad in ("err", "timeout", "unsupported", "not-run"):
            if any(str(x).startswith(bad) for x in sts): return bad
        return "ok"
    running = bool(s["running"])
    badge = ('<span class="badge run">● RUNNING</span>' if running
             else '<span class="badge idle">○ IDLE</span>')
    blocks = []
    for (eng, scale), d in sorted(s["per"].items()):
        tot = d["total"]; pend = d["counts"].get("pending", 0); doneN = tot - pend
        pct = 100 * doneN // tot if tot else 0
        rowsg = []
        for cls in [c for c in CLASS_ORDER if c in s["man"][scale]]:
            cells = []
            for m in METHODS:
                cs = cell_status(eng, scale, cls, m)
                col = {"ok": "#1f9d55", "partial": "#3b82f6", "pending": "#2b3240",
                       "timeout": "#c9820a", "unsupported": "#8a5cf6", "not-run": "#6b7280",
                       "err": "#dc2626"}.get(cs, "#2b3240")
                cells.append(f'<td class="m" style="background:{col}" title="{cls} {m}: {cs}">{m}</td>')
            rowsg.append(f'<tr><th>{cls}</th>{"".join(cells)}</tr>')
        blocks.append(f'''<div class="panel">
          <div class="ph"><b>{html.escape(eng)}</b> · {scale}
            <span class="pct">{doneN}/{tot} · {pct}%</span></div>
          <div class="bar"><i style="width:{pct}%"></i></div>
          <table class="grid"><tr><th></th>{"".join(f"<th>{m}</th>" for m in METHODS)}</tr>{"".join(rowsg)}</table>
        </div>''')
    updated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s["now"]))
    last = ago(s["mtime"], s["now"])
    body = f'''<main>
      <header><h1>SPARQLcirc R9.2 — construction matrix progress</h1>
        <div class="status">{badge}<span class="upd">snapshot {updated} · last row {html.escape(last)} · {s["rows"]} rows</span></div>
        <div class="live"><span id="open">opened just now</span> · refresh for a newer snapshot (redeployed each milestone)</div>
      </header>
      <p class="note">B/R/N/C construction timing decomposition. Each square = one query class × method
        (green all-done, blue in-progress, grey pending, amber timeout). This page is a snapshot; it
        refreshes to the latest state each time a milestone is reported.</p>
      <div class="grids">{"".join(blocks)}</div>
      <div class="legend">
        <span><i style="background:#1f9d55"></i>done</span>
        <span><i style="background:#3b82f6"></i>in-progress</span>
        <span><i style="background:#2b3240"></i>pending</span>
        <span><i style="background:#c9820a"></i>timeout</span>
        <span><i style="background:#8a5cf6"></i>unsupported</span>
        <span><i style="background:#6b7280"></i>not-run</span>
      </div>
    </main>'''
    css = '''*{box-sizing:border-box}:root{--bg:#f7f8fa;--fg:#0f1720;--mut:#5b6472;--card:#fff;--line:#e3e7ee;--accent:#2563eb}
    @media(prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--mut:#9aa4b2;--card:#161b22;--line:#232a33;--accent:#5b9bff}}
    :root[data-theme=dark]{--bg:#0d1117;--fg:#e6edf3;--mut:#9aa4b2;--card:#161b22;--line:#232a33;--accent:#5b9bff}
    :root[data-theme=light]{--bg:#f7f8fa;--fg:#0f1720;--mut:#5b6472;--card:#fff;--line:#e3e7ee;--accent:#2563eb}
    body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
    main{max-width:900px;margin:0 auto;padding:28px 20px}
    h1{font-size:20px;margin:0 0 8px;letter-spacing:-.01em}
    .status{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
    .badge{font-weight:700;padding:3px 12px;border-radius:20px;font-size:13px}
    .badge.run{background:rgba(31,157,85,.15);color:#1f9d55;animation:pulse 1.6s ease-in-out infinite}.badge.idle{background:rgba(107,114,128,.18);color:var(--mut)}
    @keyframes pulse{0%,100%{opacity:1}50%{opacity:.55}}
    @media(prefers-reduced-motion:reduce){.badge.run{animation:none}}
    .upd{color:var(--mut);font-size:12.5px;font-variant-numeric:tabular-nums}
    .live{color:var(--mut);font-size:12px;margin-top:4px;width:100%;font-variant-numeric:tabular-nums}
    .note{color:var(--mut);font-size:13px;max-width:70ch;margin:6px 0 20px}
    .grids{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
    .panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
    .ph{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px;font-size:15px}
    .pct{color:var(--mut);font-size:13px;font-variant-numeric:tabular-nums}
    .bar{height:7px;background:var(--line);border-radius:5px;overflow:hidden;margin-bottom:12px}
    .bar i{display:block;height:100%;background:var(--accent);border-radius:5px;transition:width .4s}
    table.grid{border-collapse:separate;border-spacing:3px;width:100%}
    table.grid th{color:var(--mut);font-weight:600;font-size:12px;text-align:center;padding:2px}
    table.grid th:first-child{text-align:left;width:24px}
    td.m{color:#fff;font-size:11px;font-weight:700;text-align:center;border-radius:5px;padding:6px 0;opacity:.92}
    .legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:18px;color:var(--mut);font-size:12.5px}
    .legend span{display:flex;align-items:center;gap:6px}.legend i{width:12px;height:12px;border-radius:3px;display:inline-block}'''
    script = ('<script>var t0=Date.now();setInterval(function(){var s=Math.round((Date.now()-t0)/1000);'
              'document.getElementById("open").textContent="opened "+(s<60?s+"s":Math.floor(s/60)+"m"+(s%60)+"s")+" ago";},1000);</script>')
    return f"<title>SPARQLcirc R9.2 progress</title><style>{css}</style>{body}{script}"

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--html"); args = ap.parse_args()
    s = snapshot()
    text(s)
    if args.html:
        open(args.html, "w").write(render_html(s))
        print(f"wrote {args.html}")

if __name__ == "__main__":
    main()
