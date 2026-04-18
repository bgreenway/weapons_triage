# Gemma-4-26B Server - User Guide

This server runs the Gemma-4-26B-A4B-it model and provides three interfaces. The server is currently running at **192.168.1.201**. If the server is moved to a different machine, the IP address will change.

---

## 1. Chat Web UI (Open WebUI)

**URL:** http://192.168.1.201:8080

Open this in your browser. On first visit, you'll be asked to create an account. After logging in, select **gemma-4-26b-a4b-it** as the model and start chatting.

This interface supports text conversations and image uploads (drag and drop or click the attachment icon).

---

## 2. OpenAI-Compatible API

**Base URL:** http://192.168.1.201:8000

This is a standard OpenAI-compatible API. You can use it with any tool or library that supports the OpenAI API format.

### List available models

```bash
curl http://192.168.1.201:8000/v1/models
```

### Send a chat message

```bash
curl http://192.168.1.201:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-4-26b-a4b-it",
    "messages": [
      {"role": "user", "content": "What is the capital of France?"}
    ],
    "max_tokens": 200
  }'
```

### Use with Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.1.201:8000/v1",
    api_key="not-needed",
)

response = client.chat.completions.create(
    model="gemma-4-26b-a4b-it",
    messages=[{"role": "user", "content": "Summarize the theory of relativity in two sentences."}],
    max_tokens=200,
)

print(response.choices[0].message.content)
```

---

## 3. Weapons Triage Endpoint

This endpoint analyzes security camera images for visible weapons. There are three ways to submit images, all returning the same structured JSON response.

---

### Method 1: Form-Data (anchor + crops)

**URL:** `POST http://192.168.1.201:8001/v1/triage`

Submit images as individual multipart form fields. The `anchor` is the full scene image. The `crops` are optional cropped views of a detected person, typically captured at ~100ms intervals.

| Field      | Type   | Required | Description                              |
|------------|--------|----------|------------------------------------------|
| `anchor`   | file   | Yes      | Full scene image from the camera         |
| `crops`    | file(s)| No       | Cropped images of a detected person (repeat field for multiple) |
| `event_id` | string | No       | Your event identifier (auto-generated if omitted) |
| `camera_id`| string | No       | Camera identifier for logging            |

**Single image (anchor only):**

```bash
curl -X POST http://192.168.1.201:8001/v1/triage \
  -F "anchor=@scene.jpg"
```

**Anchor + cropped images:**

```bash
curl -X POST http://192.168.1.201:8001/v1/triage \
  -F "anchor=@scene.jpg" \
  -F "crops=@C1.jpg" \
  -F "crops=@C2.jpg" \
  -F "crops=@C3.jpg" \
  -F "crops=@C4.jpg" \
  -F "crops=@C5.jpg"
```

**With event and camera IDs:**

```bash
curl -X POST http://192.168.1.201:8001/v1/triage \
  -F "anchor=@scene.jpg" \
  -F "crops=@C1.jpg" \
  -F "crops=@C2.jpg" \
  -F "event_id=evt-001" \
  -F "camera_id=lobby-cam-3"
```

---

### Method 2: Zip Package

**URL:** `POST http://192.168.1.201:8001/v1/triage`

Submit all images in a single zip file. The endpoint automatically identifies the anchor and crops based on filename conventions and ignores annotation files.

| Field      | Type   | Required | Description                              |
|------------|--------|----------|------------------------------------------|
| `package`  | file   | Yes      | Zip file containing the images           |
| `event_id` | string | No       | Your event identifier (auto-generated if omitted) |
| `camera_id`| string | No       | Camera identifier for logging            |

**Filename conventions inside the zip:**

| Pattern | Role | Examples |
|---------|------|----------|
| `O.jpg` or `*_O.jpg` | **Anchor** (required) | `O.jpg`, `2026-04-07T16-00-20_cam1_O.jpg` |
| `C1.jpg`, `C2.jpg`, ... or `*_C1.jpg`, `*_C2.jpg`, ... | **Crops** (optional) | `C1.jpg`, `2026-04-07T16-00-20_cam1_C1.jpg` |
| `A.jpg` or `*_A.jpg` | **Annotation** (ignored) | `A.jpg`, `2026-04-07T16-00-20_cam1_A.jpg` |
| Anything else | Ignored | `metadata.json`, `README.txt` |

- The zip must contain exactly one anchor image (`O.jpg` or `*_O.jpg`)
- Crops are sorted by filename, so `C1` is processed before `C2`, etc.
- Annotation images (`_A.jpg`) are automatically skipped -- they contain bounding boxes and zoomed views meant for human review, not model input
- Supported image formats: `.jpg`, `.jpeg`, `.png`
- Filenames are matched case-insensitively

