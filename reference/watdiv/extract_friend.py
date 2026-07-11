"""Extract a BOUNDED, connected friendOf subgraph from WatDiv (real edges) for the Round-3 path
experiment: BFS from a well-connected source to ~CAP nodes, then take the INDUCED friendOf subgraph
(all edges among those nodes) so multi-hop reachability exists. Reify it and emit a bound source."""
import re, sys, collections
ALL = sys.argv[1]; OUT = sys.argv[2]; CAP = int(sys.argv[3]) if len(sys.argv) > 3 else 80
RS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

adj = collections.defaultdict(list)
for line in open(ALL):
    p = line.split(None, 2)
    if len(p) < 3: continue
    s = p[0].strip("<>"); o = p[2].rstrip(" .\n").strip("<>")
    adj[s].append(o)

# source = a node with decent out-degree (so paths branch)
src = max(adj, key=lambda u: len(adj[u]))
# BFS to collect up to CAP nodes
seen = {src}; q = collections.deque([src])
while q and len(seen) < CAP:
    u = q.popleft()
    for v in adj.get(u, []):
        if v not in seen:
            seen.add(v); q.append(v)
            if len(seen) >= CAP: break
# induced friendOf subgraph on `seen`
edges = [(u, v) for u in seen for v in adj.get(u, []) if v in seen]
n = 0
with open(OUT, "w") as g:
    for (u, v) in edges:
        t = f"<urn:t:{n}>"
        g.write(f"{t} <{RS}subject> <{u}> .\n{t} <{RS}predicate> <http://db.uwaterloo.ca/~galuc/wsdbm/friendOf> .\n"
                f"{t} <{RS}object> <{v}> .\n")
        n += 1
print(f"source={src.rsplit('/',1)[-1]}  nodes={len(seen)}  edges={n}  (reified {3*n} triples) -> {OUT}")
print("SRC=" + src)
