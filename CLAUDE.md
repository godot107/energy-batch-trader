# Energy Batch Trader

EOD energy trading pipeline (USO/XLE). Strategy logic is decoupled from any
orchestrator; execution targets Robinhood's **official Agentic Trading MCP**.

## Run / build

```bash
source .venv/bin/activate              # project-local venv (core deps only)
python -m energy_trader                 # dry-run (default, safe)
python -m energy_trader --paper         # Phase 2: Alpaca paper trading (no real money)
python -m energy_trader --backtest      # backtest the strategy over history
python -m energy_trader --backtest --strategy pairs --asset USO --asset XLE  # pairs
python -m energy_trader --backtest --strategy carry --asset USO  # carry vs SMA vs B&H (needs EIA_API_KEY)
python -m energy_trader --backtest --strategy carry --asset USO --plot  # + save equity/drawdown/regime PNGs to plots/
python -m energy_trader --backtest --strategy voltgt --asset USO --plot  # SMA before/after vol-targeted sizing (Kaufman ch.23)
python -m energy_trader --live          # Phase 3: arms real orders; needs ROBINHOOD_MCP_TOKEN
.venv/bin/python -m py_compile energy_trader/*.py energy_trader/brokers/*.py dags/*.py
jupyter lab notebooks/research.ipynb    # interactive research narrative (reproduces all findings + charts)
```

Core runtime needs only `pandas numpy requests python-dotenv`. The full
`requirements.txt` adds Airflow, vectorbt, Alpaca, LLM SDKs, and the research-only
viz stack (matplotlib, jupyterlab — see `plots.py` / `notebooks/`).

## Key decisions / constraints

- **`run_pipeline()` in `energy_trader/pipeline.py` is THE entrypoint.** Keep all
  logic here, orchestrator-agnostic. CLI, the thin Airflow DAG, and a future
  Azure Function all just call it — never duplicate logic into an orchestrator.
- **Execution = official Robinhood MCP** (`agent.robinhood.com/mcp/trading`),
  driven *deterministically* (no LLM in the trade decision). `robin_stocks` is
  legacy/read-only. Trades land only in an isolated, small-funded Agentic account.
- **The anomaly gate (`anomaly.py`) is the only place an LLM may run** — a risk
  gate, not a trade decider. Today it runs a *deterministic* EIA inventory check
  (`eia.py`); the LLM/geopolitical signal is a planned add. The SMA-crossover
  signal (`strategy.py`) is plain pandas and deterministic. The built-in
  `--backtest` harness (`backtest.py`) reuses that same signal; vectorbt stays
  optional for offline parameter sweeps.
- **Live position sizing = percent-of-equity × volatility targeting** (Kaufman
  ch.23; `strategy._size_entry`, `sizing.py`): base = `risk_fraction × account_equity`
  (read live via `Broker.equity()`, else the fixed `default_notional`), then ×
  `vol_target_annual / realized_vol` (capped at `vol_max_leverage`; toggle
  `vol_target_live`). Entry sizing only — held positions aren't rebalanced daily as
  the backtest is; full rebalancing needs position-aware brokers (`TODO(rebalance)`).
  Full reasoning + references in RUNBOOK "Position sizing".
- **Cadence: once per trading day, evening ET.** The DAG fires `0 18` in
  `America/New_York` (6 PM ET, ~2h after the 4 PM close, final daily bars in). The
  tz-aware `start_date` is load-bearing — a naive datetime would mean 18:00 UTC
  (pre-close). Not holiday-aware yet; the pipeline degrades to "hold" on a stale bar.
- **The deployed scheduler is GitHub Actions** (`.github/workflows/eod-paper.yml`),
  not Airflow — always-on, zero infra. Cron `0 22 * * 1-5` (UTC; = 6 PM EDT / 5 PM
  EST, both post-close — GH cron has no DST). Installs `requirements-runtime.txt`
  (lean: no Airflow/viz), runs `--paper`, keys via repo secrets. `workflow_dispatch`
  allows manual runs. The DAG/CLI/Actions all just call the same `run_pipeline()`.
- **Research tooling is read-only and separate from live.** `--strategy pairs`
  (`pairs.py`) is a market-neutral spread strategy with a `statsmodels`
  cointegration gate (lazy import) + pair sweep; `roll.py` reports USO's
  roll-decay vs WTI spot (EIA); `--strategy carry` (`carry.py`) turns that
  roll-yield into a signal and benchmarks it against SMA + buy-and-hold. All are
  analysis only — they never place orders. (Backtests use split/dividend-adjusted
  Alpaca bars — `Adjustment.ALL` in `data.py`; raw bars corrupt USO across its
  2020 reverse split.) **Visualization is research-only too:** `plots.py` (lazy
  matplotlib, headless PNGs via `--plot`) and `notebooks/research.ipynb` reuse the
  same `BacktestResult.equity` curves — never imported by the live pipeline.
- **Brokers are pluggable** (`brokers/`): `DryRunBroker` (Phase 1),
  `AlpacaPaperBroker` (Phase 2, paper — no real money), and `RobinhoodMCPBroker`
  (Phase 3, live). Default everything to **dry-run**; `--paper` selects Alpaca
  paper, `--live` (or `EOD_BROKER=robinhood`) is the single opt-in to real money.
- **`RobinhoodMCPBroker._order_args` argument schema is unverified** against the
  live server (`TODO(live)`) — confirm via `tools/list` before the first order.
- Real money. Prefer dry-run; keep the funded Agentic balance small.
