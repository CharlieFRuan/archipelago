#!/bin/bash
#
# Run the simple_task example end-to-end.
#
# Usage:
#   cd archipelago/examples/simple_task
#   ./run.sh
#
# Prerequisites:
#   - Modal credentials (MODAL_TOKEN_ID, MODAL_TOKEN_SECRET)
#   - vLLM server running locally
#   - LLM config set in orchestrator_config.json and grading_settings.json
#

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIPELAGO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

export EXAMPLE_DIR="$SCRIPT_DIR"
export ENVIRONMENT_DIR="$ARCHIPELAGO_DIR/environment"
export AGENTS_DIR="$ARCHIPELAGO_DIR/agents"
export GRADING_DIR="$ARCHIPELAGO_DIR/grading"

echo "============================================================"
echo "SIMPLE TASK EXAMPLE (Modal Sandbox)"
echo "============================================================"
echo "Example dir:     $EXAMPLE_DIR"
echo "Archipelago dir: $ARCHIPELAGO_DIR"
echo "============================================================"

# Install agent dependencies (includes modal, requests)
echo "Installing agent dependencies..."
cd "$AGENTS_DIR"
uv sync

# Install grading dependencies
echo "Installing grading dependencies..."
cd "$GRADING_DIR"
uv sync

# Run the main script using uv from agents dir (which has modal + requests)
cd "$AGENTS_DIR" && uv run python "$EXAMPLE_DIR/main.py"
