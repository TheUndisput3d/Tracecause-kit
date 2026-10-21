# Frontend implementation specification

## F1. Product shape and selected packages

Build a desktop-first responsive investigation workbench, not a generic chatbot. The user should understand the incident, inspect the graph and verify evidence without writing a query. Use React 19 + TypeScript strict mode + Vite, React Router, TanStack Query, Tailwind CSS, Radix UI primitives, `lucide-react`, Cytoscape.js, Recharts, Zod, `react-markdown` and `rehype-sanitize`. Use npm with a committed package lock. Vite is a development/build tool, not the production server. Verify compatible maintained versions in phase 00.

React Router owns navigation. TanStack Query owns remote state, retries and cancellation. Local React state owns selection and open panels. The URL owns episode, snapshot, time cursor and retrieval mode. Do not add Redux, a second component system, Next.js, a rich text editor or a separate frontend backend. Cytoscape.js directly owns the graph canvas through a ref/effect; do not depend on an unverified React wrapper. Its documented interaction API and MIT license suit this use [S22]. Use native semantic tables rather than another table library at this scale.

Testing dependencies: Vitest, React Testing Library, `@testing-library/user-event`, MSW for isolated UI failure tests, Playwright, and `@axe-core/playwright`. Production workflows must use real backend data, not MSW. Generate TypeScript API definitions with `openapi-typescript`; use a typed fetch adapter. Validate untrusted responses at critical boundaries with generated or explicitly matched Zod schemas. Use one schema source; do not hand-maintain inconsistent enum values.

## F2. Visual and interaction system

Use a restrained technical dashboard: neutral background, clear bordered panels, readable sans-serif system fonts, 16 px body text, 12-14 px metadata, 4/8 px spacing steps, and 8 px corner radii. Offer light/dark themes using CSS variables, defaulting to the OS preference. Error severity is not proof of fault location. Use semantic icons, patterns and labels as well as color. No brand font download is required.

Desktop at 1280 px and wider: 240 px navigation/episode sidebar; flexible main panel; 340-400 px evidence drawer. At 768-1279 px use a collapsible sidebar and a modal drawer. Under 768 px stack hypothesis cards, charts and a graph/table tab; do not squeeze the full desktop layout. Minimum supported viewport is 360 x 740. Use `min-width: 0` on flex children, ellipsis with full accessible labels for long service names, and scroll only within bounded panels.

Use visible focus rings, labeled inputs, keyboard-accessible dialogs, an aria-live region for job progress, reduced-motion handling, and sufficient contrast. Canvas nodes are not a substitute for accessible content: provide a sortable service/edge table with the same selection and evidence actions. Destructive confirmations name the dataset/snapshot and are operator-only.

## F3. Routes and screen specifications

### `/` - overview

Show project purpose, data-origin explanation and an episode list. Columns: episode display label, origin (`synthetic` or `captured_demo`), interval, service count, valid-span count, completeness warning, latest snapshot status and last saved investigation status. No hidden fault label or inferred root-cause service belongs in this list. Filters: origin, ready/importing/failed, and text search over public metadata. Paginate at 20 rows. Provide an empty state with local operator import instructions, not fake cards. A "Try demo" action opens a genuinely seeded episode. Display a banner when public mode is replay-only.

### `/episodes/:episodeId` - investigation workbench

Header: episode name, data origin, snapshot ID, current as-of time, replay mode, generation mode and model label. A compact data-quality banner reports sampling, incomplete traces, absent baseline and missing availability timestamps.

Controls: question input (2,000-character maximum), optional symptom service, method selector (DENSE/LEXICAL/HYBRID/GRAPH), ranking selector (heuristic/learned when registered), cutoff slider in 10-second steps, exact UTC timestamp input, replay mode, and Run investigation. Validate before submission. Display human-readable UTC times; preserve canonical UTC ISO-8601 in URLs/API. Do not silently convert a selected cutoff to the browser's timezone.

Main area: a timeline chart above a two-column hypothesis/graph workspace. Hypothesis cards contain rank, service, ranking-score label, supporting evidence count, limitations, conflicting evidence and read-only diagnostic checks. A badge says "hypothesis", never "confirmed cause". An empty hypothesis array with `no_anomaly_detected` is a valid result, not a UI error. Scores are not percentages unless explicitly calibrated and evaluated later.

Graph: observed service nodes and directional CALLS edges; separate badges for selected hypothesis and anomaly. Node tooltip shows the eligible request/error/latency summaries and quality flags. Edge tooltip shows caller, callee, span-pair provenance and permitted window. Selecting a hypothesis highlights its validated support paths. Selecting an edge opens the evidence drawer. Keep graph positions stable while changing the cutoff; preserve pan/zoom unless Reset is clicked. Use Cytoscape's built-in breadth-first layout for the small graph; no additional layout package is necessary. Cap display at 80 nodes/120 edges and explain truncation.

