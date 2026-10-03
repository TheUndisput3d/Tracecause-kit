---
source_id: demo-doc-11
version: '1.0'
title: HTTP error interpretation
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
---
# HTTP error interpretation

This authored demonstration note discusses checkout in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** HTTP 4xx and 5xx counts carry different operational meanings.

## Diagnostic check

**Check:** Compare explicit span ERROR status with supported HTTP response codes.

## Limits

Do not treat every HTTP 4xx response as a service failure.
