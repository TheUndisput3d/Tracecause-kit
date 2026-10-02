# Collector file exporter conformance fixture

`otlp.jsonl` is one complete 22-span trace copied from the real MiniShop smoke capture on 11 October 2026. It was emitted by `otel/opentelemetry-collector-contrib:0.162.0` using its JSON file exporter and filtered by root trace ID without changing span data. SHA-256: `2bf7667535df7356c679388237a89bae7c209f96775409b3bb47530cdde54237`. The private control token and fault labels are absent. It tests the Collector's actual JSONL envelope and all five service identities; it is not a performance or accuracy sample.
