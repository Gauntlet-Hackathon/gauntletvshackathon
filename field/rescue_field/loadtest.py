"""Surge load test: `python -m rescue_field.loadtest --count 2000 [--batch 200] [--data-dir DIR]
[--cloud-url URL] [--seed 7]`.

Generates messy requests with the seed templates, sends them through `process_batch` with the
real engine client (`ENGINE_CMD`, default the fallback engine), then, with `--cloud-url`, drains
the outbox with `SyncWorker.sync_once` until empty. Prints one JSON line with the timings.
Against the compose stack: `python -m rescue_field.loadtest --count 2000 --cloud-url http://localhost:4000`.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Sequence

import httpx

from rescue_field import api, seed, sync
from rescue_field.pipeline import PipelineConfig, process_batch

MAX_SYNC_ROUNDS = 1000


def _per_1000(seconds: float, count: int) -> float | None:
    return round(seconds / count * 1000, 3) if count else None


def run(
    count: int,
    *,
    batch: int = 200,
    data_dir: str | None = None,
    cloud_url: str | None = None,
    seed_value: int = 7,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    env = {"FIELD_DATA_DIR": data_dir} if data_dir else {"FIELD_DATA_DIR": tempfile.mkdtemp()}
    store = api.open_store(env)
    try:
        items = seed.generate(count, seed_value)
        decide = api.default_decide_batch()
        config = PipelineConfig.from_env()
        before = store.outbox_count()
        tiers: Counter[str] = Counter()
        start = time.perf_counter()
        for i in range(0, len(items), batch):
            for events in process_batch(
                items[i : i + batch], store=store, decide_batch=decide, config=config
            ):
                out = events[-1]["payload"]["engine_output"]
                tiers[out["priority_tier"] or out["outcome"]] += 1
        intake_s = time.perf_counter() - start

        sync_s = None
        if cloud_url:
            worker = sync.worker_from_env(
                store, cloud_url, model_version="stub:loadtest", transport=transport
            )
            worker.down_sync = False
            start = time.perf_counter()
            for _ in range(MAX_SYNC_ROUNDS):
                worker.sync_once()
                if store.outbox_count() == 0:
                    break
            sync_s = time.perf_counter() - start
        return {
            "count": count,
            "intake_seconds": round(intake_s, 3),
            "seconds_per_1000_intake": _per_1000(intake_s, count),
            "sync_seconds": None if sync_s is None else round(sync_s, 3),
            "seconds_per_1000_sync": None if sync_s is None else _per_1000(sync_s, count),
            "outbox_left": store.outbox_count(),
            "outbox_before": before,
            "tiers": dict(tiers),
        }
    finally:
        store.close()


def main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rescue_field.loadtest", description=__doc__)
    parser.add_argument("--count", type=int, default=2000)
    parser.add_argument("--batch", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--data-dir", help="directory for field.db (default: a temp dir)")
    parser.add_argument("--cloud-url", help="drain the outbox to this cloud when given")
    args = parser.parse_args(argv)
    if args.count < 0 or args.batch < 1:
        parser.error("--count must be >= 0 and --batch >= 1")
    result = run(
        args.count,
        batch=args.batch,
        data_dir=args.data_dir,
        cloud_url=args.cloud_url,
        seed_value=args.seed,
        transport=transport,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
