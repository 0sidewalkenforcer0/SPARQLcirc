SELECT
  n.n_name AS "n_name"
FROM customer AS c
JOIN orders AS o ON o.o_custkey = c.c_custkey
JOIN lineitem AS l ON l.l_orderkey = o.o_orderkey
JOIN supplier AS s ON s.s_suppkey = l.l_suppkey
JOIN nation AS n ON n.n_nationkey = c.c_nationkey
  AND n.n_nationkey = s.s_nationkey
JOIN region AS r ON r.r_regionkey = n.n_regionkey
WHERE r.r_name = 'AMERICA'
  AND o.o_orderdate >= DATE '1993-01-01'
  AND o.o_orderdate < DATE '1994-01-01';
