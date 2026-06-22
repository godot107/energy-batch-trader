"""Thin Airflow DAG — orchestration only.

All strategy logic lives in the framework-agnostic ``energy_trader`` package so
this DAG stays a scheduler wrapper. The identical ``run_pipeline()`` also runs
from the CLI (``python -m energy_trader``) and will drop into an Azure Functions
Timer trigger unchanged. Keep this file boring on purpose.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

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
    schedule_interval="0 18 * * 1-5",  # 6 PM, Mon–Fri
    start_date=datetime(2023, 1, 1),
    catchup=False,
    tags=["trading", "energy"],
) as dag:
    PythonOperator(task_id="run_eod_pipeline", python_callable=_run_eod)
