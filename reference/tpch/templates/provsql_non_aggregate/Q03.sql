SELECT
  'http://example.org/Order/' || o.o_orderkey::text AS "order",
  o.o_orderdate AS "o_orderdate",
  o.o_shippriority AS "o_shippriority"
FROM customer AS c
JOIN orders AS o ON o.o_custkey = c.c_custkey
JOIN lineitem AS l ON l.l_orderkey = o.o_orderkey
WHERE c.c_mktsegment = 'FURNITURE'
  AND o.o_orderdate < DATE '1995-03-17'
  AND l.l_shipdate > DATE '1995-03-17';
