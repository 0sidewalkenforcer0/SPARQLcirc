SELECT
  'http://example.org/Customer/' || c.c_custkey::text AS "customer",
  c.c_name AS "c_name",
  c.c_acctbal AS "c_acctbal",
  n.n_name AS "n_name",
  c.c_address AS "c_address",
  c.c_phone AS "c_phone",
  c.c_comment AS "c_comment"
FROM customer AS c
JOIN orders AS o ON o.o_custkey = c.c_custkey
JOIN lineitem AS l ON l.l_orderkey = o.o_orderkey
JOIN nation AS n ON n.n_nationkey = c.c_nationkey
WHERE l.l_returnflag = 'R'
  AND o.o_orderdate >= DATE '1993-11-01'
  AND o.o_orderdate < DATE '1994-02-01';
