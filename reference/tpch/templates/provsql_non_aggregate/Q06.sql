SELECT
  1::integer AS "x"
FROM lineitem AS l
WHERE l.l_shipdate >= DATE '1993-01-01'
  AND l.l_shipdate < DATE '1994-01-01'
  AND l.l_discount >= 0.06
  AND l.l_discount <= 0.08
  AND l.l_quantity < 25;
