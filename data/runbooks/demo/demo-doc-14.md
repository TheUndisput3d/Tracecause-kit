---
source_id: demo-doc-14
version: '1.0'
title: Asynchronous work caveat
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
---
# Asynchronous work caveat

This authored demonstration note discusses checkout in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** Queue work may continue after a request has completed.

## Diagnostic check

**Check:** Separate asynchronous links from synchronous parent-child spans.

## Limits

The first graph projection does not infer queue or async dependencies.
