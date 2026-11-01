# Deployment

Matchpoint ships as one container (`Dockerfile`): FastAPI API + web UI + run workers, with configuration from
environment variables and a YAML file (`MATCHPOINT_CONFIG`). Nothing is cloud-specific.

```bash
docker compose up --build                       # http://localhost:8400, offline reference model
ANTHROPIC_API_KEY=... docker compose up         # then switch the provider in the UI, or set --provider anthropic
docker compose --profile tracing up             # adds Jaeger; set OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318
```

## Mapping to a client cloud

| Need | AWS | Azure | GCP |
|---|---|---|---|
| Run the container | ECS Fargate or App Runner | Container Apps | Cloud Run |
| Claude | Claude API, Claude Platform on AWS, or Bedrock | Claude API or Microsoft Foundry | Claude API or Vertex AI |
| Run queue (at scale) | SQS | Service Bus | Pub/Sub |
| Checkpoints, traces, approvals | RDS Postgres | Azure Database for PostgreSQL | Cloud SQL |
| Secrets | Secrets Manager | Key Vault | Secret Manager |
| Traces (OTLP) | ADOT collector -> X-Ray / CloudWatch | Azure Monitor (OTLP) | Cloud Trace |
| Metrics (`/metrics`) | Managed Prometheus | Managed Prometheus | Managed Prometheus |
| SSO for approvers | Cognito / IAM Identity Center | Entra ID | Identity Platform |

The provider layer is the only part that changes between Claude API, Bedrock, Vertex and Foundry: the Anthropic SDK has
a dedicated client for each (`AnthropicBedrockMantle`, `AnthropicVertex`, `AnthropicFoundry`) with the same
`messages.create` surface, so `AnthropicProvider` takes the client as a constructor argument.

## What changes for production (not implemented here)

- **Storage.** SQLite is the stand-in for both the ERP and the app database. The ERP side becomes a gateway to the
  client's system (SAP OData, Oracle Fusion REST, NetSuite SuiteTalk, Dynamics 365) behind the same read/write split;
  the executor stays the only writer. The app database moves to Postgres.
- **Workers.** The API currently runs workflows in a thread pool. At volume, the API enqueues run ids and stateless
  workers pull them; checkpoints already make any worker able to resume any run.
- **Identity.** Replace the shared bearer token with OIDC; record the approver's identity from the token.
- **Change control.** Prompts, tool schemas, policy thresholds and model versions are configuration. Changes go through
  the eval gate in CI (`evals/thresholds.yaml`) before they reach production, and a cassette of live traffic gives a
  zero-cost regression run.
- **Data handling.** Keep the tool-boundary redaction; review data residency for the chosen Claude platform
  (`inference_geo` on the Claude API, regional endpoints on Bedrock/Vertex).

## Operating it

- `GET /api/health` for load balancers; `GET /metrics` for Prometheus (LLM calls, cost, latency histograms, retries,
  fallbacks, tool errors, guardrail violations, fail-safe count, resolutions by action).
- Alert on: fail-safe rate, guardrail violation rate, unsafe proposals per hour, p95 latency, cost per run,
  breaker-open events.
- Every run has a trace id equal to its run id, across interrupts and resumes.
