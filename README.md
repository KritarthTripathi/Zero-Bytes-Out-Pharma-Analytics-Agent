# Zero-Bytes-Out Pharma Analytics Agent

Local-first, air-gapped multi-agent text-to-SQL pipeline for synthetic pharma commercial analytics (prescriptions, rep activity, payer access). It runs open-weight models through Ollama, orchestrates stateful agents with LangGraph, queries DuckDB, masks PII with Presidio, and traces every run with self-hosted Arize Phoenix, with zero external LLM API calls.

Developed on synthetic data and staged on an Azure VM, designed to move to an air-gapped on-prem machine unchanged.

**Status:** ongoing | **Data:** 100% synthetic | **Stack:** LangGraph · Ollama · DuckDB · Presidio · Arize Phoenix · Docker

The branch feature-initial-unscoped consists of a simpler architecture base on which the project was initiated and later moved to a heirarchial multi-agent hybrid routing system for scoped access control and better latency and acuracy when dealing with multiple datasets.

```
input → ingest_mask (parse + PII mask + compliance) ──blocked──► END
              │ pass
              ├─ protocol agent (section-aware chunking, Pydantic structured output)
              └─ sales agent (LLM writes SQL → read-only DuckDB)
                        ↓
                    validator (grounding / SQL checks) ──retry (bounded)──► specialist
                        ↓
                      output (optional local re-identification)
```

## Design decisions (initial)
- **Raw text never enters graph state.** Masking happens inside the ingest node, so checkpoints and Phoenix traces only ever hold masked content. The placeholder→original map lives in an encrypted vault (Fernet key via docker secret), outside state.
- **Deterministic PII first** (Presidio + spaCy + custom MRN/subject-ID/DOB/phone recognizers), with a residual re-scan as the compliance gate. To Do: Add an LLM/GLiNER contextual pass as a second layer.
- **Enforced network isolation:** `internal: true` Docker network, plus `scripts/egress_test.sh` that fails if anything can reach the internet.
- **LLM is a config value** (`src/app/llm.py`); no cloud SDKs in app code; secrets behind an interface.
- **Model-generated SQL is constrained:** single SELECT only, keyword denylist, DuckDB external access disabled. (Production hardening: run in a network-less sandbox container.)
- **Measured masking recall** against planted ground truth (`scripts/measure_recall.py`).

## Quick start (connected machine / Azure staging phase)
```bash
sh scripts/gen_key.sh                                   # PII vault key (docker secret)
docker compose build
docker compose --profile setup run --rm ollama-setup   # one-time model pull (only job with internet)
docker compose up -d                                    # add -f docker-compose.gpu.yml on a GPU host
python3 scripts/generate_synthetic.py                   # synthetic protocol + sales data

docker compose run --rm app python -m app.main data/synthetic/protocol_001.txt
docker compose run --rm app python -m app.main data/synthetic/sales_001.csv --question "Revenue by region"
docker compose run --rm app python scripts/measure_recall.py
docker compose run --rm app pytest -q tests
sh scripts/egress_test.sh                               # must print OK
```
Phoenix UI: http://localhost:6006 (on Azure, via SSH tunnel through Bastion).

## Azure → on-prem
1. `infra/azure/provision.sh` (illustrative; review first). Use synthetic data only.
2. `scripts/bundle_offline.sh`, then rebuild from the bundle with the network off.
3. On-prem: install Docker + NVIDIA Container Toolkit, load the bundle, run `egress_test.sh`, capture `tcpdump` during a full run, keep the evidence.

## Known gaps / next steps / TO DO items
- Add a GLiNER or LLM contextual-PII layer and a config-driven HIPAA/GDPR policy file.
- Replace the compliance `blocked` edge with a LangGraph `interrupt()` for review.
- Phoenix Evals with a local judge model (hallucination, extraction accuracy).
- Verify the Phoenix telemetry-disable variable and Azure extension names against current docs.
- Scale test with distractor tables: the evidence for scoped agents at 1,500+ datasets. 
- IQ/OQ/PQ, risk table and traceability matrix, generated automatically from the tests and evals.
- E-signatures on report approval, plus SOP and change-control templates.
- Azure benchmark (CPU versus GPU speed) and a Databricks port design, with Unity Catalog grants, model serving and monitoring.

## First metrics 
 - Resulted in planted=142 leaked=57 recall=0.599 which was corrected to recall 1.000 after masking the mail Ids
<img width="960" height="448" alt="trace-2-phoenix-10242026-0442" src="https://github.com/user-attachments/assets/095ad96d-1dcb-4605-b9bd-f9f7ef622537" />

<img width="960" height="513" alt="trace-2_1-phoenix-10242026-0442" src="https://github.com/user-attachments/assets/b9820b71-e56b-4aab-be2c-08a064c672a6" />

## Revised metrics 
Run	                       |               Accuracy	                     |                   Set                       |
-------------------------  |  -----------------------------------------  |  -----------------------------------------  |
Original prompt	           |                 14%	                     |                 Baseline                    |
First fix	               |                 21%	                     |                 Dev set                     |
Dynamic rules	           |    50% (general) / 57% (code model)	     |                 Dev set                     |
Rules v3	               |          79% dev / 80% hold-out 1	         |        Hold-out 1 was fairly independent    |

After rules v4 fix, a new holdout2 set was added.
57% before prompt engineering, ~80% on an independent hold-out, 93% on the dev set after rule-based fixes, 100% on a second small hold-out (n=10).


## Revised structure for scoped agentic routing

The routing was enhanced to scoped agents to reduce the load of multiple datasets on a single agent. 
Initially it is assumed the metrics would drop sligtly, but for geo-partitioned, domain specific querying it would prove to be useful when the amount of datasets concerned would increase with similar domain specific agent-moded required for different domains having completely different schema.

the enhanced workflow is given below:

```
question
  -> mask names/emails (common code, not an agent)
  -> SUPERVISOR agent       decides: clinical | sales | out_of_scope
       |- CLINICAL agent     (existing protocol pipeline)
       '- SALES router agent decides: geo | hcp | activity
            |- sales_geo       tables: territory_alignment_hierarchy, rx_report_summary_geo
            |- sales_hcp       tables: hcp_summary_with_territory_and_tm_info, hcp_rx_summary_report, territory_alignment_hierarchy
            '- sales_activity tables: territory_alignment_hierarchy, hcp_summary_with_territory_and_tm_info, hcp_activity_calls_vae_suggestions
```


On evelauting the enhanced structure the following metrics are observed:

|                      Measure                      |                      Result                       |
| ------------------------------------------------- | ------------------------------------------------- |
| Routing, rules only (hold-out)                    | supervisor 24/25, sales router 17/18              |
| Routing, hybrid (hold-out)                        | supervisor 24/25, sales router 16/18              |
| SQL, each question straight to its own agent      | 95% (21/22). The one miss is d15 (activity)       |
| SQL, one agent with all tables (--single)         | 91% (20/22)                                       |
| SQL, full hierarchy (--e2e)                       | 91% (20/22), routing correct 21/22                |
| SQL, planner only, no LLM (PROMPT_MODE=template)  | 100% (22/22)                                      |   

