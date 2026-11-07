# 08 · Risks and open questions

## Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Mercury toolchain or Python glue eats hours | Engine not ready | Python fallback behind the same JSON interface from hour one; parity tests |
| Public datasets unusable (license, access, format) | Weaker "real data" claim | Check in hour 0; lean on golden + hard sets; say so honestly |
| Circular labels (golden labels produced by the engine) | Evals prove nothing | Humans label from raw text using the rubric; sealed holdout written by a non-builder |
| SLM not ready in time | No real extraction | Stub output for UI work; integrate the HF model by hour 9–12; cut a second iteration if needed |
| Opus beats you on several rows | Weak "prove the advantage" | Lead with structural rows (offline, consistency, calibration, tunable P1 recall); be honest about the rows you lose |
| Managed Kafka setup friction | Cloud slips | Postgres event table with the same schema as a fallback |
| Live demo fails (network, cold start) | Bad first impression | Pre-warm hosted instances; recorded backup video; seeded data |
| Rubric seen as made up | Trust question | Label it "draft, to be validated with emergency managers"; cite sources if any are found |

## Open questions

1. **Hugging Face model:** repo name, model type (encoder tagger vs generative), output format.
2. **Demo city:** Houston (Harvey-style, multilingual) or another? Determines the street/landmark list and language mix.
3. **Rubric sources:** is there public triage guidance (FEMA, Coast Guard, volunteer groups) to anchor tiers to? Not verified yet.
4. **Kafka provider and free-tier limits:** Confluent Cloud vs Redpanda Serverless.
5. **Team split:** who owns which workstream?
6. **Stale/offline thresholds** for the fleet panel (proposed 5 and 30 minutes).
7. **Down-sync scope** for the demo: include other sites' assigned/rescued events or cut?

## Not verified yet (flagged during research)

- HumAID / CrisisNLP availability and license.
- Any existing products doing offline rescue-request triage (a quick competitor check is worth doing before the pitch).
