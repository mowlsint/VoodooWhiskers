#!/usr/bin/env python3
"""One bounded job: two Open Waters streams plus the existing regional snapshots."""
import asyncio
import json
import os

import fetch_openwaters as ow
import fetch_regional_contacts as regional


async def run():
    cfg = json.loads(ow.CONFIG.read_text())
    ow.validate_config(cfg)
    token = os.environ.get("OPENWATERS_TOKEN", "").strip()
    # The regional worker finishes filtering/state updates before OW publishes its results.
    if token:
        results, _ = await asyncio.gather(ow.collect(cfg, token), asyncio.to_thread(regional.main))
        ow.publish(results)
    else:
        regional.atomic_json_write(ow.STATUS, {"generated_at": regional.utc_now_iso(),
            "ok": False, "error": "OPENWATERS_TOKEN missing", "live_test_performed": False})
        await asyncio.to_thread(regional.main)
    # The existing broker decides whether valid bounded LKG remains usable.
    from merge_ais_contacts import main as merge
    return merge()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
