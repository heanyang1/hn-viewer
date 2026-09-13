"""Tests for the command line interface."""
import ipaddress

import flask

import app as app_module
from app import _primary_lan_ip, main


def run_and_capture(monkeypatch, argv):
    """Run main() with app.run patched out; return its kwargs and stdout."""
    captured = {}

    def fake_run(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(flask.Flask, "run", fake_run)
    main(argv)
    return captured


def test_defaults_are_loopback_with_debug(monkeypatch, capsys):
    kwargs = run_and_capture(monkeypatch, [])
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 5000
    assert kwargs["debug"] is True
    out = capsys.readouterr().out
    assert "http://127.0.0.1:5000" in out
    assert "Network:" not in out  # not reachable from other devices


def test_public_binds_all_interfaces_and_disables_debug(monkeypatch, capsys):
    kwargs = run_and_capture(monkeypatch, ["--public"])
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["debug"] is False  # auto-disabled on non-loopback binds
    out = capsys.readouterr().out
    assert "Network:" in out
    assert f"http://{_primary_lan_ip()}:5000" in out


def test_host_0_0_0_0_is_same_as_public(monkeypatch, capsys):
    kwargs = run_and_capture(monkeypatch, ["--host", "0.0.0.0"])
    assert kwargs["host"] == "0.0.0.0"
    assert "Network:" in capsys.readouterr().out


def test_explicit_host_and_port(monkeypatch):
    kwargs = run_and_capture(
        monkeypatch, ["--host", "192.168.1.10", "--port", "8080"]
    )
    assert kwargs["host"] == "192.168.1.10"
    assert kwargs["port"] == 8080
    assert kwargs["debug"] is False  # auto-disabled for non-loopback host


def test_debug_flags_override_auto_detection(monkeypatch):
    kwargs = run_and_capture(monkeypatch, ["--public", "--debug"])
    assert kwargs["debug"] is True

    kwargs = run_and_capture(monkeypatch, ["--no-debug"])
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["debug"] is False


def test_debug_flags_are_mutually_exclusive(capsys):
    try:
        main(["--debug", "--no-debug"])
    except SystemExit as exc:
        assert exc.code == 2  # argparse usage error
    else:
        raise AssertionError("expected SystemExit")


def test_primary_lan_ip_returns_valid_address():
    ipaddress.ip_address(_primary_lan_ip())  # raises if not an IP


def test_public_with_ip_restricts_allowlist(monkeypatch, capsys):
    monkeypatch.setattr(app_module, "ALLOWED_CLIENTS", None)  # restore after test
    kwargs = run_and_capture(monkeypatch, ["--public", "192.168.1.50"])
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["debug"] is False
    assert app_module.ALLOWED_CLIENTS == frozenset(
        {ipaddress.ip_address("192.168.1.50")}
    )
    out = capsys.readouterr().out
    assert "restricted to localhost and 192.168.1.50" in out


def test_public_with_invalid_ip_is_a_usage_error(capsys):
    try:
        main(["--public", "999.1.2.3"])
    except SystemExit as exc:
        assert exc.code == 2  # argparse usage error
        assert "not a valid IP address" in capsys.readouterr().err
    else:
        raise AssertionError("expected SystemExit")


def test_bare_public_accepts_all_requests(monkeypatch):
    monkeypatch.setattr(app_module, "ALLOWED_CLIENTS", None)
    kwargs = run_and_capture(monkeypatch, ["--public"])
    assert kwargs["host"] == "0.0.0.0"
    assert app_module.ALLOWED_CLIENTS is None  # no filtering


def get(client, path, remote_addr):
    return client.get(path, environ_base={"REMOTE_ADDR": remote_addr})


def test_requests_allowed_without_public_ip(client):
    assert get(client, "/stored", "203.0.113.7").status_code == 200


def test_allowlist_permits_localhost_and_listed_ip(client, monkeypatch):
    monkeypatch.setattr(
        app_module, "ALLOWED_CLIENTS", frozenset({ipaddress.ip_address("192.168.1.50")})
    )
    assert get(client, "/stored", "127.0.0.1").status_code == 200
    assert get(client, "/stored", "::1").status_code == 200
    assert get(client, "/stored", "192.168.1.50").status_code == 200


def test_allowlist_rejects_other_addresses(client, monkeypatch):
    monkeypatch.setattr(
        app_module, "ALLOWED_CLIENTS", frozenset({ipaddress.ip_address("192.168.1.50")})
    )
    resp = get(client, "/stored", "203.0.113.7")
    assert resp.status_code == 403
    assert b"192.168.1.50" in resp.data
    # the API is blocked too
    assert get(client, "/api/stored", "203.0.113.7").status_code == 403
