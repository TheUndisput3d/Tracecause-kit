---
source_id: demo-doc-02
version: '1.0'
title: Checkout timing checklist
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
---
# Checkout timing checklist

This authored demonstration note discusses checkout in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** Checkout can show high latency while a dependency remains healthy.

## Diagnostic check

**Check:** Compare SERVER duration, outgoing CLIENT duration and completion status for the same trace.

## Limits

Retries and queuing can lengthen checkout without a slower dependency.
