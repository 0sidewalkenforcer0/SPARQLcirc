SELECT
  'http://example.org/Supplier/' || l.l_suppkey::text AS "supplier",
  'http://example.org/LineItem/' || l.l_orderkey::text || '/' ||
    l.l_linenumber::text AS "lineitem"
FROM lineitem AS l
WHERE l.l_shipdate >= DATE '1996-01-01'
  AND l.l_shipdate < DATE '1996-04-01';
