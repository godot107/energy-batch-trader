# Energy Batch Trader

EOD energy trading pipeline (USO/XLE). Strategy logic is decoupled from any
orchestrator; execution targets Robinhood's **official Agentic Trading MCP**.

## Run / build

```bash
source .venv/bin/activate              # project-local venv (core deps only)
python -m energy_trader                 # dry-run (default, safe)
python -m energy_trader --live          # arms real orders; needs ROBINHOOD_MCP_TOKEN
.venv/bin/python -m py_compile energy_trader/*.py energy_trader/brokers/*.py dags/*.py
```

Core runtime needs only `pandas numpy requests python-dotenv`. The full
`requirements.txt` adds Airflow, vectorbt, Alpaca, and LLM SDKs.

## Key decisions / constraints

- **`run_pipeline()` in `energy_trader/pipeline.py` is THE entrypoint.** Keep all
  logic here, orchestrator-agnostic. CLI, the thin Airflow DAG, and a future
  Azure Function all just call it — never duplicate logic into an orchestrator.
- **Execution = official Robinhood MCP** (`agent.robinhood.com/mcp/trading`),
  driven *deterministically* (no LLM in the trade decision). `robin_stocks` is
  legacy/read-only. Trades land only in an isolated, small-funded Agentic account.
- **The only LLM step is anomaly detection** (`anomaly.py`) — a risk gate, not a
  trade decider. The SMA-crossover signal (`strategy.py`) is plain pandas and
  deterministic; vectorbt is for offline backtesting only.
- **Brokers are pluggable** (`brokers/`): `DryRunBroker` (Phase 1) and
  `RobinhoodMCPBroker` (Phase 2). Default everything to **dry-run**; `--live` /
  `EOD_DRY_RUN=false` is the single opt-in to real money.
- **`RobinhoodMCPBroker._order_args` argument schema is unverified** against the
  live server (`TODO(live)`) — confirm via `tools/list` before the first order.
- Real money. Prefer dry-run; keep the funded Agentic balance small.
