---
source_id: demo-doc-05
version: '1.0'
title: Inventory latency checklist
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
---
# Inventory latency checklist

This authored demonstration note discusses inventory in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** Inventory responses may be delayed even when the caller returns success.

## Diagnostic check

**Check:** Compare inventory SERVER p50 and p95 with its own baseline and call support.

## Limits

A one-off tail sample may not represent a sustained incident.
