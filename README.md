# Energy Batch Trader

An End-of-Day (EOD) energy trading pipeline. The strategy logic is **decoupled
from any orchestrator**, so the same `run_pipeline()` runs from the CLI today and
drops into Azure Functions later with no logic changes. Order execution targets
Robinhood's **official Agentic Trading MCP**.

## How it works

A single pass: `extract → anomaly gate → analyze → execute`.

1. **Extract** EOD bars for the energy universe (USO/XLE) via Alpaca (synthetic
   fallback if keys are absent).
2. **Anomaly gate** — an LLM scans for geopolitical/EIA shocks and *halts* the
   run if risk is high. This is the only place an LLM runs.
3. **Analyze** — a deterministic SMA-crossover signal produces intended orders.
   No LLM in the trade decision.
4. **Execute** — orders go to a pluggable broker (dry-run, or the Robinhood MCP),
   then a Telegram alert is sent.

## Execution: Robinhood official Agentic Trading MCP

Execution targets Robinhood's sanctioned **Agentic Trading** MCP at
`https://agent.robinhood.com/mcp/trading` (launched May 2026) — *not* the
unofficial `robin_stocks` private API. Why it's the right fit here:

- **Sanctioned + OAuth.** You approve access in the Robinhood app; no password
  is stored. (Legacy `robin_stocks` is retained only for read-only introspection.)
- **Contained blast radius.** Trades execute only in an isolated, separately
  funded **Agentic account**; every other account stays read-only.
- **Equities/ETFs only (beta).** USO/XLE are ETFs, so this limit doesn't bite.
- **Deterministic.** We call the MCP's `review_equity_order` / `place_equity_order`
  tools directly with strategy-computed params — the LLM never places trades.

## Phased rollout

| Phase | What | Risk |
|------|------|------|
| **1 — now (local, manual)** | `python -m energy_trader` in **dry-run**: compute signals, print intended orders, Telegram alert. You execute by hand. | none |
| **2 — live** | Add `ROBINHOOD_MCP_TOKEN`, fund a *small* balance in the Agentic account, run `--live`. | small, contained |
| **3 — Azure** | Timer-triggered Azure Function calls the same `run_pipeline()`; OAuth refresh token in Key Vault. | automated |

## Quick start (Phase 1)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install pandas numpy requests python-dotenv   # core runtime only
cp .env.example .env                              # optional: fill in keys

python -m energy_trader                # dry-run, default USO/XLE universe
python -m energy_trader -v             # debug logging
python -m energy_trader --asset USO    # custom universe
python -m energy_trader --live         # Phase 2: arms real orders (needs token)
```

Full deps (Airflow, vectorbt, Alpaca, LLMs): `pip install -r requirements.txt`.

## Repository structure

```
energy_trader/            # framework-agnostic pipeline (the actual logic)
  pipeline.py             #   run_pipeline() — the orchestrator-agnostic entrypoint
  config.py  data.py      #   settings; Alpaca extract (+ synthetic fallback)
  anomaly.py  strategy.py #   LLM risk gate; deterministic SMA-crossover signal
  notify.py               #   Telegram alerts
  brokers/                #   pluggable execution
    dry_run.py            #     logs intended orders (Phase 1 default)
    robinhood_mcp.py      #     official Robinhood Agentic Trading MCP (Phase 2)
  __main__.py             #   CLI: python -m energy_trader
dags/energy_eod_dag.py    # thin Airflow DAG -> calls run_pipeline()
plugins/                  # Airflow plugins (notifications shim; legacy RH hook)
```

See `RUNBOOK.md` for operations, the MCP connector flow, and Azure deployment.
