# Offline Rescue Triage: hackathon plan

**VERSUS "Master of One"** · a 24-hour specialist AI · plan dated 2026-11-07

> When the network dies, the specialist doesn't.

A small fine-tuned model reads messy rescue requests ("trapped on roof, 3 kids, water rising, near the Shell on 5th") on a field laptop with no internet. A deterministic decision engine written in **Mercury** ranks them, merges duplicates, and flags anything it can't safely decide. When a connection appears, everything syncs to a cloud system that shows the whole fleet of laptops, the records, and the model's evals.

## Files

| File | What's in it |
| --- | --- |
| `01-concept.md` | The user, the problem, why a specialist beats Opus 5.5 here, how it maps to the judging criteria |
| `02-architecture.md` | Every component: field laptop, sync, cloud, Kafka, versioning, cloud UI, hosted demo, model intake from Hugging Face |
| `03-data-and-interfaces.md` | Event schema, SLM output contract, Mercury engine interface, API endpoints, Kafka topics |
| `04-decision-rubric.md` | The priority rubric: the Mercury engine's spec and the human labeling guide at the same time |
| `05-evals.md` | Test sets, metrics, baselines, the promotion gate, sealed holdout, what the evals panel shows |
| `06-build-plan.md` | Hour-by-hour 24h schedule, workstreams, cut lines if you fall behind |
| `07-demo-script.md` | The 3-minute judge demo, and how to handle the unseen challenge |
| `08-risks-and-open-questions.md` | What could break and what's still undecided |
| `diagram/architecture.png` | The architecture diagram |
| `diagram/architecture.mmd` | Mermaid source for the diagram (edit and re-render) |
| `diagram/architecture.html` | Self-rendering HTML version of the diagram |

## The one-paragraph pitch

During floods, volunteer rescue crews and coordinators drown in thousands of messy rescue requests, often with cell networks down. Frontier AI can't help: it's unreachable offline, it gives different answers on reruns, and its confidence isn't calibrated. Our specialist runs entirely on a laptop. A small model only *reads* (who, where, how many, what hazards, how sure it is). A Mercury rules engine *decides*, deterministically, with a compiler-checked guarantee that every request gets exactly one outcome. When it isn't sure, it says so and writes the follow-up question to text back. We prove it on evals that matter to a coordinator: urgent requests missed per 1,000, top-20 accuracy, calibration, consistency, and the offline column where a general AI scores zero.
