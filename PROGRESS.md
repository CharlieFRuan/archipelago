# Archipelago Simple Task - Running with vLLM + Modal

## Setup

- **Environment sandbox**: Modal Sandbox (image built from `environment/Dockerfile`)
- **LLM (agent + grading)**: Local vLLM server at `http://0.0.0.0:8000` serving `Qwen/Qwen3-VL-30B-A3B-Thinking`
- **Agent & Grading**: Run locally with uv

## Prerequisites

### 1. vLLM with vision + tool calling

```bash
cd ~/default/SkyRL
uv run --extra fsdp vllm serve Qwen/Qwen3-VL-30B-A3B-Thinking \
  --tensor-parallel-size 8 \
  --enable-auto-tool-choice --tool-call-parser hermes \
  --max-model-len 8192 \
  --limit-mm-per-prompt '{"image": 5}'
```

Flags explained:
- `--enable-auto-tool-choice --tool-call-parser hermes` — required for OpenAI-format tool calling
- `--enforce-eager` — disables CUDA graph capture (needed to work around a PTX kernel issue in the vision encoder)
- `--max-model-len 8192` — limits context to save GPU memory
- `--limit-mm-per-prompt '{"image": 5}'` — caps images per request

### 2. vLLM patch for VIT flash attention

The bundled `vllm_flash_attn` CUDA kernels have a PTX version incompatibility with driver 550.163.01 on H100s. The vision encoder's attention wrapper must be patched to use the system `flash_attn` package instead:

**File**: `~/.venv/lib/python3.12/site-packages/vllm/attention/ops/vit_attn_wrappers.py` (inside your vLLM venv)

**Line 35**, change:
```python
from vllm.attention.utils.fa_utils import flash_attn_varlen_func
```
to:
```python
from flash_attn import flash_attn_varlen_func
```

This only affects the vision encoder (ViT) attention path. The text-only model (`Qwen3-30B-A3B`) doesn't hit this code path and works without the patch.

### 3. Modal credentials

```bash
export MODAL_TOKEN_ID=...
export MODAL_TOKEN_SECRET=...
```

### 4. Dependencies (one-time)

```bash
cd archipelago/agents && uv sync
cd ../grading && uv sync
```

## How to Run

```bash
cd ~/default/archipelago/agents
OPENAI_API_KEY=dummy OPENAI_API_BASE=http://0.0.0.0:8000/v1 \
  uv run python ../examples/simple_task/main.py
```

## What Happens

`main.py` does the following:

1. Builds a Modal image from `environment/Dockerfile` via `Image.from_dockerfile()` (cached after first build, ~15 min initial)
2. Creates a Modal Sandbox running the environment FastAPI server on port 8080
3. Exposes the sandbox via HTTPS tunnel
4. Populates the sandbox with the world snapshot (`original_snapshot.zip`)
5. Configures the filesystem MCP server inside the sandbox
6. Runs the agent locally (connects to sandbox MCP gateway, calls local vLLM)
7. Downloads the final snapshot from the sandbox
8. Runs grading locally (calls local vLLM as the judge)
9. Terminates the sandbox

With cached image, a full run takes ~1-2 minutes.

## Files Modified from Upstream

| File | Change |
|------|--------|
| `examples/simple_task/main.py` | Uses `Image.from_dockerfile()` + Modal Sandbox instead of Docker |
| `examples/simple_task/run.sh` | Updated for Modal workflow |
| `examples/simple_task/orchestrator_config.json` | Model: `openai/Qwen/Qwen3-VL-30B-A3B-Thinking`, `api_base` pointing to local vLLM, `enable_thinking: false` |
| `examples/simple_task/grading_settings.json` | Same vLLM config for the grading judge |
| `examples/simple_task/mcp_config.json` | Uses `uv run python` command (activates server's own `.venv`) |
| `examples/simple_task/agent_config.json` | Switched to `loop_agent` |
| `examples/simple_task/initial_messages.json` | Simplified system prompt |
| `agents/.env` | `OPENAI_API_KEY=dummy`, `OPENAI_API_BASE=http://0.0.0.0:8000/v1` |
| `grading/.env` | Same |

## Notes

- **Why `uv run python` in mcp_config.json**: The Dockerfile installs each MCP server into its own `.venv`. Using `uv run` ensures the server's own virtualenv is activated. Plain `python` (from the main `/app/.venv`) doesn't have server-specific deps like `mcp-schema`.
- **Why `enable_thinking: false`**: Without this, the Thinking model consumes all output tokens on `<think>` reasoning before generating tool calls, causing empty responses.
- **Why `loop_agent` instead of `react_toolbelt_agent`**: Simpler agent loop, fewer meta-tools that confuse smaller models.
- `VLLM_URL` and `VLLM_MODEL` env vars can override the defaults in `main.py`.
- Modal image caching is per-account. Cached images persist for days/weeks. Force rebuild with `force_build=True` in `Image.from_dockerfile()`.
