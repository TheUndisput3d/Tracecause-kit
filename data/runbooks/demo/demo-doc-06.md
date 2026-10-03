---
source_id: demo-doc-06
version: '1.0'
title: Storefront edge retry note
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
curated_service_refs:
  - namespace: minishop
    environment: demo
    name: storefront
    alias: front-end
---
# Storefront edge retry note

This authored demonstration note discusses front-end in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** A repeated edge request can inflate volume without changing downstream health.

## Diagnostic check

**Check:** Count unique SERVER spans and check whether CLIENT attempts share one user request.

## Limits

The term front-end is a curated alias only within the demo namespace.
