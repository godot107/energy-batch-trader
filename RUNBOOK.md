# Energy Batch Trader — Operations Runbook

Day-to-day operations, the Robinhood MCP connector flow, and the Azure path.

## 1. Run it locally (Phase 1 — dry-run)

```bash
source .venv/bin/activate
python -m energy_trader -v
```

Dry-run computes signals, prints the orders it *would* place, and (if Telegram is
configured) sends an alert. Nothing is sent to a broker. This is the safe loop
for validating the strategy before any real money is involved.

## 2. Backtesting

**Built-in harness (default).** Replays the *live* signal over history:

```bash
python -m energy_trader --backtest                 # 3y, USO/XLE
python -m energy_trader --backtest --years 5 --asset USO
```

It reuses `strategy.crossover_series()` — the same rule the live job runs — so the
backtest can't drift from production. Reports total return, CAGR, Sharpe, max
drawdown, trade count, and win rate vs. buy-and-hold. numpy/pandas only.

> Backtests need **real bars** to mean anything: set `ALPACA_API_KEY` /
> `ALPACA_SECRET_KEY`. Without them the run uses synthetic data (proves the
> harness works, tells you nothing about the strategy).

**VectorBT (optional, research only).** For fast `fast_window`/`slow_window`
parameter sweeps, prototype in a notebook *outside* `dags/`: pull Alpaca history,
run `vbt.Portfolio.from_signals()`, then set the winning windows in
`energy_trader/config.py`. Not a runtime dependency of the daily job.

## 3. Telegram notifications

1. Message `@BotFather` → `/newbot` → copy the HTTP API token → `TELEGRAM_BOT_TOKEN`.
2. Send your bot `/start`, then hit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat.id` →
   `TELEGRAM_CHAT_ID`.

Unset = notifications are silently skipped.

## 4. Going live — Robinhood Agentic Trading MCP (Phase 2)

Execution uses Robinhood's **official** MCP (`agent.robinhood.com/mcp/trading`),
not `robin_stocks`. The old SMS/TOTP MFA headache is gone — auth is OAuth.

1. **Open an Agentic account.** In the Robinhood app, start the Agentic Trading
   connector flow (desktop). Trades execute *only* in this isolated account.
2. **Fund it small.** Move in a deliberately small balance — that balance is the
   entire blast radius.
3. **Connect + get a token.** Approve the connector in the Robinhood app; export
   the issued token as `ROBINHOOD_MCP_TOKEN` in `.env`.
4. **Confirm the tool schema.** Before the first live order, list the server's
   tools and confirm the `place_equity_order` argument names, then adjust
   `RobinhoodMCPBroker._order_args` if they differ (it's flagged `TODO(live)`).
5. **Arm it.** `python -m energy_trader --live` (or set Airflow Variable
   `EOD_DRY_RUN=false`). Start with one asset and a tiny `default_notional`.

> Interactive sanity check: connect the same MCP to Claude Code / Claude Desktop
> and ask it to read your Agentic account — a quick way to confirm OAuth works
> before automating.

## 5. Deployment options

**Airflow (kept as an option).** `export AIRFLOW_HOME=$(pwd)` so the `dags/`
folder is found; the DAG is a thin wrapper that calls `run_pipeline()`. Heavy for
one daily job — fine if you already run Airflow.

**Azure Functions (Phase 3 target).** A Timer-triggered function is the cheap
serverless fit. The function body is essentially:

```python
import azure.functions as func
from energy_trader import run_pipeline

def main(timer: func.TimerRequest) -> None:   # CRON e.g. "0 0 22 * * 1-5" (UTC)
    run_pipeline(dry_run=False)
```

Notes for the serverless move:
- **Secrets in Key Vault**, not `.env` — especially the OAuth refresh token.
  Functions are stateless/ephemeral, so do *not* rely on a local token cache.
- **Schedule in UTC.** 6 PM ET ≈ `22:00`/`23:00` UTC depending on DST.
- Use **Durable Functions** only if you later want per-step retries/fan-out;
  for a single daily pass a plain Timer trigger is enough.

## 6. Troubleshooting

- **`RobinhoodMCPNotConfigured`** — `ROBINHOOD_MCP_TOKEN` isn't set; you ran
  `--live` without finishing the connector flow. Use dry-run until it's set.
- **DAG not appearing** — `AIRFLOW_HOME` must point at the repo root (where
  `dags/` lives). The DAG adds the repo root to `sys.path` to import the package.
- **All HOLD every run** — expected with synthetic data (no Alpaca keys). Add
  Alpaca keys for real bars, or tune the SMA windows in `config.py`.
- **MCP order rejected** — likely a tool-argument mismatch; re-check step 4.4
  against the live `tools/list` schema.
