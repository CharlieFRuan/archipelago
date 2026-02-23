#!/usr/bin/env python3
"""
Run the simple_task example end-to-end.

The environment (MCP gateway) runs in a Modal Sandbox.
The agent and grading run locally, connecting to the sandbox via tunnel URL.
The LLM (vLLM) runs locally.

Usage:
    cd archipelago/agents
    uv run python ../examples/simple_task/main.py

Prerequisites:
    - Modal credentials (MODAL_TOKEN_ID, MODAL_TOKEN_SECRET)
    - vLLM server running locally
    - uv installed, agents and grading deps installed (uv sync)
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

# Paths - can be overridden via environment variables
EXAMPLE_DIR = Path(os.environ.get("EXAMPLE_DIR", Path(__file__).parent))
ARCHIPELAGO_DIR = Path(os.environ.get("ARCHIPELAGO_DIR", EXAMPLE_DIR.parent.parent))
ENVIRONMENT_DIR = Path(
    os.environ.get("ENVIRONMENT_DIR", ARCHIPELAGO_DIR / "environment")
)
AGENTS_DIR = Path(os.environ.get("AGENTS_DIR", ARCHIPELAGO_DIR / "agents"))
GRADING_DIR = Path(os.environ.get("GRADING_DIR", ARCHIPELAGO_DIR / "grading"))
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
        .add_local_dir(
            str(ENVIRONMENT_DIR),
            remote_path="/app",
            ignore=_ignore_filter,
            copy=True,
        )
        .add_local_dir(
            str(MCP_SERVERS_DIR / "filesystem"),
            remote_path="/app/mcp_servers/filesystem",
            ignore=_ignore_filter,
            copy=True,
        )
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


def start_environment() -> tuple[modal.Sandbox, str]:
    """Start the environment in a Modal Sandbox. Returns (sandbox, url)."""
    image = build_modal_image()
    app = modal.App.lookup("archipelago-env", create_if_missing=True)

    log("Creating Modal sandbox...")
    sandbox = modal.Sandbox.create(
        "uv", "run", "uvicorn", "runner.main:app",
        "--host", "0.0.0.0", "--port", "8080",
        app=app,
        image=image,
        encrypted_ports=[8080],
        timeout=30 * 60,
    )

    log(f"Sandbox created: {sandbox.object_id}")

    tunnel = sandbox.tunnels()[8080]
    env_url = tunnel.url
    log(f"Environment URL: {env_url}")

    log("Waiting for environment to be healthy...")
    if not wait_for_health(env_url):
        log("ERROR: Environment failed to start in Modal sandbox")
        sandbox.terminate()
        sys.exit(1)
    log("Environment is healthy!")

    return sandbox, env_url


def zip_to_tar_gz(zip_path: Path, strip_prefix: str = "filesystem/") -> Path:
    """Convert zip to tar.gz for environment population.

    Strips the prefix (e.g., 'filesystem/') since the subsystem parameter
    already specifies the target directory.
    """
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
    trajectory_id = f"example_{uuid.uuid4().hex[:8]}"
    grading_run_id = f"gr_{uuid.uuid4().hex[:8]}"

    log("=" * 60)
    log("SIMPLE TASK EXAMPLE (Modal Sandbox)")
    log("=" * 60)
    log(f"Trajectory ID: {trajectory_id}")
    log(f"vLLM URL: {VLLM_URL}")
    log(f"Model: {VLLM_MODEL}")

    # Start environment in Modal
    sandbox, env_url = start_environment()

    try:
        # Populate world snapshot
        log("Populating environment with world snapshot...")
        original_zip = EXAMPLE_DIR / "original_snapshot.zip"
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

        # Configure MCP servers
        log("Configuring MCP servers...")
        with open(EXAMPLE_DIR / "mcp_config.json") as f:
            mcp_config = json.load(f)

        resp = requests.post(f"{env_url}/apps", json=mcp_config, timeout=120)
        resp.raise_for_status()
        log(f"MCP servers configured: {resp.json()}")

        # Run agent
        log("Running agent...")
        with open(EXAMPLE_DIR / "orchestrator_config.json") as f:
            orchestrator_config = json.load(f)

        agent_cmd = [
            "uv", "run", "python", "-m", "runner.main",
            "--trajectory-id", trajectory_id,
            "--initial-messages", str(EXAMPLE_DIR / "initial_messages.json"),
            "--mcp-gateway-url", f"{env_url}/mcp/",
            "--agent-config", str(EXAMPLE_DIR / "agent_config.json"),
            "--orchestrator-model", orchestrator_config["model"],
            "--output", str(EXAMPLE_DIR / "trajectory.json"),
        ]

        if orchestrator_config.get("extra_args"):
            extra_args_file = EXAMPLE_DIR / "orchestrator_extra_args.json"
            with open(extra_args_file, "w") as f:
                json.dump(orchestrator_config["extra_args"], f)
            agent_cmd.extend(["--orchestrator-extra-args", str(extra_args_file)])

        env_vars = os.environ.copy()
        env_vars["OPENAI_API_KEY"] = "dummy"
        env_vars["OPENAI_API_BASE"] = VLLM_URL

        result = subprocess.run(agent_cmd, cwd=AGENTS_DIR, env=env_vars)
        if result.returncode != 0:
            log(f"WARNING: Agent exited with code {result.returncode}")

        # Check agent status
        trajectory_file = EXAMPLE_DIR / "trajectory.json"
        agent_status = None
        if trajectory_file.exists():
            with open(trajectory_file) as f:
                trajectory = json.load(f)
                agent_status = trajectory.get("status")
                log(f"Agent status: {agent_status}")

        # Save final snapshot
        log("Saving final snapshot...")
        resp = requests.post(f"{env_url}/data/snapshot", stream=True, timeout=120)
        resp.raise_for_status()

        final_tar_gz = EXAMPLE_DIR / "final_snapshot.tar.gz"
        with open(final_tar_gz, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)

        final_zip = tar_gz_to_zip(final_tar_gz)
        log(f"Saved: {final_zip}")

        # Run grading if agent completed
        if agent_status != "completed":
            log(f"Skipping grading (agent status: {agent_status})")
        else:
            log("Running grading...")
            grading_cmd = [
                "uv", "run", "python", "-m", "runner.main",
                "--grading-run-id", grading_run_id,
                "--trajectory-id", trajectory_id,
                "--initial-snapshot", str(EXAMPLE_DIR / "original_snapshot.zip"),
                "--final-snapshot", str(final_zip),
                "--trajectory", str(trajectory_file),
                "--grading-settings", str(EXAMPLE_DIR / "grading_settings.json"),
                "--verifiers", str(EXAMPLE_DIR / "verifiers.json"),
                "--eval-configs", str(EXAMPLE_DIR / "eval_configs.json"),
                "--scoring-config", str(EXAMPLE_DIR / "scoring_config.json"),
                "--output", str(EXAMPLE_DIR / "grades.json"),
            ]

            result = subprocess.run(grading_cmd, cwd=GRADING_DIR, env=env_vars)
            if result.returncode != 0:
                log(f"WARNING: Grading exited with code {result.returncode}")

            grades_file = EXAMPLE_DIR / "grades.json"
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
    log(f"Output: {EXAMPLE_DIR}")
    log("=" * 60)


if __name__ == "__main__":
    main()
