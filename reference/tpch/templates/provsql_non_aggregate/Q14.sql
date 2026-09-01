SELECT
  1::integer AS "x"
FROM lineitem AS l
JOIN part AS p ON p.p_partkey = l.l_partkey
WHERE l.l_shipdate >= DATE '1993-04-01'
  AND l.l_shipdate < DATE '1993-05-01';
