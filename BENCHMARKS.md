# Triage Benchmark Results

Server: Ubuntu, 4x NVIDIA RTX 5090 (32GB each)
Model: google/gemma-4-26B-A4B-it
Date: 2026-04-18

---

## Accuracy

### Labeled Test Packages (26 packages with ground truth)

| Metric | Value |
|--------|-------|
| Accuracy | 25/26 (96.2%) |
| True Positives | 20 (weapon correctly detected) |
| True Negatives | 5 (clean correctly identified) |
| False Positives | 1 (hammer/crowbar -- not a firearm) |
| False Negatives | 0 (no weapons missed) |

The one "false positive" correctly identified the objects as `["hammer"]` in the `weapon_types` field. Filtering on `weapon_types` for firearms only would yield 100% accuracy on this test set.

### Production Alert Samples (10 events from alert server)

- 5 weapons detected, 5 clean
- All results visually verified as correct

### Misclassified Samples (24 events the old GPT/Haiku system got wrong)

| Category | Count | Our Model |
|----------|-------|-----------|
| ANDGATE_SAVED (FPs caught by AND-gate) | 8 | 8/8 correctly clean |
| FP_CORRECT (FPs old system rejected) | 3 | 2/3 correctly clean |
| FP_LEAK (FPs old system leaked) | 4 | 3/4 correctly clean |
| TP_CORRECT (weapons old system caught) | 4 | 4/4 detected |
| TP_MISS (weapons old system missed) | 5 | 5/5 detected |

- Caught all 9 real weapons (old system missed 5 of them)
- Correctly dismissed 13 of 15 false positives (old system needed an AND-gate to catch 8)
- 2 false positives at confidence 0.85 and 0.9 (fanny pack, phone) -- a 0.95 threshold would eliminate both

---

## Latency (Single Request)

| Dataset | Images per Request | Avg | Min | Max |
|---------|-------------------|-----|-----|-----|
| Alert server samples (small images) | 4 | 500ms | 437ms | 566ms |
| Misclassified samples | 4 | 910ms | 840ms | 956ms |
| 200-event production cluster | 4 | 649ms | 507ms | 5991ms |
| Labeled test packages (large images) | 6 | 2937ms | 2054ms | 3862ms |

Latency scales with image size and count. Production-sized images typically complete in under 1 second.

---

## Concurrency

Tested by submitting N requests simultaneously from the production 200-event cluster.

| Concurrent Requests | Wall Clock | Avg Latency | Max Latency | Throughput | Errors |
|--------------------|-----------|-------------|-------------|------------|--------|
| 1 (sequential) | 0.6s | 600ms | 600ms | 1.6 req/s | 0 |
| 10 | 3.3s | 3.2s | 3.3s | 3.0 req/s | 0 |
| 25 | 3.8s | 3.2s | 3.7s | 6.6 req/s | 0 |
| 50 | 8.8s | 2.6s | 8.6s | 5.7 req/s | 1 |
| 100 | 10.2s | 3.4s | 9.9s | 9.8 req/s | 1 |

- vLLM batches concurrent requests efficiently -- 25 requests complete in nearly the same time as 10
- Throughput peaks around 10 req/s at 100 concurrent
- Max latency stays under 10 seconds even at 100 concurrent
- Error rate ~1% under heavy load (JSON parse failures on clean scenes, caught by retry logic)
- No crashes, no dropped requests at any concurrency level

---

## Notes

- `confidence` represents the model's confidence in its conclusion (weapon or clean), not just weapon detection
- `weapon_types` provides specific classification (firearm, handgun, hammer, etc.) enabling post-filtering
- False positives consistently appear at lower confidence (0.85-0.9) vs true positives (0.95-1.0)
- Clean scenes with clear views return confidence 0.95-1.0
