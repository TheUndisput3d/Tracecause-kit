---
source_id: demo-doc-12
version: '1.0'
title: Sparse traffic interpretation
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
---
# Sparse traffic interpretation

This authored demonstration note discusses catalog in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** Short windows with few calls can produce unstable percentile estimates.

## Diagnostic check

**Check:** Inspect sample counts, baseline bucket coverage and quality flags before ranking.

## Limits

Sparse traffic can hide a real issue or exaggerate a single outlier.
