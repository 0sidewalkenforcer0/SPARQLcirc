SELECT
  'http://example.org/Order/' || o.o_orderkey::text AS "order",
  o.o_orderpriority AS "o_orderpriority"
FROM orders AS o
JOIN lineitem AS l ON l.l_orderkey = o.o_orderkey
WHERE o.o_orderdate >= DATE '1993-07-01'
  AND o.o_orderdate < DATE '1993-10-01'
  AND l.l_commitdate < l.l_receiptdate;
