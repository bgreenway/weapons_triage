# Gemma-4-26B Serving Stack

Runs **Gemma-4-26B-A4B-it** on 4x RTX 5090 GPUs via Docker Compose, providing three interfaces:

1. **OpenAI-Compatible API** (port 8000) -- chat completions, summarization, general use
2. **Chat Web UI** (port 8080) -- Open WebUI for browser-based conversations
3. **Weapons Triage Endpoint** (port 8001) -- image analysis for visible weapons detection

## Architecture

```
docker-compose.yml
├── gemma4        vLLM OpenAI server     port 8000   (4x GPU)
├── triage        FastAPI HTTP client     port 8001   (no GPU)
└── open-webui    Chat UI                 port 8080   (no GPU)
```

Only the `gemma4` container loads the model and uses GPUs. The `triage` container is a lightweight FastAPI app that sends requests to vLLM over the Docker network. Open WebUI connects to the same vLLM instance.

## Project Structure

```
~/gemma4_serve/
├── docker-compose.yml        # Three-service stack definition
├── Dockerfile.triage         # Lightweight Python image for triage service
├── api_server.py             # FastAPI triage server (HTTP client to vLLM)
├── requirements-triage.txt   # Triage container dependencies
├── USAGE.md                  # User-facing instructions for all 3 interfaces
└── README.md                 # This file
```

## Prerequisites

- Docker and Docker Compose
- NVIDIA Container Toolkit
- Model downloaded to `~/gemma-4-26B-A4B-it/`

## Quick Start

```bash
cd ~/gemma4_serve
docker compose up -d --build
```

The model takes 2-3 minutes to load. The triage and Open WebUI containers wait for the model to be ready before starting.

Watch model loading progress:

```bash
docker compose logs -f gemma4
```

## Verify

```bash
# vLLM API
curl http://localhost:8000/v1/models

# Triage health
curl http://localhost:8001/health

# Open WebUI
# Open http://192.168.1.201:8080 in a browser
```

## Stop / Restart

```bash
docker compose down          # Stop everything
docker compose up -d         # Start (no rebuild needed)
docker compose up -d --build # Rebuild triage container after code changes
```

## User Instructions

See [USAGE.md](USAGE.md) for detailed instructions on using all three interfaces, including curl and Python examples.
