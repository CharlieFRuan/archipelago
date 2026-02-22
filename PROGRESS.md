# Archipelago Simple Task - Setup Progress

## Goal
Run the "Option B: Simple Task" (find gorilla image) end-to-end using:
- **LLM**: Local vLLM server at http://0.0.0.0:8000 serving Qwen/Qwen3-30B-A3B
- **Environment**: Running locally (no Docker needed)
- **Agent & Grading**: Run locally with uv

## Current Status: COMPLETED SUCCESSFULLY

Final score: **1.0** (pass) - Agent correctly identified gorilla at `/animals/xk92m/qz7fw.png`

## Key Findings
- Docker is NOT installed on this machine
- vLLM v0.13.0 is running at http://0.0.0.0:8000 serving `Qwen/Qwen3-30B-A3B`
- Modal CLI v1.3.3 is installed with valid tokens (not needed for simple local run)
- Python 3.12.12 available system-wide; uv auto-installed Python 3.13 for projects that require it
- Both agents and grading use LiteLLM for LLM calls
- LiteLLM respects `OPENAI_API_BASE` env var and `api_base` in extra_args for OpenAI-compatible servers

## What Was Done

### Phase 1: Restart vLLM with tool calling support
- Killed the existing vLLM process (which lacked tool calling flags)
- Restarted with: `vllm serve Qwen/Qwen3-30B-A3B --tensor-parallel-size 8 --enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser deepseek_r1`
- The `--enable-auto-tool-choice --tool-call-parser hermes` flags enable proper OpenAI-format tool calling
- The `--reasoning-parser deepseek_r1` separates `<think>` reasoning tokens into `reasoning_content` field

### Phase 2: Set up environment (locally, no Docker/Modal needed)
- Created `/filesystem` and `/.apps_data` directories with correct permissions
- Installed environment dependencies: `cd environment && uv sync`
- Installed filesystem MCP server deps: `cd mcp_servers/filesystem/mcp_servers/filesystem_server && uv sync --all-extras`
- Started environment FastAPI server: `APP_FS_ROOT=/filesystem uv run uvicorn runner.main:app --host 0.0.0.0 --port 8080`
- Updated `mcp_config.json` to use local paths instead of Docker container paths

### Phase 3: Configure agent for vLLM
- Installed agent deps: `cd agents && uv sync`
- Created `agents/.env` with `OPENAI_API_KEY=dummy` and `OPENAI_API_BASE=http://0.0.0.0:8000/v1`
- Updated `orchestrator_config.json`: model → `openai/Qwen/Qwen3-30B-A3B`, extra_args with `api_base`
- Switched from `react_toolbelt_agent` to simpler `loop_agent` (less overhead, works better with smaller models)
- Simplified system prompt (removed toolbelt-specific references)

### Phase 4: Configure grading for vLLM
- Installed grading deps: `cd grading && uv sync`
- Created `grading/.env` with `OPENAI_API_KEY=dummy` and `OPENAI_API_BASE=http://0.0.0.0:8000/v1`
- Updated `grading_settings.json`: model → `openai/Qwen/Qwen3-30B-A3B`, extra_args with `api_base`

### Phase 5: Run E2E
- Modified `simple_task/main.py` to start environment locally instead of via Docker
- Full pipeline completed successfully:
  1. Environment started and healthy
  2. World snapshot populated (9 objects, 70KB)
  3. MCP filesystem server configured
  4. Agent ran (9 steps, ~8 seconds): explored dirs, listed files, identified gorilla
  5. Final snapshot saved
  6. Grading passed: score 1.0

## Files Changed

| File | Change |
|------|--------|
| `examples/simple_task/main.py` | Replaced Docker-based `start_environment()` with local process startup |
| `examples/simple_task/orchestrator_config.json` | Model → `openai/Qwen/Qwen3-30B-A3B`, added `api_base` |
| `examples/simple_task/grading_settings.json` | Model → `openai/Qwen/Qwen3-30B-A3B`, added `api_base` |
| `examples/simple_task/mcp_config.json` | Updated paths to local filesystem, command `uv run python` |
| `examples/simple_task/agent_config.json` | Switched to `loop_agent` with adjusted config |
| `examples/simple_task/initial_messages.json` | Simplified system prompt |
| `agents/.env` | Created with vLLM config |
| `grading/.env` | Created with vLLM config |
| `environment/.env` | Copied from .env.example |

## How to Reproduce

### Prerequisites
- vLLM server running at http://0.0.0.0:8000 with Qwen/Qwen3-30B-A3B
- uv installed
- `/filesystem` and `/.apps_data` directories exist and are writable

### Steps

```bash
# 1. Start vLLM with tool calling support
cd /path/to/SkyRL
uv run --extra fsdp vllm serve Qwen/Qwen3-30B-A3B \
  --tensor-parallel-size 8 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --reasoning-parser deepseek_r1

# 2. Install all dependencies
cd archipelago/environment && uv sync
cd ../mcp_servers/filesystem/mcp_servers/filesystem_server && uv sync --all-extras
cd ../../../../agents && uv sync
cd ../grading && uv sync

# 3. Start environment server
cd archipelago/environment
APP_FS_ROOT=/filesystem uv run uvicorn runner.main:app --host 0.0.0.0 --port 8080 &

# 4. Run the simple task E2E
cd archipelago/agents
OPENAI_API_KEY=dummy OPENAI_API_BASE=http://0.0.0.0:8000/v1 \
  uv run python archipelago/examples/simple_task/main.py

# 5. Check results
cat archipelago/examples/simple_task/grades.json | python3 -m json.tool
```
