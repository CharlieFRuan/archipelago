# Archipelago Simple Task - Setup Progress

## Goal
Run the "Option B: Simple Task" (find gorilla image) end-to-end using:
- **LLM**: Local vLLM server at http://0.0.0.0:8000 serving Qwen/Qwen3-30B-A3B
- **Environment**: Two modes: local process OR Modal sandbox
- **Agent & Grading**: Run locally with uv

## Current Status: COMPLETED (both local and Modal modes)

- Local mode: score **1.0** (pass)
- Modal mode: pipeline runs E2E (score varies due to model non-determinism)

## Key Findings
- Docker is NOT installed on this machine
- vLLM v0.13.0 is running at http://0.0.0.0:8000 serving `Qwen/Qwen3-30B-A3B`
- Modal CLI v1.3.3 / Python SDK 1.3.0 installed with valid tokens
- Python 3.12.12 available system-wide; uv auto-installed Python 3.13 for projects that require it
- Both agents and grading use LiteLLM for LLM calls
- LiteLLM respects `OPENAI_API_BASE` env var and `api_base` in extra_args for OpenAI-compatible servers

## What Was Done

### Phase 1: Restart vLLM with tool calling support
- Killed the existing vLLM process (which lacked tool calling flags)
- Restarted with: `vllm serve Qwen/Qwen3-30B-A3B --tensor-parallel-size 8 --enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser deepseek_r1`
- The `--enable-auto-tool-choice --tool-call-parser hermes` flags enable proper OpenAI-format tool calling
- The `--reasoning-parser deepseek_r1` separates `<think>` reasoning tokens into `reasoning_content` field

### Phase 2: Set up environment (local mode)
- Created `/filesystem` and `/.apps_data` directories with correct permissions
- Installed environment dependencies: `cd environment && uv sync`
- Installed filesystem MCP server deps: `cd mcp_servers/filesystem/mcp_servers/filesystem_server && uv sync --all-extras`
- Modified `simple_task/main.py` to start environment as a local process instead of Docker
- Updated `mcp_config.json` to use local paths and `uv run python`

### Phase 3: Set up environment (Modal mode)
- Created `examples/simple_task/run_modal.py` - standalone Modal-based runner
- Uses `modal.Image.debian_slim(python_version="3.13")` with apt/uv deps
- Copies environment code and filesystem MCP server into the image (excluding `.venv`)
- Creates a Modal Sandbox with `encrypted_ports=[8080]` for tunnel access
- Agent and grading run locally, connecting to the sandbox via HTTPS tunnel URL
- MCP config uses `/app/mcp_servers/...` paths (inside the sandbox container)

### Phase 4: Configure agent and grading for vLLM
- Created `.env` files with `OPENAI_API_KEY=dummy` and `OPENAI_API_BASE=http://0.0.0.0:8000/v1`
- Updated `orchestrator_config.json` and `grading_settings.json` for `openai/Qwen/Qwen3-30B-A3B`
- Switched from `react_toolbelt_agent` to simpler `loop_agent`
- Simplified system prompt for compatibility with smaller models

## Files Changed

| File | Change |
|------|--------|
| `examples/simple_task/main.py` | Replaced Docker-based `start_environment()` with local process startup |
| `examples/simple_task/run_modal.py` | **NEW** - Modal sandbox-based runner for E2E pipeline |
| `examples/simple_task/orchestrator_config.json` | Model -> `openai/Qwen/Qwen3-30B-A3B`, added `api_base` |
| `examples/simple_task/grading_settings.json` | Model -> `openai/Qwen/Qwen3-30B-A3B`, added `api_base` |
| `examples/simple_task/mcp_config.json` | Updated paths to local filesystem, command `uv run python` |
| `examples/simple_task/agent_config.json` | Switched to `loop_agent` with adjusted config |
| `examples/simple_task/initial_messages.json` | Simplified system prompt |
| `agents/.env` | Created with vLLM config |
| `grading/.env` | Created with vLLM config |
| `environment/.env` | Copied from .env.example |

## How to Run

### Option A: Local Mode (no Docker/Modal needed)

```bash
# 1. Start vLLM with tool calling support
cd /path/to/SkyRL
uv run --extra fsdp vllm serve Qwen/Qwen3-30B-A3B \
  --tensor-parallel-size 8 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --reasoning-parser deepseek_r1

# 2. Install all dependencies (one-time)
cd archipelago/environment && uv sync
cd ../mcp_servers/filesystem/mcp_servers/filesystem_server && uv sync --all-extras
cd ../../../../agents && uv sync
cd ../grading && uv sync

# 3. Create dirs and start environment server
sudo mkdir -p /filesystem /.apps_data && sudo chown $(id -u):$(id -g) /filesystem /.apps_data
cd archipelago/environment
APP_FS_ROOT=/filesystem uv run uvicorn runner.main:app --host 0.0.0.0 --port 8080 &

# 4. Run the simple task E2E
cd archipelago/agents
OPENAI_API_KEY=dummy OPENAI_API_BASE=http://0.0.0.0:8000/v1 \
  uv run python ../examples/simple_task/main.py

# 5. Check results
cat ../examples/simple_task/grades.json | python3 -m json.tool
```

### Option B: Modal Mode (environment in cloud sandbox)

```bash
# 1. Start vLLM with tool calling support (same as above)

# 2. Install deps (one-time, same as above)

# 3. Set Modal credentials
export MODAL_TOKEN_ID=...
export MODAL_TOKEN_SECRET=...

# 4. Run via Modal
cd archipelago/agents
OPENAI_API_KEY=dummy OPENAI_API_BASE=http://0.0.0.0:8000/v1 \
  uv run python ../examples/simple_task/run_modal.py

# 5. Check results
cat ../examples/simple_task/grades.json | python3 -m json.tool
```

The Modal runner automatically:
- Builds a container image with the environment and filesystem MCP server
- Creates a sandbox and exposes port 8080 via HTTPS tunnel
- Runs the agent and grading locally, connecting to the sandbox
- Terminates the sandbox when done
