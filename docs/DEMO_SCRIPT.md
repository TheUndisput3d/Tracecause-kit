# Portfolio demonstration

A three-minute core walkthrough shows the operational knowledge graph, competing
change explanations, immutable source evidence, historical comparison and an
asynchronous handoff. Run a fresh local-model investigation as an optional second
part; inference time is outside the three-minute core.

## Prepare the application

From the repository root:

```sh
docker compose -f compose.full.yaml up --build -d --wait --wait-timeout 2400
```

Open **http://127.0.0.1:14000** and wait for the six captured episodes to appear
in **Incident library**. Use **Newsroom — captured incident 2** for the main
walkthrough and **Media Works — captured incident 2** for the asynchronous example.
The second episode in each application has a completed earlier episode to compare.
The initial cutoff and symptom come from the capture's public metadata.

Use a 1440 × 1000 browser viewport for a recording. Browsing evidence requires no
operator token. To prepare an actual generated report, follow the optional model
section below before recording. Retrieve the token privately and keep the terminal
that displays it outside the recording.

Port 14000 is the full local application. Port 13000 is the separate older
read-only release; development servers are not the deployed application. See the
[complete Docker guide](DOCKER_FULL_STACK.md) for startup diagnostics.

## Three-minute core

| Time | Action | Explain what the audience is seeing |
|---|---|---|
| 0:00–0:20 | Open **Newsroom — captured incident 2**. Show the captured origin, immutable snapshot, UTC cutoff and actual service/span counts. | This is a real local capture of a small controlled application. PostgreSQL and immutable artifacts are authoritative; Neo4j provides a source-checked temporal projection. |
| 0:20–0:55 | Use **Changes & history**, then **Change analysis**. Inspect a **Review first** lead, its service path, **Inspect change record** and **Inspect observed signal**. Briefly expand **Recorded signal and baseline**. | Changes are joined with directed dependencies, timing and latency/error measurements. Later, healthy and disconnected changes remain visible. Review order is a heuristic association; it does not establish a cause. |
| 0:55–1:25 | Open **System graph**. Show service → version → runtime connections, configuration, deployment, host and ownership records. Choose a **Context service** and **Context service footprint**, then open **Relationship table** and inspect a source. | Each typed relationship has supporting evidence. The focused canvas reduces visual clutter; the table retains the full returned relationship set. Source drawers show the exact scope and artifact identity. The receipt JSON is the validated record from the immutable bundle. |
| 1:25–1:55 | Open **Previous incidents**. Show similarity components and the earlier/current state table. Open **Earlier source**, then **Current source** for one difference. | Historical comparison stays within the same application and environment. Both sides preserve their own episode, snapshot and cutoff. Similarity describes shared structure and signals; it is not diagnostic confidence. |
| 1:55–2:25 | Return to the library and open **Media Works — captured incident 2**. In **System graph → Relationship table**, find `MESSAGE_FLOW` and select **Inspect relationship record**. | The uploader's producer span directly parents a consumer span on the worker. Show the shared trace identity, parent/span IDs, separate artifact hashes and normalized record offsets. The HTTP call to the broker remains a distinct `CALLS` edge. |
| 2:25–3:00 | Return to the episode used for the prepared model run. Open its **Saved investigations**, inspect the generation badge and one summary citation, then select **Export JSON**. If no report has been prepared, use this time to show **Investigation settings** and continue with the optional fresh run below. | A saved report is playback of an immutable result. Opening it does not call Qwen again. Its generation origin, evidence, original cutoff and provenance remain inspectable. |

**Useful narration:** “The graph makes relationships inspectable. The measurements
show what was observed. The change review and historical comparison help decide
where to investigate next; they are not proof of root cause.”

The graph is bounded and source dependent. These captures exercise three small
application topologies; they do not establish production coverage or independent
human diagnostic accuracy. Do not equate operational review priority, historical
similarity and the separate investigation ranker's scores.

## Optional: run Qwen live

This step performs new model computation. Its duration depends on the host,
provider readiness, retrieval and queue state; allow additional time. The recorded
run's timing metadata is an observation, not a latency promise.

1. Retrieve the locally generated token in a private terminal:

   ```sh
   docker compose -f compose.full.yaml exec -T api cat /run/secrets/operator_token
   ```

2. In the episode, select **Connect operator** and paste the token. Close the
   connection dialog before beginning the public walkthrough.
3. Expand **Investigation settings**. Choose **Retrieval method: GRAPH**,
   **Generation: ollama**, and the application's entry service as the symptom
   (`portal` for Newsroom, `checkout` for Parcel Shop, `uploader` for Media Works).
   Keep the displayed snapshot and useful default cutoff.
