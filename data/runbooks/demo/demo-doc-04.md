---
source_id: demo-doc-04
version: '1.0'
title: Catalog read diagnostics
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
---
# Catalog read diagnostics

This authored demonstration note discusses catalog in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** Catalog reads can become sparse or slow during normal inventory changes.

## Diagnostic check

**Check:** Inspect request counts, status and dependency timing in matching ten-second buckets.

## Limits

A low sample count should be shown as missingness, not certainty.
