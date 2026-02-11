#!/usr/bin/env bash
# Start SPICEBridge MCP server with a named Cloudflare tunnel for cloud access.
#
# Permanent URL: https://spicebridge.clanker-lover.work/mcp
#
# Usage:
#   ./start_cloud.sh              # default port 8000
#   PORT=9000 ./start_cloud.sh    # custom port
set -euo pipefail

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
TRANSPORT="${TRANSPORT:-streamable-http}"

# --- Preflight checks ---

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
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

# --- API key generation ---

if [ -z "${SPICEBRIDGE_API_KEY:-}" ]; then
    SPICEBRIDGE_API_KEY=$("$VENV_PYTHON" -c "import secrets; print(secrets.token_urlsafe(32))")
    echo "Generated API key: $SPICEBRIDGE_API_KEY"
fi
export SPICEBRIDGE_API_KEY

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
    if curl -sf -H "Authorization: Bearer $SPICEBRIDGE_API_KEY" "http://$HOST:$PORT/mcp" -o /dev/null --max-time 1 2>/dev/null; then
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

echo "Starting Cloudflare named tunnel..."
cloudflared tunnel run spicebridge &
TUNNEL_PID=$!

sleep 3

TUNNEL_URL="https://spicebridge.clanker-lover.work"

echo ""
echo "========================================="
echo " SPICEBridge cloud MCP server is running"
echo "========================================="
echo ""
echo "Permanent URL: $TUNNEL_URL/mcp"
echo "API Key:       $SPICEBRIDGE_API_KEY"
echo ""
echo "MCP client config (add to your client's settings):"
echo ""
echo "  {"
echo "    \"mcpServers\": {"
echo "      \"spicebridge\": {"
echo "        \"url\": \"$TUNNEL_URL/mcp\","
echo "        \"headers\": {"
echo "          \"Authorization\": \"Bearer $SPICEBRIDGE_API_KEY\""
echo "        }"
echo "      }"
echo "    }"
echo "  }"
echo ""
echo "Press Ctrl+C to stop."
echo ""

# Wait for either process to exit
wait
