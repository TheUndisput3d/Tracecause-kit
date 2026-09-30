# Operational application captures

Actual local HTTP and queue workloads captured on **19 October 2026**, from
05:05:26 to 05:10:40 UTC. These are controlled engineering experiments, not
production telemetry or independently labelled incident diagnoses.

## What ran

Each service ran in a separate Python subprocess on an ephemeral loopback port.
The capture supervisor started real processes, checked their health, deployed
different executable release artifacts, wrote real configuration files and
measured request outcomes. All applications ran on one physical development host.

| Application | Architecture | Services |
|---|---|---|
| Parcel Shop | Synchronous chain | `checkout → orders → ledger` |
| Newsroom | Concurrent fanout | `portal → search`, `pricing`, `recommendations` |
| Media Works | Asynchronous queue and processing chain | `uploader → broker`; a worker consumes jobs and calls `transcoder → object-store` |

Media jobs retain an actual producer/consumer parent-span relationship. A message
handoff is distinct from an HTTP call: the export does not invent a
`broker → worker` synchronous call. Queue polling and completion callbacks are
also real HTTP requests and appear in the raw traces.

Each application has two chronological episodes with stable namespace,
environment and service identity. Every episode includes 70 seconds of baseline,
60 seconds of changed workload, and 20 seconds of recovery, at three business
operations per second. Process startup, aligned change boundaries and shutdown
add real elapsed time to those intervals; timestamps were never backdated.

## Retained measurements

The source audit finds **28,935 valid spans**, **129 operational receipts** and
**900 directly linked producer/consumer pairs**. Every HTTP server service has
eight populated ten-second baseline buckets. The queue worker emits internal,
client and consumer spans rather than pretending to be an HTTP request server.

| Application | Episode | Valid spans | Operational events | Baseline success | Changed success / failure | Recovery success |
|---|---:|---:|---:|---:|---:|---:|
| Parcel Shop | 1 | 2,250 | 19 | 210 | 90 / 90 | 60 |
| Parcel Shop | 2 | 2,250 | 16 | 210 | 0 / 180 | 60 |
| Newsroom | 1 | 3,150 | 23 | 210 | 90 / 90 | 60 |
| Newsroom | 2 | 3,150 | 20 | 210 | 0 / 180 | 60 |
| Media Works | 1 | 9,094 | 27 | 210 | 90 / 90 | 60 |
| Media Works | 2 | 9,041 | 24 | 210 | 0 / 180 | 60 |

There are zero rejected spans, duplicate spans or missing parents. The source
audit also checks every manifest hash, file length, operational event timestamp,
span interval and deployed release artifact hash. This demonstrates source
integrity and observable workload changes; it is not a diagnosis accuracy result.

## Files and trust boundaries

- [`index.json`](index.json): public application identities, measured intervals,
  source hashes, capture implementation hashes and demonstration replay cursors.
- `episodes/<UUID>/manifest.json`: exact import allowlist for one episode.
- `episodes/<UUID>/traces.jsonl`: manually instrumented OTLP envelopes recording
  actual call start/end times, response statuses and parent propagation.
- `episodes/<UUID>/operational.json`: typed process-supervisor receipts for real
  deployments, versions, configuration writes, process inventory, ownership
  assignments and measured incident summaries.
- `artifacts/<SHA-256>.py`: the exact small executable release policy artifacts.
- `capture-sources/<SHA-256>.py`: the exact capture/exporter implementations that
  produced the retained dataset. The current exporter additionally hardens URL
  parsing; the archive preserves the executed version without rewriting evidence.

Ownership is explicitly **operator-assigned lab ownership**, not an assertion
about a real company. Host identity is a stable digest of the local host name;
process IDs and version identifiers come from the actual launched subprocesses.
Incident summaries report observed counts and do not assert root causes.

Original control and command receipts remain separately under
`reports/operational-lab/private/`. Minimal reference labels are also published in
[`docs/evaluation/operational/labels`](../../docs/evaluation/operational/labels)
so the bundled six-case comparison can be reproduced. Their provenance manifest
records both original and sanitized receipt hashes. Neither private controls nor
published labels are manifest sources or runtime inputs; evaluation documentation
is excluded from the Docker build context. The Docker seed copies only the public
episode directories and public index.

The experiment protocol was declared before inspecting reasoning outputs. Its
SHA-256 is
`0794ccd8b6ff3f352b35c99ad2db036362b5cbce75c3dbf90bc8e7aaa3b9e80e`.
Every changed window includes a newer benign change on the entry service, so
chronological order alone does not identify the intended changed component.
Offline evaluation must retain all six cases and use the same eligible evidence
and candidate budget for graph and non-graph methods.

## Reproduce or audit

From the repository root, with locked Python dependencies installed:

```bash
uv run --locked python -m scripts.capture.operational_lab \
  --output /tmp/tracecause-new-capture \
  --private /tmp/tracecause-new-capture-private

uv run --locked python -m scripts.capture.operational_lab_audit \
  --root data/operational-lab \
  --output reports/operational-lab/audit.json

uv run --locked pytest tests/unit/test_operational_capture.py \
  tests/capture/test_operational_lab.py tests/unit/test_operational_seed.py
```

Fresh captures have new real timestamps, process IDs, UUIDs and measured latency;
their byte hashes are expected to differ. The stored dataset is immutable.

## Limits

This corpus contains three deliberately small applications and two incidents per
application, not a representative sample of production systems. Versions are
tiny controlled release policies, all infrastructure shares one physical host,
and ownership is assigned by the experiment operator. It supports reproducible
tests of temporal joins, provenance, graph traversal and historical comparison.
It does not establish production causal identification or broad diagnostic
generalization. The seven-to-eight populated baseline buckets are sufficient for
the declared error/latency baseline; missing earlier traffic must not be treated
as evidence of a real zero-volume baseline.
