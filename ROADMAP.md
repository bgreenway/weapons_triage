# Triage Server Roadmap

Features planned for implementation, in priority order.

---

## 1. Weapon Type Filtering

**Goal:** Only alert on firearms, not tools like hammers or crowbars.

**Why:** The model correctly identifies object types in `weapon_types` (e.g., "hammer", "crowbar" vs "firearm", "handgun"). Currently `should_alert` fires on any `visible_weapon=true` regardless of type. Our test data shows this is the only source of false positives on the labeled test set.

**Implementation:**
- Add `ALERT_WEAPON_TYPES` environment variable (default: `firearm,handgun,rifle,shotgun,pistol`)
- In `run_triage()`, after parsing, check if any item in `weapon_types` matches the alert list (case-insensitive, substring match)
- Set `should_alert = visible_weapon AND weapon_type_matches`
- `visible_weapon` stays unchanged -- it still reports what the model sees
- Add `alert_weapon_types` to `/health` response so callers know the current filter

**Files:** `api_server.py`, `docker-compose.yml` (env var), `USAGE.md`

**Verification:**
- Re-run the 26-package benchmark -- WD_Day_012 (hammer/crowbar) should now have `should_alert=false`
- Re-run the 200-cluster test -- confirm no change in firearm detections
- Test with `ALERT_WEAPON_TYPES=*` to disable filtering

---

## 2. Triage Logging

**Goal:** Persistent JSONL log of every triage request and result for auditing and accuracy tracking.

**Why:** Currently there is no record of what the system has processed. Needed for auditing, accuracy analysis over time, and debugging false positives in production.

**Implementation:**
- Add `LOG_DIR` environment variable (default: `/var/log/triage`)
- Create a `log_triage_result()` function that appends a JSON line to `{LOG_DIR}/triage_log.jsonl`
- Each line contains: timestamp (ISO 8601), event_id, camera_id, visible_weapon, weapon_types, confidence, should_alert, requires_review, inference_time_ms, images_used, error
- Call it at the end of `run_triage()` for every request (success and failure)
- Mount a host volume in docker-compose for log persistence
- Add log rotation guidance to README

**Files:** `api_server.py`, `docker-compose.yml` (volume + env var), `README.md`

**Verification:**
- Run 10 triage requests
- Verify `triage_log.jsonl` contains 10 lines with correct data
- Verify logs persist across container restart
- Verify failed requests are also logged

---

## 3. Model Switching

**Goal:** Easy switching between Gemma-4-26B, Gemma-4-31B, and Qwen3.5-35B-A3B without editing docker-compose.yml.

**Why:** All three models run on the same 4x RTX 5090 hardware. Gemma-26B is the fast MoE default, Gemma-31B is denser/slower, Qwen runs on a sister server. Need a simple way to test and compare.

**Implementation:**
- Create three env files:
  - `env.gemma-26b`: model path, served name, vLLM flags (current config)
  - `env.gemma-31b`: model path, served name, adjusted memory/seq settings if needed
  - `env.qwen-35b`: model path, served name, vLLM flags
- Parameterize `docker-compose.yml` with `${VARIABLES}` for: model path, model name, tensor parallel size, gpu memory utilization, max model len, max num seqs
- Create `switch-model.sh` script:
  - Takes model name as argument (`./switch-model.sh gemma-26b`)
  - Copies the right env file to `.env`
  - Runs `docker compose down && docker compose up -d`
  - Waits for healthcheck and reports status
- Update triage container's `MODEL_NAME` env var to match

**Files:** `docker-compose.yml`, `env.gemma-26b`, `env.gemma-31b`, `env.qwen-35b`, `switch-model.sh`, `README.md`

**Verification:**
- Switch to gemma-31b, verify model loads and triage works
- Switch back to gemma-26b, verify model loads and triage works
- Run benchmark on each model to compare accuracy and speed
- Verify Open WebUI shows the correct model name after switch

---

## 4. Prometheus Metrics

**Goal:** Expose triage-specific metrics for monitoring dashboards.

**Why:** IT team manages 20+ Triton servers with existing monitoring infrastructure. Need the triage service to fit into that ecosystem.

**Implementation:**
- Add `prometheus-fastapi-instrumentator` or `prometheus_client` to requirements
- Expose `/metrics` endpoint on the triage container with:
  - `triage_requests_total` (counter, labels: result=weapon|clean|error)
  - `triage_request_duration_seconds` (histogram)
  - `triage_images_processed_total` (counter)
  - `triage_alerts_total` (counter)
  - `triage_confidence` (histogram)
- Add to Dockerfile.triage requirements
- Document metrics in USAGE.md

**Files:** `api_server.py`, `requirements-triage.txt`, `Dockerfile.triage`, `USAGE.md`

**Verification:**
- `curl http://localhost:8001/metrics` returns Prometheus format
- Run 20 triage requests (mix of weapon/clean)
- Verify counters increment correctly
- Verify histogram buckets make sense for our latency range
- Confirm existing `/status` endpoint still works (different purpose)

---

## 5. Webhook / Callback on Alert

**Goal:** When `should_alert=true`, automatically POST the triage result to a configurable URL.

**Why:** Currently the caller must poll or wait for the synchronous response. For integration with alerting systems, a push model is more natural -- the triage server notifies downstream systems immediately.

**Implementation:**
- Add `ALERT_WEBHOOK_URL` environment variable (default: empty/disabled)
- Add `ALERT_WEBHOOK_HEADERS` environment variable for auth headers (e.g., `X-API-Key:secret`)
- In `run_triage()`, after building the response, if `should_alert=true` and webhook is configured:
  - Fire-and-forget async POST to the webhook URL with the TriageResponse as JSON body
  - Do not block the response to the caller
  - Log webhook success/failure
- Add retry with backoff (1 retry, 2s delay) for webhook delivery
- Add `webhook_delivered` boolean field to TriageResponse when webhook is configured

**Files:** `api_server.py`, `docker-compose.yml` (env var), `USAGE.md`

**Verification:**
- Set up a simple webhook receiver (e.g., `nc -l 9999` or a Flask echo server)
- Submit a weapon image -- verify webhook fires with correct payload
- Submit a clean image -- verify webhook does NOT fire
- Test with webhook URL down -- verify triage response still returns normally
- Test with auth headers -- verify they're sent
