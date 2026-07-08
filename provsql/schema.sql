-- E7 ProvSQL harness schema. Requires PostgreSQL + the ProvSQL extension.
-- Load order: this file, then the INSERTs from rdf_to_sql.py, then add_provenance.

CREATE EXTENSION IF NOT EXISTS provsql;

DROP TABLE IF EXISTS triples CASCADE;
CREATE TABLE triples (s text, p text, o text);

-- ... run the INSERT INTO triples(...) statements emitted by rdf_to_sql.py here ...

-- one provenance variable (gate) per base triple  == our leaf token:
-- SELECT add_provenance('triples');

-- probabilities (align with the values used on our side for a fair comparison):
-- ALTER TABLE triples ADD COLUMN proba double precision;
-- UPDATE triples SET proba = <p>;                 -- per-row probability
-- then register each row's probability with its provenance gate via set_prob(...).