**Example:**

```bash
curl -X POST http://192.168.1.201:8001/v1/triage \
  -F "package=@event_bundle.zip"
```

**Example zip contents:**

```
event_bundle.zip
├── 2026-04-07T16-00-20-030Z_cam1_O.jpg    (anchor - used)
├── 2026-04-07T16-00-20-030Z_cam1_C1.jpg   (crop 1 - used)
├── 2026-04-07T16-00-20-130Z_cam1_C2.jpg   (crop 2 - used)
├── 2026-04-07T16-00-20-230Z_cam1_C3.jpg   (crop 3 - used)
├── 2026-04-07T16-00-20-330Z_cam1_C4.jpg   (crop 4 - used)
├── 2026-04-07T16-00-20-430Z_cam1_C5.jpg   (crop 5 - used)
├── 2026-04-07T16-00-20-030Z_cam1_A.jpg    (annotation - skipped)
└── metadata.json                           (ignored)
```

---

### Method 3: Raw Image

**URL:** `POST http://192.168.1.201:8001/v1/triage/image`

Submit a single image as the raw request body. This is the simplest integration -- no multipart encoding, no zip, just the image bytes. Useful for quick single-frame checks.

| Parameter  | Location | Required | Description                              |
|------------|----------|----------|------------------------------------------|
| Body       | body     | Yes      | Raw image bytes (JPEG or PNG)            |
| `event_id` | query    | No       | Your event identifier (auto-generated if omitted) |

**Example:**

```bash
curl -X POST http://192.168.1.201:8001/v1/triage/image \
  -H "Content-Type: image/jpeg" \
  --data-binary @scene.jpg
```

**With event ID:**

```bash
curl -X POST "http://192.168.1.201:8001/v1/triage/image?event_id=evt-001" \
  -H "Content-Type: image/jpeg" \
  --data-binary @scene.jpg
```

**Note:** This method only supports a single image (no crops). For best accuracy, use Method 1 or Method 2 with an anchor and multiple crops.

### Response format

```json
{
  "event_id": "evt-001",
  "visible_weapon": true,
  "weapon_types": ["firearm"],
  "confidence": 0.95,
  "law_enforcement": false,
  "environment_type": "residential",
  "person_description": "Adult male in a grey t-shirt and khaki shorts, holding a long gun.",
  "should_alert": true,
  "requires_review": false,
  "error": null,
  "inference_time_ms": 2260.84,
  "images_used": 4
}
```

### Response fields

| Field                | Type    | Description                                              |
|----------------------|---------|----------------------------------------------------------|
| `event_id`           | string  | The event identifier (yours or auto-generated)           |
| `visible_weapon`     | bool    | Whether a weapon is visibly present                      |
| `weapon_types`       | list    | Types of weapons detected (e.g., "firearm", "knife")     |
| `confidence`         | float   | Model's self-reported confidence (0-1)                   |
| `law_enforcement`    | bool    | Whether law enforcement indicators are present           |
| `environment_type`   | string  | Description of the environment (e.g., "residential")     |
| `person_description` | string  | Description of the person in the image                   |
| `should_alert`       | bool    | Whether this event should trigger an alert               |
| `requires_review`    | bool    | Whether a human should review this result                |
| `error`              | string  | Error message if something went wrong, otherwise null    |
| `inference_time_ms`  | float   | How long the model took to process the images            |
| `images_used`        | int     | Number of images that were analyzed                      |

### Health check

```bash
curl http://192.168.1.201:8001/health
```

### Server status

Returns live metrics from the vLLM inference engine, useful for monitoring and load balancing.

```bash
curl http://192.168.1.201:8001/status
```

```json
{
  "healthy": true,
  "requests_running": 3,
  "requests_waiting": 1,
  "kv_cache_usage": 0.42,
  "model": "gemma-4-26b-a4b-it"
}
```

| Field              | Type   | Description                                              |
|--------------------|--------|----------------------------------------------------------|
| `healthy`          | bool   | Whether the vLLM backend is reachable                    |
| `requests_running` | int    | Requests currently being processed by the model          |
| `requests_waiting` | int    | Requests queued waiting for processing                   |
| `kv_cache_usage`   | float  | KV cache utilization (0.0-1.0), indicates memory pressure|
| `model`            | string | The model currently loaded                               |

---

## Ports Summary

| Port | Interface             | Purpose                          |
|------|-----------------------|----------------------------------|
| 8000 | OpenAI-compatible API | Chat completions, model queries  |
| 8001 | Triage endpoint       | Weapons detection from images    |
| 8080 | Open WebUI            | Browser-based chat interface     |
