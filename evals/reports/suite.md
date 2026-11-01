# Eval suite (sim reference policy)

| Suite | Runs | pass@1 | pass^3 | Safety violations | Unsafe proposals blocked | Fail-safe | First-pass action | Faults injected | Retries |
|---|---|---|---|---|---|---|---|---|---|
| reference | 156 | 100.0% | 100.0% | 0 | 0 | 0 | 100.0% | 0 | 0 |
| chaos-controls-on | 156 | 99.4% | 98.1% | 0 | 0 | 0 | 100.0% | 468 | 267 |
| chaos-controls-off | 156 | 18.6% | 1.9% | 0 | 0 | 126 | 19.2% | 198 | 0 |
| faulty-model | 156 | 97.4% | 92.3% | 0 | 63 | 0 | 50.0% | 0 | 0 |
