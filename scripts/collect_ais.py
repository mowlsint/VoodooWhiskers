#!/usr/bin/env python3
"""One bounded job: two Open Waters streams plus the existing regional snapshots."""
import asyncio
import json
import os

import fetch_openwaters as ow
import fetch_regional_contacts as regional
from traffic_context import TrafficContext


async def run():
    cfg = json.loads(ow.CONFIG.read_text())
    ow.validate_config(cfg)
    token = os.environ.get("OPENWATERS_TOKEN", "").strip()
    context = TrafficContext()
    # The regional worker finishes filtering/state updates before OW publishes its results.
    if token:
        results, _ = await asyncio.gather(ow.collect(cfg, token), asyncio.to_thread(regional.main, context_sink=context.add_source))
        publish_result = ow.publish(results)
        if publish_result:
            print("::warning::Open Waters collection degraded; safe partial AIS snapshots were retained where available. See data/ais_openwaters_fetch_status_latest.json.")
        usable = [(rows, status) for rows, status in results if ow.stream_publishable(rows, status)]
        if usable:
            degraded = any(not status["ok"] for _, status in usable)
            context.add_source("openwaters", [row for rows, _ in usable for row in rows],
                               partial=degraded or len(usable) < len(results))
    else:
        regional.atomic_json_write(ow.STATUS, {"generated_at": regional.utc_now_iso(),
            "ok": False, "error": "OPENWATERS_TOKEN missing", "live_test_performed": False})
        await asyncio.to_thread(regional.main, context_sink=context.add_source)
    # The existing broker decides whether valid bounded LKG remains usable.
    import merge_ais_contacts as broker
    result = broker.main()
    monitored = json.loads(broker.OUTPUT_PATH.read_text()).get("contacts", []) if broker.OUTPUT_PATH.exists() else []
    context.publish(monitored)
    return result


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
