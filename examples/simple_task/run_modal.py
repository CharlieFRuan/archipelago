#!/usr/bin/env python3
"""
Run the simple_task example using Modal Sandbox for the environment.

The environment (MCP gateway + filesystem server) runs in a Modal Sandbox.
The agent and grading run locally, connecting to the sandbox via tunnel URL.
The LLM (vLLM) runs locally at http://0.0.0.0:8000.

Usage:
    cd archipelago/examples/simple_task
    python run_modal.py

Prerequisites:
    - Modal credentials (MODAL_TOKEN_ID, MODAL_TOKEN_SECRET)
    - vLLM server running at http://0.0.0.0:8000
    - uv installed
    - agents and grading deps installed (uv sync in each dir)
"""

import io
import json
import os
import subprocess
import sys
import tarfile
import time
import uuid
import zipfile
from pathlib import Path

import modal
import requests

# Paths
SCRIPT_DIR = Path(__file__).parent
ARCHIPELAGO_DIR = SCRIPT_DIR.parent.parent
ENVIRONMENT_DIR = ARCHIPELAGO_DIR / "environment"
AGENTS_DIR = ARCHIPELAGO_DIR / "agents"
GRADING_DIR = ARCHIPELAGO_DIR / "grading"
MCP_SERVERS_DIR = ARCHIPELAGO_DIR / "mcp_servers"

VLLM_URL = os.environ.get("VLLM_URL", "http://0.0.0.0:8000/v1")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "openai/Qwen/Qwen3-30B-A3B")


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _ignore_filter(path: Path) -> bool:
    """Return True to IGNORE (exclude) files from Modal image upload."""
    excluded_dirs = {
        ".venv", "__pycache__", ".git", ".ruff_cache",
        ".pytest_cache", "node_modules", ".mypy_cache",
    }
    if any(p in excluded_dirs for p in path.parts):
        return True
    # Exclude .env files (env vars set via Modal image.env())
    if path.name == ".env":
        return True
    return False


def build_modal_image() -> modal.Image:
    """Build the Modal image for the environment sandbox."""
    log("Building Modal image...")

    image = (
        modal.Image.debian_slim(python_version="3.13")
        .apt_install("curl", "git", "build-essential")
        .run_commands(
            "curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh"
        )
        .workdir("/app")
        # Copy environment code (pyproject.toml, uv.lock, runner/, etc.)
        .add_local_dir(
            str(ENVIRONMENT_DIR),
            remote_path="/app",
            ignore=_ignore_filter,
            copy=True,
        )
        # Copy filesystem MCP server
        .add_local_dir(
            str(MCP_SERVERS_DIR / "filesystem"),
            remote_path="/app/mcp_servers/filesystem",
            ignore=_ignore_filter,
            copy=True,
        )
        # Install dependencies
        .run_commands(
            "cd /app && uv sync",
            "cd /app/mcp_servers/filesystem/mcp_servers/filesystem_server && uv sync --all-extras",
            "mkdir -p /filesystem /.apps_data",
        )
        .env({
            "PATH": "/app/.venv/bin:/usr/local/bin:/usr/bin:/bin",
            "UV_SYSTEM_PYTHON": "1",
            "APP_FS_ROOT": "/filesystem",
            "GUI_ENABLED": "true",
            "INTERNET_ENABLED": "false",
            "HAS_STATE": "true",
            "STATE_LOCATION": "/.apps_data/chat",
        })
    )
    return image


