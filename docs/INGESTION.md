# OTLP trace ingestion, version 1

TraceCause accepts a single uncompressed OTLP/JSON `ExportTraceServiceRequest` object (`otlp_json`) or newline-delimited request objects (`otlp_jsonl`). The latter is parsed one bounded line at a time. The [OTLP JSON specification](https://opentelemetry.io/docs/specs/otlp/) requires case-insensitive hex trace/span IDs, integer enum values and lowerCamelCase fields; the parser enforces these rather than guessing base64. The pinned official conformance fixture and attribution are in `data/fixtures/official/`. The `collector_file_json_v1` adapter name is reserved but explicitly rejects input until a pinned Collector file-exporter capture is tested in Phase 12. Metrics, logs, profiles, Protobuf binary, gzip and archives are unsupported in this path.

Run a local normalization with:

```bash
tracecause normalize \
  --episode-dir data/fixtures/smoke/0eeb41fb-4596-588e-8a2d-e6ba464fbf29 \
  --artifact-root /tmp/tracecause-artifacts \
  --output-dir smoke-v1
```

The output directory must be a new relative path inside an absolute, non-symlink artifact root. The episode directory must contain exactly `manifest.json` and declared regular source files; evaluator roots, sidecars and symlinks are refused. The current CLI supports one trace source per episode. It writes `report.json` and, on success, redacted `spans.jsonl`. The default is strict: any rejected record exits with code 2 and no spans file. `--allow-partial` exits 0 for nonfatal record errors, writes only valid nonconflicting spans, and sets `status=partial` with counts and safe diagnostics. Oversized files/lines and hash mismatch are fatal even with partial mode. Original input bytes are read only and never rewritten. The report distinguishes the SHA-256 of original source bytes from the SHA-256 of redacted normalized JSONL output.

Limits: 25 MiB per trace source, 4 MiB per NDJSON line, 16 nested JSON levels, 128 items per object/array and 128 attributes per span/resource. IDs must be 32/16 hex characters and nonzero. Nanosecond times must be decimal integers in signed BIGINT range, with end at or after start. Status codes are 0/1/2 and span kinds 0–5; unknown values are rejected. Missing resource identity is retained as unresolved, missing namespace/environment use distinct key sentinels, and missing parent spans receive a quality flag. Out-of-order spans are accepted. Identical duplicate keys count once; conflicting duplicates are quarantined, both records removed, and a safe conflict diagnostic is emitted.

Redaction version `1.0` keeps only selected typed HTTP/RPC status codes, HTTP methods and safe exception/error categories. Headers, cookies, tokens, bodies, query strings, arbitrary attributes and status/exception messages are dropped. Unsafe or dynamic operation names become `<redacted-operation>`; only reviewed MiniShop HTTP route names are retained. Service identity accepts bounded safe characters and rejects common secret/PII patterns; a missing or unsafe service name becomes unresolved. Redaction is deliberately conservative and may discard useful context. These rules reduce common leaks but cannot guarantee removal of every possible personal identifier in arbitrary text. No raw telemetry goes to the provider boundary.
