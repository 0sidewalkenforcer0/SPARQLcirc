CREATE TABLE part (
  p_partkey bigint NOT NULL,
  p_name text NOT NULL,
  p_mfgr text NOT NULL,
  p_brand text NOT NULL,
  p_type text NOT NULL,
  p_size integer NOT NULL,
  p_container text NOT NULL,
  p_retailprice numeric(15, 2) NOT NULL,
  p_comment text NOT NULL,
  _trailer text
);

CREATE TABLE region (
  r_regionkey bigint NOT NULL,
  r_name text NOT NULL,
  r_comment text NOT NULL,
  _trailer text
);

CREATE TABLE nation (
  n_nationkey bigint NOT NULL,
  n_name text NOT NULL,
  n_regionkey bigint NOT NULL,
  n_comment text NOT NULL,
  _trailer text
);

CREATE TABLE supplier (
  s_suppkey bigint NOT NULL,
  s_name text NOT NULL,
  s_address text NOT NULL,
  s_nationkey bigint NOT NULL,
  s_phone text NOT NULL,
  s_acctbal numeric(15, 2) NOT NULL,
  s_comment text NOT NULL,
  _trailer text
);

CREATE TABLE partsupp (
  ps_partkey bigint NOT NULL,
  ps_suppkey bigint NOT NULL,
  ps_availqty integer NOT NULL,
  ps_supplycost numeric(15, 2) NOT NULL,
  ps_comment text NOT NULL,
  _trailer text
);

CREATE TABLE customer (
  c_custkey bigint NOT NULL,
  c_name text NOT NULL,
  c_address text NOT NULL,
  c_nationkey bigint NOT NULL,
  c_phone text NOT NULL,
  c_acctbal numeric(15, 2) NOT NULL,
  c_mktsegment text NOT NULL,
  c_comment text NOT NULL,
  _trailer text
);

CREATE TABLE orders (
  o_orderkey bigint NOT NULL,
  o_custkey bigint NOT NULL,
  o_orderstatus text NOT NULL,
  o_totalprice numeric(15, 2) NOT NULL,
  o_orderdate date NOT NULL,
  o_orderpriority text NOT NULL,
  o_clerk text NOT NULL,
  o_shippriority integer NOT NULL,
  o_comment text NOT NULL,
  _trailer text
);

CREATE TABLE lineitem (
  l_orderkey bigint NOT NULL,
  l_partkey bigint NOT NULL,
  l_suppkey bigint NOT NULL,
  l_linenumber integer NOT NULL,
  l_quantity numeric(15, 2) NOT NULL,
  l_extendedprice numeric(15, 2) NOT NULL,
  l_discount numeric(15, 2) NOT NULL,
  l_tax numeric(15, 2) NOT NULL,
  l_returnflag text NOT NULL,
  l_linestatus text NOT NULL,
  l_shipdate date NOT NULL,
  l_commitdate date NOT NULL,
  l_receiptdate date NOT NULL,
  l_shipinstruct text NOT NULL,
  l_shipmode text NOT NULL,
  l_comment text NOT NULL,
  _trailer text
);