def wait_for_health(url: str, timeout: int = 180) -> bool:
    """Wait for environment to be healthy."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"{url}/health", timeout=10)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def zip_to_tar_gz(zip_path: Path, strip_prefix: str = "filesystem/") -> Path:
    """Convert zip to tar.gz for environment population."""
    tar_gz_path = zip_path.with_suffix(".tar.gz")
    with zipfile.ZipFile(zip_path, "r") as zf:
        with tarfile.open(tar_gz_path, "w:gz") as tar:
            for name in zf.namelist():
                new_name = name
                if strip_prefix and name.startswith(strip_prefix):
                    new_name = name[len(strip_prefix):]
                if not new_name:
                    continue
                info = tarfile.TarInfo(name=new_name)
                if name.endswith("/"):
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    tar.addfile(info)
                else:
                    data = zf.read(name)
                    info.size = len(data)
                    info.mode = 0o644
                    tar.addfile(info, io.BytesIO(data))
    return tar_gz_path


def tar_gz_to_zip(tar_gz_path: Path) -> Path:
    """Convert tar.gz to zip for grading."""
    stem = tar_gz_path.stem
    if stem.endswith(".tar"):
        stem = stem[:-4]
    zip_path = tar_gz_path.parent / f"{stem}.zip"
    with tarfile.open(tar_gz_path, "r:gz") as tar:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f is not None:
                        zf.writestr(member.name, f.read())
    return zip_path


def main():
    trajectory_id = f"modal_{uuid.uuid4().hex[:8]}"
    grading_run_id = f"gr_{uuid.uuid4().hex[:8]}"

    log("=" * 60)
    log("SIMPLE TASK EXAMPLE (Modal Sandbox)")
    log("=" * 60)
    log(f"Trajectory ID: {trajectory_id}")
    log(f"vLLM URL: {VLLM_URL}")
    log(f"Model: {VLLM_MODEL}")

    # Step 1: Build image and create sandbox
    image = build_modal_image()
    app = modal.App.lookup("archipelago-env", create_if_missing=True)

    log("Creating Modal sandbox...")
    sandbox = modal.Sandbox.create(
        "uv", "run", "uvicorn", "runner.main:app",
        "--host", "0.0.0.0", "--port", "8080",
        app=app,
        image=image,
        encrypted_ports=[8080],
        timeout=30 * 60,  # 30 minutes
    )

    try:
        sandbox_id = sandbox.object_id
        log(f"Sandbox created: {sandbox_id}")

        # Step 2: Get tunnel URL
        tunnel = sandbox.tunnels()[8080]
        env_url = tunnel.url
        log(f"Environment URL: {env_url}")

        # Step 3: Wait for health
        log("Waiting for environment to be healthy...")
        if not wait_for_health(env_url):
            log("ERROR: Environment failed to start in Modal sandbox")
            # Check sandbox logs
            proc = sandbox.exec("cat", "/tmp/uvicorn.log")
            log(f"Sandbox output: {proc.stdout.read()[:2000]}")
            sys.exit(1)
        log("Environment is healthy!")

        # Step 4: Populate world snapshot
        log("Populating environment with world snapshot...")
        original_zip = SCRIPT_DIR / "original_snapshot.zip"
        world_tar_gz = zip_to_tar_gz(original_zip)

        with open(world_tar_gz, "rb") as f:
            resp = requests.post(
                f"{env_url}/data/populate",
                files={"archive": ("world.tar.gz", f, "application/gzip")},
                params={"subsystem": "filesystem"},
                timeout=60,
            )
            if resp.status_code != 200:
                log(f"ERROR: Failed to populate: {resp.status_code} {resp.text}")
                sys.exit(1)
            log(f"Populated: {resp.json()}")

        # Step 5: Configure MCP servers (using paths inside the sandbox)
        log("Configuring MCP servers...")
        mcp_config = {
            "mcpServers": {
                "filesystem_server": {
                    "transport": "stdio",
                    "command": "uv",
                    "args": ["run", "python", "main.py"],
                    "cwd": "/app/mcp_servers/filesystem/mcp_servers/filesystem_server",
                    "env": {
                        "APP_FS_ROOT": "/filesystem",
                        "SERVER_NAME": "filesystem_server",
                        "MCP_TRANSPORT": "stdio",
                    },
                }
            }
        }
        resp = requests.post(f"{env_url}/apps", json=mcp_config, timeout=120)
        resp.raise_for_status()
        log(f"MCP servers configured: {resp.json()}")

        # Step 6: Run agent locally
        log("Running agent...")

        # Write orchestrator extra args to temp file
        extra_args = {"api_base": VLLM_URL, "api_key": "dummy"}
        extra_args_file = SCRIPT_DIR / "orchestrator_extra_args.json"
        with open(extra_args_file, "w") as f:
            json.dump(extra_args, f)

        agent_cmd = [
            "uv", "run", "python", "-m", "runner.main",
            "--trajectory-id", trajectory_id,
            "--initial-messages", str(SCRIPT_DIR / "initial_messages.json"),
            "--mcp-gateway-url", f"{env_url}/mcp/",
            "--agent-config", str(SCRIPT_DIR / "agent_config.json"),
            "--orchestrator-model", VLLM_MODEL,
            "--orchestrator-extra-args", str(extra_args_file),
            "--output", str(SCRIPT_DIR / "trajectory.json"),
        ]

        env_vars = os.environ.copy()
        env_vars["OPENAI_API_KEY"] = "dummy"
        env_vars["OPENAI_API_BASE"] = VLLM_URL

        result = subprocess.run(agent_cmd, cwd=AGENTS_DIR, env=env_vars)
        if result.returncode != 0:
            log(f"WARNING: Agent exited with code {result.returncode}")

        # Check agent status
        trajectory_file = SCRIPT_DIR / "trajectory.json"
        agent_status = None
        if trajectory_file.exists():
            with open(trajectory_file) as f:
                trajectory = json.load(f)
                agent_status = trajectory.get("status")
                log(f"Agent status: {agent_status}")

        # Step 7: Save final snapshot
        log("Saving final snapshot from Modal sandbox...")
        resp = requests.post(f"{env_url}/data/snapshot", stream=True, timeout=120)
        resp.raise_for_status()

        final_tar_gz = SCRIPT_DIR / "final_snapshot.tar.gz"
        with open(final_tar_gz, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)

        final_zip = tar_gz_to_zip(final_tar_gz)
        log(f"Saved: {final_zip}")

        # Step 8: Run grading locally
        if agent_status != "completed":
            log(f"Skipping grading (agent status: {agent_status})")
        else:
            log("Running grading...")
            grading_cmd = [
                "uv", "run", "python", "-m", "runner.main",
                "--grading-run-id", grading_run_id,
                "--trajectory-id", trajectory_id,
                "--initial-snapshot", str(SCRIPT_DIR / "original_snapshot.zip"),
                "--final-snapshot", str(final_zip),
                "--trajectory", str(trajectory_file),
                "--grading-settings", str(SCRIPT_DIR / "grading_settings.json"),
                "--verifiers", str(SCRIPT_DIR / "verifiers.json"),
                "--eval-configs", str(SCRIPT_DIR / "eval_configs.json"),
                "--scoring-config", str(SCRIPT_DIR / "scoring_config.json"),
                "--output", str(SCRIPT_DIR / "grades.json"),
            ]

            result = subprocess.run(grading_cmd, cwd=GRADING_DIR, env=env_vars)
            if result.returncode != 0:
                log(f"WARNING: Grading exited with code {result.returncode}")

            grades_file = SCRIPT_DIR / "grades.json"
            if grades_file.exists():
                with open(grades_file) as f:
                    grades = json.load(f)
                log("=" * 60)
                log("GRADING RESULTS")
                log("=" * 60)
                log(f"Status: {grades.get('grading_run_status')}")
                log(f"Final Score: {grades.get('scoring_results', {}).get('final_score')}")
                for vr in grades.get("verifier_results", []):
                    log(f"  - {vr.get('verifier_id')}: {vr.get('score')}")

    finally:
        log("Terminating Modal sandbox...")
        sandbox.terminate()
        log("Sandbox terminated.")

    log("=" * 60)
    log("DONE")
    log(f"Output: {SCRIPT_DIR}")
    log("=" * 60)


if __name__ == "__main__":
    main()
