---
source_id: demo-doc-09
version: '1.0'
title: Product list cache checks
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
curated_service_refs:
  - namespace: minishop
    environment: demo
    name: catalog
    alias: product-list
---
# Product list cache checks

This authored demonstration note discusses product-list in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** Cached catalog content can be stale while request latency remains normal.

## Diagnostic check

**Check:** Inspect catalog status and compare responses with observed inventory calls.

## Limits

Stale content is not established by latency telemetry alone.
