"""Thin Airflow DAG — orchestration only.

All strategy logic lives in the framework-agnostic ``energy_trader`` package so
this DAG stays a scheduler wrapper. The identical ``run_pipeline()`` also runs
from the CLI (``python -m energy_trader``) and will drop into an Azure Functions
Timer trigger unchanged. Keep this file boring on purpose.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator

# Make the repo-root ``energy_trader`` package importable from the dags folder.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

default_args = {
    "owner": "williemaize",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def _run_eod(**_):
    # Imported inside the callable so DAG *parsing* never needs the trading deps.
    from energy_trader import run_pipeline

    # Live trading is opt-in: set Airflow Variable EOD_DRY_RUN="false" to arm it.
    from airflow.models import Variable

    dry_run = Variable.get("EOD_DRY_RUN", default_var="true").lower() != "false"
    result = run_pipeline(dry_run=dry_run)
    return result.summary()


with DAG(
    "energy_eod_strategy",
    default_args=default_args,
    description="Daily EOD energy strategy — extract, anomaly gate, analyze, execute",
    # 6 PM America/New_York, Mon–Fri — i.e. ~2h after the 4 PM ET equity close, so
    # the final daily bars are in. The tz-aware start_date is what makes "18" mean
    # 6 PM Eastern (a naive datetime would be 18:00 UTC ≈ 1–2 PM ET, pre-close).
    # NOTE: this runs every weekday incl. market holidays; the pipeline degrades to
    # "hold" on a stale/absent bar, so it's safe but not holiday-aware (TODO).
    schedule_interval="0 18 * * 1-5",
    start_date=pendulum.datetime(2023, 1, 1, tz="America/New_York"),
    catchup=False,
    tags=["trading", "energy"],
) as dag:
    PythonOperator(task_id="run_eod_pipeline", python_callable=_run_eod)
