// Server-owned one-hop query. Repeat in bounded application traversal.
// All parameters come from validated scope/config, never LLM-generated Cypher.
MATCH (a:Service)-[r:CALLS]->(b:Service)
WHERE a.id IN $frontier_ids
  AND a.workspace_id = $workspace_id AND a.snapshot_id = $snapshot_id
  AND b.workspace_id = $workspace_id AND b.snapshot_id = $snapshot_id
  AND r.workspace_id = $workspace_id AND r.snapshot_id = $snapshot_id
  AND EXISTS {
    MATCH (o:Observation)
    WHERE o.id IN r.support_observation_ids
      AND o.workspace_id = $workspace_id AND o.snapshot_id = $snapshot_id
      AND o.window_end_ns <= $cutoff_ns
      AND o.window_end_ns >= $lookback_start_ns
      AND ($available_mode = false OR
           (o.available_at_ns IS NOT NULL AND o.available_at_ns <= $cutoff_ns))
  }
RETURN a.id AS source_id, b.id AS target_id, r.id AS edge_id
ORDER BY source_id, target_id, edge_id
LIMIT $bounded_limit;
// A separate allowlisted query supports incoming-neighbor exploration while
// preserving caller->callee orientation. Return only eligible support IDs via
// another scoped query; do not return an unfiltered support array as evidence.
