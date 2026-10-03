---
source_id: demo-doc-13
version: '1.0'
title: Trace parent validation
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
---
# Trace parent validation

This authored demonstration note discusses storefront in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** A shared trace ID can include several branches that are not direct calls.

## Diagnostic check

**Check:** Confirm parent-span ancestry and caller-to-callee direction for each dependency.

## Limits

A shared trace ID alone does not establish a CALLS edge.