Timeline: Recharts line charts for SERVER-span p95 latency and error fraction, each with units, a legend and a text summary. Use null gaps for missing buckets, not zeros. The analysis cutoff and baseline region are visible. Do not draw hidden intervention intervals in an active investigation. Evaluation-only intervention overlays may appear only in clearly separated completed benchmark reports. A chart's data must respect the same cutoff/availability rules as the graph.

Evidence drawer: source ID/hash, document publication or observation interval, eligible availability metadata, source type, exact quoted text or typed metrics, supporting trace/span references, excerpt offsets, and a breadcrumb back to the claim. Never construct a file URL from user input. Render text excerpts as escaped text; Markdown is sanitized with raw HTML disabled. Copy citation produces an evidence ID plus snapshot/cutoff, not a made-up academic reference.

### `/investigations/:investigationId` - stable result permalink

Render a persisted immutable result, including original model/prompt/config versions and original cutoff. Opening a permalink never reruns the model. Show "saved result" and generation origin. Provide JSON export through an authorized backend download and a printable text view. Do not expose private evaluator labels in exports.

### `/compare` - retrieval/evaluation comparison

Allow the operator to compare the same episode, question, cutoff, final evidence count and context budget under multiple retrieval methods. A live comparison creates separate bounded jobs only in local operator mode. Public mode selects saved comparison bundles. Show retrieved evidence IDs, unique/repeated evidence, graph path count, latency split, candidate coverage and generation mode. Quality metrics appear only from a published evaluation artifact, with sample size, split, dataset origin and uncertainty. Display NOT RUN rather than illustrative percentages. A baseline must not secretly receive less source data.

### `/evaluations` and `/evaluations/:reportId`

List sanitized published reports. Detail tabs: retrieval, fault localization, citation audit, abstention, performance, ablations and limitations. Include dataset hash, split hash, commit, feature schema, model revision and hardware. Show synthetic and captured results separately. Link to dataset/model cards, not private truth files. Mark tiny sample sizes and missing tests prominently.

### `/admin/imports` - local operator panel

Require a token entered by the operator; keep it only in memory, never localStorage, a URL or a Vite environment variable. Show a supported-manifest upload form, file-size limits and dry-run validation. Import returns a job ID. Progress shows queued/validating/parsing/projecting/validating-snapshot/publishing/done, with record counts and rejection diagnostics. Provide cancel at safe checkpoints. No signup, password reset or production identity provider is required for this single-operator capstone. Public builds may render the route as unavailable, but backend enforcement is still mandatory.

## F4. Component inventory and ownership

Create `AppShell`, `EpisodeTable`, `OriginBadge`, `SnapshotBadge`, `TimeCursor`, `QuestionForm`, `MethodControls`, `InvestigationProgress`, `HypothesisCard`, `EvidenceCitation`, `EvidenceDrawer`, `DependencyGraph`, `GraphLegend`, `GraphTable`, `SignalTimeline`, `QualityBanner`, `ComparisonPanel`, `EvaluationTable`, `LimitationsPanel`, `ImportForm`, `ErrorPanel` and `OperatorTokenDialog`. Components take typed props; graph/ranking business logic stays on the server.

Folders: `src/app/`, `src/routes/`, `src/features/episodes/`, `src/features/investigations/`, `src/features/graph/`, `src/features/evidence/`, `src/features/evaluation/`, `src/features/admin/`, `src/components/ui/`, `src/lib/api/`, `src/lib/queryKeys.ts`, `src/styles/`, and `src/test/`. Keep API errors, formatting, theme and query-key factories centralized.

## F5. Network workflow, cancellation and cache identity

Query keys include episode, snapshot, cutoff, temporal mode, method and configuration hash. The Run button POSTs a validated request with a fresh idempotency key; successful 202 returns job and investigation IDs. Poll jobs at 1 second initially and 2 seconds after 10 seconds, stopping on a terminal state or unmount. Do not use WebSockets for version 1. Refetch the immutable result once succeeded. Display phase progress rather than a fabricated token stream.

Changing the time cursor updates cheap graph/series reads after a 300 ms debounce; it does not run the LLM automatically. Changing the cursor marks the previous explanation stale and requires an explicit Run. Use AbortController and request identity checks so earlier responses cannot overwrite a later selection. Browser cancellation does not imply server job cancellation; an explicit operator Cancel calls the cancel endpoint. Do not automatically retry POST mutations. Retry safe GET failures at most twice with bounded backoff, except 401/403/404/409.

## F6. Required states and browser tests

Test loading, empty, healthy, insufficient evidence, partial/truncated graph, ambiguous entity, invalid cutoff, unpublished snapshot, import rejected, queue full, model unavailable, model fallback, token missing, unauthorized, timeout, failed dependency and saved-replay modes. Every state must have a truthful user-facing message and a defined recovery action.

Playwright must select a real episode, run a real template investigation, follow every displayed citation, inspect a path, move the cutoff rapidly, verify stale-result protection, open a saved report and confirm operator routes are denied anonymously. A separately tagged local-LLM browser smoke test must really call Ollama. Inspect 360/768/1440 px screenshots; test keyboard navigation, long labels and malicious markup. Browser test fixtures may seed data through an explicitly test-only CLI, never through a public bypass endpoint.
