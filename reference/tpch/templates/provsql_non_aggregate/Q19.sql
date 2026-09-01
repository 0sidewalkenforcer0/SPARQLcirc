SELECT
  1::integer AS "x"
FROM lineitem AS l
JOIN part AS p ON p.p_partkey = l.l_partkey
WHERE (
    p.p_brand = 'Brand13'
    AND p.p_container IN ('SM CASE', 'SM BOX', 'SM PACK', 'SM PKG')
    AND l.l_quantity >= (6) AND l.l_quantity <= (6) + 10
    AND p.p_size >= 1 AND p.p_size <= 5
    AND l.l_shipmode IN ('AIR', 'AIR REG')
    AND l.l_shipinstruct = 'DELIVER IN PERSON'
  )
  OR (
    p.p_brand = 'Brand#43'
    AND p.p_container IN ('MED BAG', 'MED BOX', 'MED PKG', 'MED PACK')
    AND l.l_quantity >= (11) AND l.l_quantity <= (11) + 10
    AND p.p_size >= 1 AND p.p_size <= 10
    AND l.l_shipmode IN ('AIR', 'AIR REG')
    AND l.l_shipinstruct = 'DELIVER IN PERSON'
  )
  OR (
    p.p_brand = 'Brand#55'
    AND p.p_container IN ('LG CASE', 'LG BOX', 'LG PACK', 'LG PKG')
    AND l.l_quantity >= (27) AND l.l_quantity <= (27) + 10
    AND p.p_size >= 1 AND p.p_size <= 15
    AND l.l_shipmode IN ('AIR', 'AIR REG')
    AND l.l_shipinstruct = 'DELIVER IN PERSON'
  );
