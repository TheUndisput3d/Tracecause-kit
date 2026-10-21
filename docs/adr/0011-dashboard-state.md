# ADR0011: Scoped dashboard reads and schema-derived browser validation

Date:2026-10-12. Status:accepted implementation decision under the user's autonomous-build instruction.

The Router owns episode/snapshot/cutoff/temporal/retrieval URL state. TanStack Query owns remote data, polling, bounded GET retry and abort signals. React owns selected paths, dialogs and the memory-only operator token. There is no persistent browser credential storage or automatic inference on cursor changes.

The contract exporter additionally writes a browser JSON Schema registry from the same Pydantic definitions used for API schemas/OpenAPI. Zod's installed schema converter constructs validators from that generated registry, avoiding separately maintained request/response enums. Contract tests exercise every exported schema and reject extra/malformed response fields. This converter is pinned with the existing dependency lock; it is not treated as an independently verified validator for domain invariants, which remain server-side.

Graph/timeline reads include scope, method and configuration identity in their cache keys. They debounce300ms and consume AbortSignal. Results from prior scope keys are never used as placeholder data for a new scope. Saved explanations remain explicitly stale until the operator runs a new request. Citations always retain the original result scope.

The dashboard seed uses isolated PostgreSQL and artifact roots, real public data and real worker services. Simulated UI boundary tests are separately identified. A quiet cursor selected from public signals tests healthy rendering without changing the retained final-cutoff result. This is a UI regression fixture, not a new accuracy measurement.
