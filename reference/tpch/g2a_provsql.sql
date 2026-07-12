-- G2a — ProvSQL PQE on TPC-H Q3 (head-to-head vs ours; see reference/G2a_RESULTS.md).
-- Reproducible SQL for the ProvSQL side. Run against a PostgreSQL with the ProvSQL extension:
--   PGHOST=$WS/pgsock PGPORT=54320 psql -d provsqltest -f g2a_provsql.sql
-- Prereq (once): the .tbl files loaded into schema g2a (SF0.01) / g2a1 (SF0.1) with trailing
-- '|' stripped:  sed 's/|$//' customer.tbl > customer.s.tbl ; \copy g2a.customer FROM 'customer.s.tbl' (FORMAT csv, DELIMITER '|')
-- Tables use explicit column DDL (NOT `LIKE` a provenanced table — that copies the provenance column
-- and breaks COPY). add_provenance must come AFTER the data is loaded.

SET search_path = g2a, public, provsql;

-- 1. Mark each base table's rows as uncertain tokens.
SELECT add_provenance('g2a.customer');
SELECT add_provenance('g2a.orders');
SELECT add_provenance('g2a.lineitem');

-- 2. Per-token weight p = 0.5.  NOTE: set_prob takes provenance() over the BASE ROWS, bare —
--    NOT wrapped in an aggregate (count(set_prob(...)) => "set_prob called on non-input gate").
\o /dev/null
SELECT set_prob(provenance(), 0.5) FROM g2a.customer;
SELECT set_prob(provenance(), 0.5) FROM g2a.orders;
SELECT set_prob(provenance(), 0.5) FROM g2a.lineitem;
\o

-- 3. Timed Q3 SPJ PQE: probability(provenance()) per (order, line) answer.
\timing on
CREATE TEMP TABLE q3 AS
  SELECT o.o_orderkey, l.l_linenumber, probability(provenance()) AS p
  FROM g2a.customer c, g2a.orders o, g2a.lineitem l
  WHERE o.o_custkey = c.c_custkey
    AND l.l_orderkey = o.o_orderkey
    AND c.c_mktsegment = 'BUILDING';
\timing off

-- 4. Verify: 14908 answers (SF0.01), every probability = 0.5^3 = 0.125 (customer ⊗ order ⊗ line).
SELECT count(*) AS answers, round(min(p)::numeric,4) AS min_p, round(max(p)::numeric,4) AS max_p FROM q3;
