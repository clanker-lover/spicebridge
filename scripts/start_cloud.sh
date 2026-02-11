#!/usr/bin/env bash
# Start SPICEBridge MCP server with a Cloudflare quick tunnel for cloud access.
#
# Usage:
#   ./scripts/start_cloud.sh              # default port 8000
#   PORT=9000 ./scripts/start_cloud.sh    # custom port
set -euo pipefail

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
TRANSPORT="${TRANSPORT:-sse}"

# --- Preflight checks ---

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: Virtual environment not found at $PROJECT_DIR/.venv"
    echo "  Run: python -m venv .venv && .venv/bin/pip install -e '.[dev]'"
    exit 1
fi

if ! command -v cloudflared &>/dev/null; then
    echo "ERROR: cloudflared not found on PATH"
    echo ""
    echo "Install options:"
    echo "  Debian/Ubuntu:  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null"
    echo "                  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main' | sudo tee /etc/apt/sources.list.d/cloudflared.list"
    echo "                  sudo apt update && sudo apt install cloudflared"
    echo ""
    echo "  Binary:         https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
fi

# --- Cleanup on exit ---

SERVER_PID=""
TUNNEL_PID=""

cleanup() {
    echo ""
    echo "Shutting down..."
    [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT INT TERM

# --- Start MCP server ---

echo "Starting SPICEBridge MCP server on $HOST:$PORT ($TRANSPORT)..."
FASTMCP_PORT="$PORT" FASTMCP_HOST="$HOST" "$VENV_PYTHON" -m spicebridge --transport "$TRANSPORT" &
SERVER_PID=$!

# Wait for server to be ready
echo "Waiting for server..."
for i in $(seq 1 30); do
    if curl -sf "http://$HOST:$PORT/sse" -o /dev/null --max-time 1 2>/dev/null; then
        echo "Server ready."
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "ERROR: Server process exited unexpectedly."
        exit 1
    fi
    sleep 0.5
done

# --- Start Cloudflare tunnel ---

echo "Starting Cloudflare tunnel..."
cloudflared tunnel --url "http://$HOST:$PORT" &
TUNNEL_PID=$!

# Give tunnel a moment to print its URL
sleep 3

echo ""
echo "========================================="
echo " SPICEBridge cloud MCP server is running"
echo "========================================="
echo ""
echo "Look above for your tunnel URL (*.trycloudflare.com)."
echo ""
echo "MCP client config (add to your client's settings):"
echo ""
echo "  {"
echo "    \"mcpServers\": {"
echo "      \"spicebridge\": {"
echo "        \"url\": \"https://<YOUR-TUNNEL-URL>/sse\""
echo "      }"
echo "    }"
echo "  }"
echo ""
echo "Press Ctrl+C to stop."
echo ""

# Wait for either process to exit
wait
