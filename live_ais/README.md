# MOwlSINT – Live AIS collector (STAGED, NOT DEPLOYED)

This optional, **read-only** component prepares Voodoo Whiskers to replace AISStream with:

- Open Waters native \`wss://ais.openwaters.io/v1/stream\`: **two** simultaneous Personal connections, with **399** and **400** subscribed square degrees respectively, 50 AIS messages/s per stream. The operator has confirmed that area and rate limits apply per connection.
- BarentsWatch Norwegian live JSONL stream, using the **existing** AIS OAuth client credentials.
- Fintraffic Digitraffic AIS live MQTT over WebSocket at \`wss://meri.digitraffic.fi:443/mqtt\`; subscribes to \`vessels-v2/#\`.

No Actions are enabled, rescheduled or triggered by this branch. No public data products are changed. No receiver data is forwarded to Open Waters. **Do not merge/start until server setup and validation are approved.**

## Quick setup on an already available server

From the VoodooWhiskers checkout root:

\`\`\`sh
cp live_ais/.env.example live_ais/.env
chmod 600 live_ais/.env
mkdir -p live_ais/state
sudo chown 10001:10001 live_ais/state
# Edit live_ais/.env locally, inserting OPENWATERS_TOKEN and optionally
# the existing BARENTSWATCH_CLIENT_ID / BARENTSWATCH_CLIENT_SECRET.
# Don't commit .env or expose token in an issue.
docker compose --env-file live_ais/.env -f live_ais/compose.yml build
# When explicitly authorized and ready:
docker compose --env-file live_ais/.env -f live_ais/compose.yml up -d
docker compose --env-file live_ais/.env -f live_ais/compose.yml logs -f --tail 100
\`\`\`

Stop:

\`\`\`sh
docker compose --env-file live_ais/.env -f live_ais/compose.yml down
\`\`\`

**Security:** no ports are published; \`live_ais/state/\` and \`live_ais/.env\` are ignored by Git. Docker daemon administrators can inspect container environment; keep host access restricted.

## Current capabilities and limits

- Existing GitHub Actions are **not** used for ingestion. The three streams are kept alive, with bounded reconnect backoff; Norwegian access tokens are refreshed by periodic reconnect.
- Data is received as fast as each upstream produces it. Static Open Waters frames carrying cached coordinates are not mislabelled as new positions. Source timestamps, origins and invalid sentinel values are retained/handled explicitly.
- SQLite WAL: single latest position per MMSI; duplicate positions at equal timestamps favour the independent Nor/Fin sources. The optional \`TRACK_MMSIS\` list stores all distinct positional fixes for named MMSIs, up to 14 days. **No historical archive for all traffic is retained by default**, protecting disk space.
- Every 30 seconds, **private unfiltered** snapshots are written under \`live_ais/state/snapshots/\` in \`ais_contacts_*_latest.json\`-like format. They are **not** published or automatically copied to the existing canonical \`data/\` folder. The snapshot builder excludes contacts last seen more than 45 minutes ago.
- **Important:** existing Voodoo Whiskers \`keep_contact\` / VOI filtering, public manifests, source licensing, and Magic Paws mirrors have *not* yet been connected to this new collector. Publishing the unfiltered snapshots directly is prohibited. The next step is controlled VOI filtering, validation and Voodoo-to-Magic mirroring, without per-message GitHub commits.
- Position timestamps are compared with a stable UTC representation; a bad source timestamp does not silently become retrieval time. No implication of worldwide or gap-free AIS coverage.
- Capacity is not yet production load-tested; monitor CPU, data volume, event drops and provider limits during a supervised pilot.
- Open Waters stream reconnect with \`snapshot:true\` rebuilds **current** state, not the intermediate messages missed during downtime.

## Checks

\`\`\`sh
python -m py_compile live_ais/collector.py
python -m unittest discover -s tests -p test_live_ais.py -v
\`\`\`

## Operator dependencies

1. Generate a personal key locally at https://openwaters.io/ais/token/ and place it only in the server's private \`.env\`.
2. Supply existing BarentsWatch OAuth credentials to the *server* if Norwegian live receiving is desired.
3. Confirm a writable server directory, Docker support and available disk before a supervised start.
4. Preserve the current Voodoo producer → Magic Paws consumer data contract; do not enable paused Actions simply to generate live AIS.
