"""Tests for CLI argument handling in spicebridge.__main__."""

from __future__ import annotations

from unittest.mock import patch


def test_host_port_reach_mcp_settings():
    """--host and --port must propagate to mcp.settings before mcp.run()."""
    captured = {}

    def fake_run(transport):
        # Import here so we read the *current* mcp singleton
        from spicebridge.server import mcp

        captured["host"] = mcp.settings.host
        captured["port"] = mcp.settings.port
        captured["transport"] = transport

    with patch(
        "sys.argv",
        [
            "spicebridge",
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
            "--port",
            "9999",
        ],
    ):
        from spicebridge.server import mcp

        with patch.object(mcp, "run", side_effect=fake_run):
            from spicebridge.__main__ import main

            main()

    # Restore flag set by configure_for_remote() to avoid polluting other tests
    import spicebridge.server as _srv

    _srv._http_transport = False

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9999
    assert captured["transport"] == "streamable-http"


def test_host_port_reach_uvicorn_with_auth():
    """--host and --port must reach uvicorn.Config when API key is set."""
    captured = {}

    def fake_run_with_auth(mcp, transport, host, port, api_key):
        captured["host"] = host
        captured["port"] = port

    with (
        patch(
            "sys.argv",
            [
                "spicebridge",
                "--transport",
                "streamable-http",
                "--host",
                "0.0.0.0",
                "--port",
                "7777",
            ],
        ),
        patch.dict("os.environ", {"SPICEBRIDGE_API_KEY": "test-key-123"}),
        patch("spicebridge.__main__._run_with_auth", side_effect=fake_run_with_auth),
    ):
        from spicebridge.__main__ import main

        main()

    # Restore flag set by configure_for_remote() to avoid polluting other tests
    import spicebridge.server as _srv

    _srv._http_transport = False

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 7777
