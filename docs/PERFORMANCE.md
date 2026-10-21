# Measured performance

Phase17 measurements use a developer host (Intel Core i7-13700HX,24 logical CPUs),
real PostgreSQL/Neo4j and pinned CPU MiniLM. They do not establish performance on
the proposed4-vCPU/8GiB deployment target. Reports retain actual Python/platform,
affinity/memory, source sizes, counts and concurrency. Filesystem caches are not
flushed; first-process calls and later warm calls are distinguished explicitly.
No inference provider is called in these performance runs.

## Import measurement and decision

`scripts.performance.import_benchmark` imports100,000 deterministic synthetic
spans from four13,480,000-byte source files into a fresh isolated database. The
baseline took105.497seconds,40,223 SQL execution calls and695,296KiB peak process
RSS. Nullable fields split ORM batches. Explicit `render_nulls=True` keeps those
columns in batches, retaining actual SQL NULL semantics. Three repetitions took
96.688,96.359 and approximately97.70seconds, each423 SQL calls and approximately
695,140–698,084KiB peak RSS (precise values in reports). The engineering target
is600seconds; measured tolerances are stricter but allow host noise.

An explicit multi-VALUES experiment took132.060seconds and was rejected. Its
first run briefly overlapped the preceding repetition; it is not an isolated
speed comparison. Remaining repetitions were interrupted and are not passes.
Raw results and `phase17-import-experiment-notes.json` are retained.

## Reference investigations

Ten sequential investigations per mode used fresh result identities, with every
returned citation reopened. Concurrent HTTP graph/metadata reads used40requests
per endpoint and four workers. Before corpus reuse, warm lexical p95 was1095.975ms;
after reuse585.010ms (nine warm samples per mode). Graph p95 changed976.891→980.514ms;
no graph speed improvement is claimed. Warm lexical median526.406ms and graph
median858.870ms in the second diagnostic. Zero errors, fallbacks or provider calls
were observed. Full eligible corpus stays internal and is excluded from serialized
retrieval results. Scope/temporal checks and source validation still execute.

The reference script records stage distributions and separate external wall time.
Legacy `total` ends before source validation; `pipeline_before_publication` includes
validation, while external wall time includes persistence. Do not compare these as
identical latency definitions. API metrics are authenticated process-local request
histograms and actual queue counts; they do not pretend to aggregate worker stages.
Worker/result records provide stage timings. No private route or raw question is a
metric label. `/internal/metrics` is forbidden in public read-only mode.

## Regression gate

`make performance-check` creates new measurements and fails above3s warm
investigation p95,500ms metadata p95,1s graph p95,180s import,1.2GiB process RSS or
1,000 import SQL calls. Errors and provider calls must be zero. These are local
regression tolerances derived from measurements, not production guarantees.
See the phase report for the final executed gate and stored-row equality result.

Final local budget run `reports/performance/20261012T084637158503Z` passed all
eight checks: lexical/graph warm p95=599.089/789.532ms; metadata/graph HTTP
p95=16.512/100.475ms;100k import96.942s,692,992KiB peak RSS,423SQL calls.
Across three import repeats, observed throughput was1,023.58–1,037.79spans/s
(0.614–0.623completed100k-span jobs/minute), with zero failed jobs.
These counts describe this bounded import workload, not production arrival capacity.

Phase19 post-citation-repair regression at
`reports/performance/20261012T095829708727Z` also passed all eight budgets:
lexical/graph warm p95=760.122/942.235ms (nine warm samples each), metadata/graph
HTTP p95=66.644/141.091ms (40 requests each, concurrency4),100k import96.782s,
692,340KiB peak process RSS and423SQL calls. This confirms local tolerances after
source revalidation; it is not a claim of improvement over phase17 or a VM SLA.
