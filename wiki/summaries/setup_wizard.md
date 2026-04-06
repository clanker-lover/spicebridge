# setup_wizard.py

**Source:** `src/spicebridge/setup_wizard.py`

## Purpose

Interactive setup wizard for deploying SPICEBridge with a Cloudflare tunnel. Handles cloudflared installation, authentication, tunnel creation/management, config generation, and process lifecycle.

## Public API

- **`run_wizard(argv=None)`**: Main entry point. Returns exit code. Parses args (`--quick`, `--port`, `--host`, `--tunnel-name`, `--domain`, `--no-install`), then runs either quick tunnel or named tunnel flow.

## Wizard Flows

**Quick tunnel**: No Cloudflare account needed. Starts server + `cloudflared tunnel --url` for a temporary `trycloudflare.com` URL. Auto-generates API key.

**Named tunnel**: Requires Cloudflare account. Authenticates, creates/reuses tunnel, configures DNS routing, generates `~/.cloudflared/config.yml`, starts server + named tunnel. Permanent custom domain URL.

## Security

- Validates hostnames, tunnel names, and tunnel IDs with strict regexes.
- Strips unsafe env vars (`PYTHONSTARTUP`, `PYTHONPATH`, `LD_PRELOAD`, `LD_LIBRARY_PATH`, `NODE_OPTIONS`) from subprocess environment.
- Config files written atomically with 0o600 permissions.
- Existing config backed up before overwrite.

## Process Management

`_run_processes()` blocks until Ctrl+C, then cleanly terminates both server and tunnel processes. `_kill_proc()` does terminate -> wait(5s) -> kill -> wait.

## Dependencies

`subprocess`, `argparse`, `secrets`, `urllib.request`, `fcntl` (Unix-only for quick tunnel stderr parsing).

## Architecture Role

Deployment tool. Invoked via `spicebridge setup-cloud` from [__main__.py](__main__.md). See [cloud-deployment](../concepts/cloud-deployment.md).
