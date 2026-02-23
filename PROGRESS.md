# Archipelago Simple Task - Running with vLLM + Modal

## Setup

- **Environment sandbox**: Modal Sandbox (no Docker needed)
- **LLM (agent + grading)**: Local vLLM server at `http://0.0.0.0:8000` serving `Qwen/Qwen3-30B-A3B`
- **Agent & Grading**: Run locally with uv

## Prerequisites

- vLLM running with tool calling enabled:
  ```
  vllm serve Qwen/Qwen3-30B-A3B --tensor-parallel-size 8 \
    --enable-auto-tool-choice --tool-call-parser hermes \
    --reasoning-parser deepseek_r1
  ```
- Modal credentials exported (`MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`)
- Dependencies installed (one-time):
  ```bash
  cd archipelago/agents && uv sync
  cd ../grading && uv sync
  ```

## How to Run

```bash
cd archipelago/agents
OPENAI_API_KEY=dummy OPENAI_API_BASE=http://0.0.0.0:8000/v1 \
  uv run python ../examples/simple_task/main.py
```

Or via the shell script:
```bash
cd archipelago/examples/simple_task
./run.sh
```

## What Happens

`main.py` does the following:

1. Builds a Modal image (debian slim + Python 3.13 + environment code + filesystem MCP server)
2. Creates a Modal Sandbox running the environment FastAPI server on port 8080
3. Exposes the sandbox via HTTPS tunnel
4. Populates the sandbox with the world snapshot (`original_snapshot.zip`)
5. Configures the filesystem MCP server inside the sandbox
6. Runs the agent locally (connects to sandbox MCP gateway, calls local vLLM)
7. Downloads the final snapshot from the sandbox
8. Runs grading locally (calls local vLLM as the judge)
9. Terminates the sandbox

## Files Modified from Upstream

| File | Change |
|------|--------|
| `examples/simple_task/main.py` | Uses Modal Sandbox instead of Docker |
| `examples/simple_task/run.sh` | Updated for Modal workflow |
| `examples/simple_task/orchestrator_config.json` | Model: `openai/Qwen/Qwen3-30B-A3B`, `api_base` pointing to local vLLM |
| `examples/simple_task/grading_settings.json` | Same vLLM config for the grading judge |
| `examples/simple_task/mcp_config.json` | Uses `uv run python` command, container paths |
| `examples/simple_task/agent_config.json` | Switched to `loop_agent` |
| `examples/simple_task/initial_messages.json` | Simplified system prompt |
| `agents/.env` | `OPENAI_API_KEY=dummy`, `OPENAI_API_BASE=http://0.0.0.0:8000/v1` |
| `grading/.env` | Same |

## Notes

- The vLLM flags `--enable-auto-tool-choice --tool-call-parser hermes` are required for the agent's tool calling to work.
- `--reasoning-parser deepseek_r1` separates Qwen3's `<think>` tokens into `reasoning_content` so they don't pollute tool call parsing.
- `VLLM_URL` and `VLLM_MODEL` env vars can override the defaults if needed.
- Modal image is cached after first build; subsequent runs reuse it.
