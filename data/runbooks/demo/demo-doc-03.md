---
source_id: demo-doc-03
version: '1.0'
title: Payment response triage
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
---
# Payment response triage

This authored demonstration note discusses payment in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** Payment responses may have explicit HTTP errors or an upstream timeout.

## Diagnostic check

**Check:** Separate payment SERVER status from checkout CLIENT status and inspect matched trace IDs.

## Limits

An upstream timeout does not prove that payment itself failed.
