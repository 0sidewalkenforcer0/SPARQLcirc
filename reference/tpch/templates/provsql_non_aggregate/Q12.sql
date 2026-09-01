SELECT
  l.l_shipmode AS "l_shipmode"
FROM orders AS o
JOIN lineitem AS l ON l.l_orderkey = o.o_orderkey
WHERE l.l_shipmode IN ('FOB', 'REG AIR')
  AND l.l_commitdate < l.l_receiptdate
  AND l.l_shipdate < l.l_commitdate
  AND l.l_receiptdate >= DATE '1993-01-01'
  AND l.l_receiptdate < DATE '1994-01-01';