4. Enter a fresh incident-review question and select **Run investigation**. Show
   the actual job states and phases that appear; a short-lived queued state may
   finish before the browser polls it.
5. At **Investigation findings**, inspect the generation badge. For an actual
   successful model demonstration it must read **ollama**, with no fallback
   notice. If the provider fails, describe that result as a failure or fallback;
   do not present it as successful model generation.
6. Open **Summary source 1**, inspect the measurement/excerpt and source identity,
   then open the saved investigation and **Export JSON**. The export preserves
   `generation_origin`, `fallback_reason`, timings, scope and evidence.

### Explain the model boundary accurately

Local Qwen selects verified fact IDs for the existing measurement/runbook
investigation pipeline. Server code renders the selected facts' values and
citations, and validates the result. `template` is deterministic generation.
The learned ranker is a separate experimental component; the default ranking is
heuristic.

Operational changes, historical comparison and message-handoff proofs are computed
by source-checked rules. Their receipts and historical state differences are not
passed to Qwen as new narrative facts in this implementation. Show both workflows
as complementary capabilities with distinct responsibilities.

### Reproduce the browser recordings

The recording scripts drive the actual deployed browser and inspect real API
responses. They do not intercept requests or supply mocked results.

```sh
# Operational graph, sources, history, responsive layouts and dark theme.
node apps/web/scripts/operational-showcase.mjs \
  http://127.0.0.1:14000 reports/operational-showcase \
  b1a5371e-d1f0-400f-9fe6-0c15b8ce70fd

# The asynchronous capture also records the MESSAGE_FLOW source drawer.
node apps/web/scripts/operational-showcase.mjs \
  http://127.0.0.1:14000 reports/message-flow-showcase \
  94fc555d-b189-40ae-8a4b-8458691f0ad8
```

These commands require Node 24 and the installed frontend and Playwright browser
packages; see [development instructions](../README.md#development-and-verification) for setup. The AI recorder additionally
requires `TRACECAUSE_DEMO_TOKEN` in its environment and rejects a fallback result:

```sh
node apps/web/scripts/ai-showcase.mjs \
  http://127.0.0.1:14000 reports/ai-showcase \
  b1a5371e-d1f0-400f-9fe6-0c15b8ce70fd portal
```

Set the token privately before running this command. Reports contain the observed
scope and result status; inspect them before using a video as demonstration
evidence. A recorded video is a previous live run, while a saved-result page is
read-only playback. Neither performs a fresh inference for the viewer.

After all three captures pass, `apps/web/scripts/assemble-showcase.mjs` accepts
the Newsroom, Media Works and AI output directories and publishes the selected
media to `docs/assets/operational`. It requires successful source inspections,
responsive/dark accessibility checks, actual model generation and verified export.
Its `manifest.json` records capture scopes, outcomes and media SHA-256 hashes;
raw duplicate browser video files are not copied into the gallery.

## Appendix: original MiniShop authored-check demonstration

The original synthetic MiniShop episode remains useful for demonstrating authored
knowledge and exact quotes. Its runbooks belong to that namespace/environment;
they are not inherited by similarly named services in the operational captures.

| Step | Action and explanation |
|---|---|
| 1 | Open a **synthetic demo** episode and state its origin, snapshot and UTC cutoff. |
| 2 | Open **Explore connected evidence**, select **storefront**, and inspect **Checks connected through dependencies**. Show a downstream check, its exact quote and **Inspect call step**. Explain the service → `CALLS` → service ← assertion → excerpt path. Calls establish observed communication; the check is authored guidance. |
| 3 | Switch to **Evidence relationships**. Show document, excerpt, assertion, observation and service entities. Open a source and its hash/offsets. Projected assertions are checked against their source bytes. |
| 4 | Move the exact cutoff 20 seconds earlier, **Pin this cutoff**, then return to the final cutoff. Inspect the dependency comparison. An edge absent from the six-minute window does not establish that a dependency was removed. |
| 5 | Choose GRAPH retrieval and `template` generation for a deterministic report, or `ollama` for an additional actual model run. Inspect an abnormal measurement and a baseline citation. Identify the generation mode shown by the result. |
| 6 | Open the saved result and export. State that the data is controlled, ranking is heuristic by default, and the project has no independent human validation. |

The earlier authored-KG competency test found 21/21 expected checks across eight
declared synthetic topologies, versus 8/21 for direct service lookup. This measures
relationship-query behavior, not root-cause accuracy. Keep that evaluation separate
from the operational capture ablations, captured development ranking results and
the original frozen v3 test results in **Evaluation reports**.
