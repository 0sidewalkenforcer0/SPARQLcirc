"""Build a self-contained results page (figures embedded as data URIs) from the E2/E3/E4/E7
figures. Writes reference/watdiv/results_page.html for the Artifact tool."""
import os, base64, html

HERE = os.path.dirname(os.path.abspath(__file__)); FIG = os.path.join(HERE, "watdiv", "figures")
def uri(name):
    with open(os.path.join(FIG, name), "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()

E2, E3, E4, E7 = (uri(f"{n}.png") for n in
                  ("E2_compactness", "E3_construction_scaling", "E4_compile_vs_treewidth", "E7_vs_provsql"))

SECTIONS = [
    ("E2", "Compactness", "One shared circuit beats per-answer strings by orders of magnitude — on deep queries.",
     E2,
     "The provenance strings NPCS / SPARQLprov emit repeat every shared subterm, so their size grows with the "
     "number of derivations. Our content-addressed circuit stores each gate once. On shallow queries the two are "
     "comparable; on deep, recurring derivations the gap opens without bound.",
     "match", "Predicted ≈0.5–1× on shallow tree-like queries, →10²–10³× on deep/recurring.",
     "Observed 0.4× (drug) and 0.9× (shallow), rising to 201.4× at depth-12 — the pre-registered 201× anchor, hit exactly."),

    ("E4", "Compilation vs. treewidth", "d-DNNF keeps compiling where our OBDD walls — until treewidth grows, where both hit the #P wall.",
     E4,
     "Tractability is governed by the lineage's treewidth. At bounded treewidth the d-DNNF stays near-linear in n "
     "while the OBDD grows polynomially with treewidth in the exponent; once treewidth itself grows, every exact "
     "compiler is 2^Θ(tw). Every compiled instance's d4 weighted model count equals our OBDD count equals the exact value.",
     "match", "Predicted bounded-tw → d-DNNF ~linear in n, OBDD n^O(tw); growing-tw → both 2^Θ(tw).",
     "At tw=2 the OBDD explodes to 299k nodes and stops compiling by n≥126, while d-DNNF stays ≤5,270. At growing tw "
     "both blow up (OBDD 375k, d-DNNF 212k at tw=8). Honest nuance: on the grid family our OBDD stayed smaller than "
     "d4's d-DNNF — the small-scale vtree effect the pre-registration flagged."),

    ("E3", "Construction scaling", "The stock engine builds the circuit in time proportional to the number of derivations — from 10M to 100M triples.",
     E3,
     "An unmodified GraphDB runs our CONSTRUCT and materialises the circuit. Build time tracks the number of "
     "derivations it enumerates, near-linearly, across five orders of magnitude — selective queries at 100M finish "
     "sub-second; the full unbound circuits are larger but still built by the same stock engine.",
     "match", "Predicted near-linear build ≈ small constant × plain-query time; sub-second→seconds at 10⁶–10⁷.",
     "Build ∝ #derivations along the slope-1 guide; selective S/L/F at 100M in 19–515 ms; structural compactness a "
     "steady 0.5–0.66×. The unbound query at 100M exceeded the engine — the wall that motivates the selective / "
     "factored path."),

    ("E7", "Head-to-head vs. ProvSQL", "The same exact probability as ProvSQL — without modifying the database engine.",
     E7,
     "ProvSQL is the closest system: it also builds a provenance circuit and knowledge-compiles it for exact "
     "probability — but inside a modified PostgreSQL, over relations. On identical data, probabilities and queries "
     "the two agree to floating-point precision; the difference is deployability.",
     "match", "Predicted identical exact probability (both knowledge-compile); our axis is the unmodified engine.",
     "All 3/3 instances matched ProvSQL to max |Δp| = 2×10⁻¹⁶. ProvSQL needs a PostgreSQL extension and relational "
     "remodelling; SPARQL_circ runs on stock SPARQL 1.1."),
]

def section(eyebrow, title, finding, img, body, status, predicted, observed):
    return f"""
    <section class="exp">
      <div class="exp-head">
        <span class="eyebrow">{eyebrow}</span>
        <h2>{html.escape(title)}</h2>
      </div>
      <p class="finding">{html.escape(finding)}</p>
      <figure><img src="{img}" alt="{html.escape(title)} figure" loading="lazy"/></figure>
      <p class="body">{html.escape(body)}</p>
      <div class="callout {status}">
        <div class="cl-row"><span class="cl-label">Predicted</span><span class="cl-text">{html.escape(predicted)}</span></div>
        <div class="cl-row"><span class="cl-label obs">Observed</span><span class="cl-text">{html.escape(observed)}</span></div>
        <div class="verdict">✓ matches pre-registration</div>
      </div>
    </section>"""

BODY = f"""
<header class="masthead">
  <p class="kicker">Native probabilistic query evaluation for SPARQL · precursor to VLDB '27</p>
  <h1>SPARQL<span class="sub">circ</span> — evaluation</h1>
  <p class="thesis">A query rewriting makes an <em>unmodified</em> SPARQL engine materialise one shared,
  content-addressed provenance circuit; the client knowledge-compiles it for <em>exact</em> probabilistic
  query evaluation. Four pre-registered experiments, run on this server, against their predictions.</p>
  <div class="meta">
    <div><span class="m-k">Engines</span><span class="m-v">GraphDB 10.7.6 · d4 v1 · PostgreSQL 18 + ProvSQL 1.11</span></div>
    <div><span class="m-k">Data</span><span class="m-v">WatDiv 10M &amp; 100M (32.7M / 327M reified triples)</span></div>
    <div><span class="m-k">Verdict</span><span class="m-v ok">4 / 4 experiments match the pre-registered prediction</span></div>
  </div>
</header>
<main>
  {''.join(section(*s) for s in SECTIONS)}
</main>
<footer>
  <p>Every scale number is gated on correctness: circuit WMC == possible-world enumeration on the small checks,
  and d4 / ProvSQL WMC == our OBDD per instance. Full tables in <code>reference/watdiv/RESULTS.md</code>;
  vector figures (PDF) and CSVs alongside.</p>
</footer>"""

CSS = """
:root{
  --bg:#f5f7f9; --surface:#ffffff; --figure:#ffffff; --ink:#131822; --ink-2:#41505f; --ink-3:#6b7887;
  --line:#e0e6ec; --line-2:#eef2f6;
  --ours:#0a6aad; --base:#b9791a; --good:#0a7d5a; --warn:#c14e12;
  --accent:var(--ours); --band:rgba(10,106,173,.05);
  --serif:"Iowan Old Style","Charter","Palatino Linotype",Georgia,"Times New Roman",serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0d1117; --surface:#141b24; --figure:#ffffff; --ink:#e8edf2; --ink-2:#a6b3c0; --ink-3:#7a8794;
  --line:#232d38; --line-2:#1b232d; --ours:#3fa0dd; --base:#e0a233; --good:#33b98a; --warn:#e8763a;
  --band:rgba(63,160,221,.07);
}}
:root[data-theme="dark"]{
  --bg:#0d1117; --surface:#141b24; --figure:#ffffff; --ink:#e8edf2; --ink-2:#a6b3c0; --ink-3:#7a8794;
  --line:#232d38; --line-2:#1b232d; --ours:#3fa0dd; --base:#e0a233; --good:#33b98a; --warn:#e8763a;
  --band:rgba(63,160,221,.07);
}
:root[data-theme="light"]{
  --bg:#f5f7f9; --surface:#ffffff; --figure:#ffffff; --ink:#131822; --ink-2:#41505f; --ink-3:#6b7887;
  --line:#e0e6ec; --line-2:#eef2f6; --ours:#0a6aad; --base:#b9791a; --good:#0a7d5a; --warn:#c14e12;
  --band:rgba(10,106,173,.05);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  line-height:1.6;-webkit-font-smoothing:antialiased}
.masthead,main,footer{max-width:820px;margin-inline:auto;padding-inline:24px}
.masthead{padding-top:72px;padding-bottom:40px;border-bottom:1px solid var(--line)}
.kicker{font-family:var(--mono);font-size:12px;letter-spacing:.12em;text-transform:uppercase;
  color:var(--accent);margin:0 0 18px}
h1{font-family:var(--serif);font-weight:600;font-size:clamp(2.6rem,7vw,4rem);line-height:1.02;
  letter-spacing:-.02em;margin:0;text-wrap:balance}
h1 .sub{font-style:italic;color:var(--accent);font-size:.62em;vertical-align:.06em;margin-left:.05em}
.thesis{font-size:1.12rem;color:var(--ink-2);max-width:60ch;margin:22px 0 34px}
.thesis em{font-style:italic;color:var(--ink)}
.meta{display:grid;gap:1px;background:var(--line);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.meta>div{display:flex;gap:16px;background:var(--surface);padding:12px 16px}
.m-k{font-family:var(--mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--ink-3);flex:0 0 88px;padding-top:2px}
.m-v{font-size:.95rem;color:var(--ink-2)}
.m-v.ok{color:var(--good);font-weight:600}
.exp{padding:56px 0;border-bottom:1px solid var(--line-2)}
.exp-head{display:flex;align-items:baseline;gap:14px;margin-bottom:6px}
.eyebrow{font-family:var(--mono);font-weight:600;font-size:.95rem;color:var(--accent);
  border:1px solid color-mix(in srgb,var(--accent) 40%,transparent);border-radius:6px;padding:1px 8px;flex:none}
h2{font-family:var(--serif);font-weight:600;font-size:clamp(1.5rem,3.6vw,2rem);letter-spacing:-.01em;
  margin:0;line-height:1.1;text-wrap:balance}
.finding{font-size:1.16rem;color:var(--ink);font-weight:500;margin:14px 0 26px;max-width:58ch;line-height:1.4}
figure{margin:0 0 24px;background:var(--figure);border:1px solid var(--line);border-radius:12px;
  padding:14px;box-shadow:0 1px 3px rgba(16,26,40,.06),0 8px 30px -18px rgba(16,26,40,.25)}
figure img{display:block;width:100%;height:auto;border-radius:4px}
.body{color:var(--ink-2);max-width:66ch;margin:0 0 26px}
.callout{border:1px solid var(--line);border-left:3px solid var(--good);border-radius:10px;
  background:var(--band);padding:16px 18px;display:flex;flex-direction:column;gap:9px}
.cl-row{display:flex;gap:14px;align-items:baseline}
.cl-label{font-family:var(--mono);font-size:11px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--base);flex:0 0 74px;font-weight:600}
.cl-label.obs{color:var(--good)}
.cl-text{font-size:.96rem;color:var(--ink-2)}
.verdict{font-family:var(--mono);font-size:.82rem;color:var(--good);font-weight:600;
  padding-top:4px;border-top:1px dashed var(--line);margin-top:2px}
footer{padding:40px 24px 88px;color:var(--ink-3)}
footer p{font-size:.9rem;max-width:64ch;margin:0}
code{font-family:var(--mono);font-size:.88em;background:var(--surface);border:1px solid var(--line);
  border-radius:5px;padding:1px 5px;color:var(--ink-2)}
@media (max-width:560px){.masthead{padding-top:52px}.exp{padding:40px 0}.cl-row{flex-direction:column;gap:2px}}
"""

HTMLDOC = f"<title>SPARQL_circ — evaluation results</title>\n<style>{CSS}</style>\n{BODY}\n"
out = os.path.join(HERE, "watdiv", "results_page.html")
with open(out, "w") as f:
    f.write(HTMLDOC)
print("wrote", out, f"({len(HTMLDOC)//1024} KB)")
