SELECT
  EXTRACT(YEAR FROM o.o_orderdate)::integer AS "o_year"
FROM part AS p
JOIN lineitem AS l ON l.l_partkey = p.p_partkey
JOIN supplier AS s ON s.s_suppkey = l.l_suppkey
JOIN nation AS n2 ON n2.n_nationkey = s.s_nationkey
JOIN orders AS o ON o.o_orderkey = l.l_orderkey
JOIN customer AS c ON c.c_custkey = o.o_custkey
JOIN nation AS n1 ON n1.n_nationkey = c.c_nationkey
JOIN region AS r ON r.r_regionkey = n1.n_regionkey
WHERE p.p_type = 'PROMO POLISHED TIN'
  AND r.r_name = 'AFRICA'
  AND o.o_orderdate >= DATE '1995-01-01'
  AND o.o_orderdate <= DATE '1996-12-31';
