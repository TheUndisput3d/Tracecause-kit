---
source_id: demo-doc-01
version: '1.0'
title: Storefront request overview
origin: authored_demo
document_type: runbook
published_at: 2025-12-01T00:00:00Z
license: Apache-2.0
attribution: TraceCause authored demonstration corpus
---
# Storefront request overview

This authored demonstration note discusses storefront in the minishop demo. It is guidance or a historical report, not a measurement of the current episode.

## Observation or report

**Symptom:** A slow customer request can reflect time spent in a downstream hop or local rendering.

## Diagnostic check

**Check:** Compare the storefront SERVER span with its child CLIENT span before assigning responsibility.

## Limits

A long parent span alone does not identify the slow child.
