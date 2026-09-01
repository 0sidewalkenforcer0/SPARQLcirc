SELECT
  l.l_returnflag AS "l_returnflag",
  l.l_linestatus AS "l_linestatus"
FROM lineitem AS l
WHERE l.l_shipdate <= DATE '1998-09-24';
