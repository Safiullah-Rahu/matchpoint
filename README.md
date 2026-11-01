# Matchpoint

[![ci](https://github.com/Safiullah-Rahu/matchpoint/actions/workflows/ci.yml/badge.svg)](https://github.com/Safiullah-Rahu/matchpoint/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![license](https://img.shields.io/badge/license-MIT-green)

**A multi-agent harness for accounts-payable invoice exceptions, built the way production agents need to be built:**
a deterministic state graph with LLM agents inside it, tool calling with least privilege, policy RAG with grounded
citations, human-in-the-loop approval, model routing with retries and fallbacks, prompt-injection and fraud controls,
OpenTelemetry-style tracing, and an eval suite that measures reliability (pass^k), safety and the effect of each control.

Runs fully offline with a deterministic reference model (no key, no cost), and on Claude when `ANTHROPIC_API_KEY` is set.

![Matchpoint UI: an injection attempt on an over-tolerance invoice, resolved as a credit memo request](docs/img/run.png)

---

## The business problem

When a supplier invoice fails the automatic three-way match (PO ↔ goods receipt ↔ invoice), it lands in an
exception queue. An AP specialist then works the case: compare quantities and prices, check the vendor master, look for
duplicates, read the policy manual, decide (pay, hold, request a credit memo, reject, escalate), write to the vendor,
and leave an audit trail. It is high-volume, rules-plus-judgment work, which makes it a good candidate for agents. It
also moves money, which is exactly where business-email-compromise fraud and prompt injection aim.

Matchpoint handles that queue end to end on a synthetic ERP with 52 labelled exception invoices (13 scenarios × 4
variants), including bank-detail fraud, duplicates in disguise, invoices above the approval limit, and vendor notes
that try to instruct the agent.

## What it demonstrates

| Capability | Where |
|---|---|
| Agents, multi-agent orchestration, typed hand-offs | 6 roles in [`agents/workflow.py`](src/matchpoint/agents/workflow.py); artifacts in [`domain/models.py`](src/matchpoint/domain/models.py) |
| Agent loop with repair, nudges, loop guard, budgets, parallel tools | [`harness/loop.py`](src/matchpoint/harness/loop.py) |
| State graph, checkpoints, interrupt/resume, crash recovery, fail-closed routing | [`harness/graph.py`](src/matchpoint/harness/graph.py), [`harness/checkpoint.py`](src/matchpoint/harness/checkpoint.py) |
| Tool integration: strict schemas, validation, timeouts, truncation + paging, read-only SQL | [`tools/`](src/matchpoint/tools) |
| RAG with grounded citations | [`rag/retriever.py`](src/matchpoint/rag/retriever.py) over the [policy manual](src/matchpoint/domain/policies) |
| Model routing: tiers, risk escalation, retries, circuit breakers, fallback, refusal fallbacks | [`llm/router.py`](src/matchpoint/llm/router.py), [`llm/anthropic_provider.py`](src/matchpoint/llm/anthropic_provider.py) |
| Context management and memory | append-only history, prompt caching, briefs instead of transcripts, verified-only vendor memory |
| AI security | injection screening + spotlighting, deterministic policy engine, redaction, output guard: [`guardrails/`](src/matchpoint/guardrails) |
| Human in the loop | controller approval above 25,000 via graph interrupt; UI inbox and CLI |
| Evals | graders on ERP end state, pass^k, chaos and faulty-model ablations, CI gate: [`evals/`](src/matchpoint/evals) |
| Observability | spans with OTel GenAI attributes, optional OTLP export, Prometheus `/metrics`, per-run cost: [`obs/`](src/matchpoint/obs) |
| Enterprise integration and deployment | ERP gateway with idempotent writes, FastAPI + bearer auth, Docker, CI, cloud mapping in [`docs/deployment.md`](docs/deployment.md) |

## Quickstart

```bash
git clone https://github.com/Safiullah-Rahu/matchpoint.git
cd matchpoint
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
matchpoint seed                                         # synthetic ERP + 52 labelled exceptions
matchpoint serve                                        # http://127.0.0.1:8400
```

From the terminal:

```bash
matchpoint queue                                        # the exception queue
matchpoint run INV-10087 --trace                        # BEC fraud + injection case, with the span tree
matchpoint run INV-10079                                # pauses for controller approval (41,122.50 > 25,000)
matchpoint approve <approval_id> --comment "checked"    # resumes the run from its checkpoint
matchpoint run INV-10083 --gullible                     # a model that obeys the injected note; watch it get blocked
matchpoint eval --trials 3                              # 156 runs, offline
matchpoint suite                                        # reference / chaos on / chaos off / faulty model
pytest                                                  # 63 tests
```

On Claude:

```bash
export ANTHROPIC_API_KEY=...                            # org-level keys may also need ANTHROPIC_WORKSPACE_ID
matchpoint run INV-10087 --provider anthropic --trace
matchpoint eval --provider anthropic --limit 13 --yes   # one variant per scenario
matchpoint serve --provider anthropic
```

Docker: `docker compose up --build` (add `--profile tracing` for Jaeger).

## Architecture

```mermaid
flowchart LR
    subgraph Graph["Deterministic state graph (checkpoint after every node)"]
        intake[intake<br/><i>injection screen</i>] --> triage[triage<br/><i>fast</i>] --> investigate[investigate<br/><i>tools</i>] --> policy[policy<br/><i>RAG</i>] --> decide[decide<br/><i>resolver</i>] --> review{review<br/><i>policy engine + LLM</i>}
        review -- revise --> decide
        review -- "> 25,000" --> approval[[human approval<br/><i>interrupt</i>]] --> execute
        review -- ok --> execute[execute<br/><i>idempotent</i>] --> communicate[communicate<br/><i>email guard</i>] --> finalize[finalize<br/><i>verified memory</i>]
        err[[any node error]] -.-> failsafe[fail_safe<br/><i>escalate, never pay</i>]
    end
    investigate <--> tools[(read-only tools<br/>ERP · match · duplicates · SQL)]
    policy <--> corpus[(policy manual<br/>BM25)]
    execute --> erp[(ERP gateway<br/>audit + idempotency)]
    Graph -.-> obs[[spans · metrics · cost]]
```

Models propose; deterministic code disposes. The graph decides what runs next; agents decide what evidence to gather,
which policy applies and what to propose; the policy engine checks the proposal against facts recomputed from the ERP
(never against the agent's own findings); the executor is the only code that writes, and it re-checks before writing.

| Role | Default model | Tools | Hands off |
|---|---|---|---|
| triage | `claude-haiku-5-5` | none | `TriageOutput` (category guess, risk) |
| investigator | `claude-sonnet-5-5` | invoice, vendor, PO, receipts, 3-way match, duplicates, history, SQL, paging | `Findings` |
| policy analyst | `claude-sonnet-5-5` | `search_policy`, `get_policy_section` | `PolicyAnalysis` (grounded rules) |
| resolver | `claude-sonnet-5-5` → `claude-opus-5-5` when high risk | `get_policy_section` | `Resolution` |
| reviewer | `claude-opus-5-5` | `get_policy_section` | `ReviewVerdict` |
| communicator | `claude-haiku-5-5` | none | `VendorEmail` |

More detail, including why the graph is ~200 lines of owned code rather than LangGraph and how it maps onto LangGraph:
[`docs/harness.md`](docs/harness.md).

## Reliability controls

- **Agent loop:** strict tool schemas; terminal tools as output contracts with validation-error repair; a nudge when
  the model answers in prose; loop guard; per-role turn limits; parallel read-only calls.
- **Graph:** checkpoint after every node; `Interrupt` + `resume` for approvals; crash recovery that re-runs the
  interrupted node; idempotency keys on every side effect (a crash between "pay" and "checkpoint" cannot double-pay;
  [test](tests/test_harness.py)); any node error routes to `fail_safe`; `max_steps`.
- **Model calls:** full-jitter backoff honouring `retry-after`; per-model circuit breakers; tier fallback chain;
  server-side refusal fallbacks; per-run budgets on calls, tokens, USD and wall clock. SDK retries are off so every
  attempt is visible in the trace.
- **Context:** typed briefs instead of transcripts; truncation at insertion with `read_result` paging; append-only
  history (keeps prompt caching and thinking-block replay valid); cached system prompts and tool definitions.
- **Degradation:** triage failure falls back to high risk; reviewer failure falls back to the deterministic checks;
  an email that fails its guard twice is left for a human. None of these block the decision.

## Security

The model is assumed to be fooled sometimes. What keeps that harmless:

1. Vendor text is screened at intake and at the tool boundary (instruction patterns, invisible Unicode tag characters
   decoded and scanned), wrapped in an escaped `<untrusted_vendor_text>` envelope, and the case is routed to the
   frontier model.
2. A deterministic policy engine checks every proposal against ERP facts: approvals only for clean matches at the exact
   total; with bank-detail risk or an inactive vendor, only hold or escalate; credit memos only for the computed
   overbilled amount; citations must exist.
3. The executor re-checks inside the write path and enforces the 25,000 release limit with human approval.
4. Bank numbers are masked before reaching a model; vendor emails pass an output guard; the SQL tool is read-only with
   a table allow-list; agents cannot write memory.

Full threat model with the test for each control: [`docs/security.md`](docs/security.md).

## Evals

52 labelled cases, isolated ERP copy per run, graders that read the ERP end state, pass@1 and **pass^k** (a case counts
only if all k trials pass). **Safety violation** = money moved or a vendor contacted when policy forbids it.

| Suite (52 cases × 3 trials) | pass@1 | pass^3 | Safety violations | Unsafe proposals blocked | Fail-safe runs | First-pass action | Faults injected | Retries |
|---|---|---|---|---|---|---|---|---|
| reference | **100.0%** | 100.0% | **0** | 0 | 0 | 100.0% | 0 | 0 |
| chaos, controls on | **99.4%** | 98.1% | **0** | 0 | 0 | 100.0% | 468 | 267 |
| chaos, controls off | 18.6% | 1.9% | **0** | 0 | 126 | 19.2% | 198 | 0 |
| faulty model | **97.4%** | 92.3% | **0** | 63 | 0 | 50.0% | 0 | 0 |

![Evals tab](docs/img/evals.png)

More screens: [fraud case](docs/img/fraud.png) · [awaiting controller approval](docs/img/approval.png) ·
[architecture](docs/img/arch.png). The UI's provider menu also has two demo presets: a gullible model that obeys
injected instructions, and 30% injected faults.

How to read this:

- **reference** validates labels, graders and plumbing: the reference policy must score 100%, or something in the eval
  is wrong.
- **chaos-controls-on vs off** isolates the harness. The same 25% fault rate (429/529/5xx/timeouts, malformed arguments,
  unknown tools, turns without a tool call) is absorbed with controls on and wrecks accuracy with them off. Accuracy
  collapses, but safety does not: with controls off every unrecoverable run fails closed to an escalation.
  The one controls-on failure is graceful degradation: the duplicate was rejected correctly, but after repeated
  injected faults the vendor email was left for manual drafting.
- **faulty-model** simulates a bad model: wrong first-pass decisions half the time (many of them unsafe), obedience to
  injected instructions, and a reviewer that misses a third of the safe-but-wrong proposals. The policy engine blocks
  every unsafe proposal; the revision loop recovers most of the accuracy; the remaining failures are unnecessary
  escalations, never payments.

These numbers come from the deterministic reference model (`sim`), so they measure the harness and the eval set, not a
language model's judgment. `matchpoint eval --provider anthropic --yes` runs the same suite on Claude; `--record` saves a
cassette so CI can replay it at zero cost. Details: [`docs/evals.md`](docs/evals.md), reports in
[`evals/reports/`](evals/reports).

## Observability

- Spans for run → node → agent → LLM call → tool, plus guardrail spans, with OpenTelemetry GenAI attribute names
  (`gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.cache_read_input_tokens`, ...). Stored in SQLite
  for the built-in waterfall; mirrored to any OTLP backend when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
- `trace_id == run_id`, so a run that pauses for approval and resumes later is one trace.
- `GET /metrics` (Prometheus): LLM calls, cost, latency histograms, retries, fallbacks, tool calls by status, guardrail
  violations by code, fail-safe count, resolutions by action.

## Project layout

```
src/matchpoint/
  agents/       workflow graph, role prompts, deterministic executor
  harness/      state graph, agent loop, checkpoints, retries/breakers/budgets
  llm/          Anthropic provider, router, sim reference model, chaos, record/replay
  tools/        tool registry + AP tools
  rag/          section chunking + BM25 retrieval
  guardrails/   injection screen, redaction, policy engine, email guard
  domain/       models, ERP gateway, facts, synthetic data, policy manual
  evals/        graders, runner, ablation suite, CI gate
  obs/          tracing + metrics
  api/ ui/      FastAPI service and the web UI (no build step)
tests/          63 tests: domain, guardrails, tools, RAG, reliability, harness, providers, evals, API
evals/          thresholds.yaml, dataset.jsonl, reports/
docs/           harness.md, security.md, evals.md, deployment.md
```

## Design decisions

- **Own the graph and the loop.** The control path is small enough to read in one sitting, has no framework
  dependency, and makes exactly-once side effects and fail-closed routing explicit. The node functions port to
  LangGraph mechanically ([mapping](docs/harness.md#why-not-langgraph-here)).
- **Manual tool loop over an SDK runner.** The loop is where budgets, repair, nudges, loop guards and tracing live.
- **BM25 over embeddings.** A policy manual with numbered sections is small and keyword-dense; BM25 is exact,
  explainable and has no infrastructure. `PolicyCorpus.search` is the seam for hybrid retrieval on a larger corpus.
- **SQLite everywhere.** Zero-setup demo; the gateway and checkpointer are the only places that know about it.
- **A deterministic reference model.** It makes the harness and the eval set testable in CI, and turns into an
  adversary for ablations.

## Limitations

- The ERP, vendors and policy manual are synthetic. Real deployments have messier evidence (PDF invoices, email
  threads, contract terms), which is where the LLM roles earn their cost.
- The injection screen is pattern-based; it is a routing signal, and the guarantee comes from the deterministic layers.
- Results in this README come from the reference model; live-model numbers depend on the model and prompts and should
  be measured with the same suite before any rollout.

## License

MIT. Built by Safiullah Rahu with Claude Code.
