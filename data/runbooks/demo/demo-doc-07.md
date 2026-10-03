---
source_id: demo-doc-07
version: '1.0'
title: Order flow dependency map
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
curated_service_refs:
  - namespace: minishop
    environment: demo
    name: checkout
    alias: orders
---
# Order flow dependency map

This authored demonstration note discusses orders in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** The order path includes several independently observed service calls.

## Diagnostic check

**Check:** Walk supported caller-to-callee edges and inspect each observation end time.

## Limits

A graph path is a communication path, not a root-cause chain.
