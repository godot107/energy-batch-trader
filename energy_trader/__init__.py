"""Energy Batch Trader — framework-agnostic EOD energy trading pipeline.

The strategy logic lives here, fully decoupled from any orchestrator so the
*same* ``run_pipeline()`` runs three ways with no logic changes:

  - CLI (local, now):  ``python -m energy_trader --dry-run``
  - Airflow:           a thin DAG task that calls ``run_pipeline()``
  - Azure Functions:   a Timer-triggered function that calls ``run_pipeline()``

Execution is pluggable (see ``energy_trader.brokers``): a ``DryRunBroker`` for
Phase 1 validation and a ``RobinhoodMCPBroker`` targeting Robinhood's official
Agentic Trading MCP (``https://agent.robinhood.com/mcp/trading``) for Phase 2.
"""

from energy_trader.brokers.base import Order
from energy_trader.pipeline import PipelineResult, run_pipeline

__all__ = ["Order", "PipelineResult", "run_pipeline"]
