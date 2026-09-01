SELECT
  n1.n_name AS "supp_nation",
  n2.n_name AS "cust_nation",
  EXTRACT(YEAR FROM l.l_shipdate)::integer AS "l_year"
FROM supplier AS s
JOIN nation AS n2 ON n2.n_nationkey = s.s_nationkey
JOIN lineitem AS l ON l.l_suppkey = s.s_suppkey
JOIN orders AS o ON o.o_orderkey = l.l_orderkey
JOIN customer AS c ON c.c_custkey = o.o_custkey
JOIN nation AS n1 ON n1.n_nationkey = c.c_nationkey
WHERE (
    (n1.n_name = 'MOZAMBIQUE' AND n2.n_name = 'UNITED KINGDOM')
    OR
    (n1.n_name = 'UNITED KINGDOM' AND n2.n_name = 'MOZAMBIQUE')
  )
  AND l.l_shipdate >= DATE '1995-01-01'
  AND l.l_shipdate <= DATE '1996-12-31';
