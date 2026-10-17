// Community-compatible uniqueness constraints; verify exact chosen release.
CREATE CONSTRAINT service_id IF NOT EXISTS FOR (n:Service) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT observation_id IF NOT EXISTS FOR (n:Observation) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT assertion_id IF NOT EXISTS FOR (n:Assertion) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT snapshot_id IF NOT EXISTS FOR (n:Snapshot) REQUIRE n.id IS UNIQUE;
CREATE INDEX service_scope IF NOT EXISTS FOR (n:Service) ON (n.workspace_id, n.snapshot_id);
CREATE INDEX observation_scope_time IF NOT EXISTS FOR (n:Observation) ON (n.snapshot_id, n.window_end_ns);
CREATE INDEX retrievable_scope IF NOT EXISTS FOR (n:Retrievable) ON (n.workspace_id, n.snapshot_id);
// Version 1 uses exact eligible-vector scans. Optional ANN/full-text DDL
// belongs in a separately verified migration, not an assumed universal API.
