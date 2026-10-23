import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

import xstream_probe as xp


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def fake_urlopen_factory(body: bytes, calls=None):
    def fake_urlopen(request, timeout=None):
        if calls is not None:
            calls.append((request, timeout))
        return FakeResponse(body)

    return fake_urlopen


def test_normalize_url():
    assert xp.normalize_url("  http://example.com///  ") == "http://example.com"
    assert xp.normalize_url('"http://example.com/"') == "http://example.com"


def test_build_api_url_encodes_credentials():
    url = xp.build_api_url("http://example.com", "user name", "p&ss=word")
    parts = urlsplit(url)
    query = parse_qs(parts.query)

    assert parts.path == "/player_api.php"
    assert query["username"] == ["user name"]
    assert query["password"] == ["p&ss=word"]


def test_load_urls_from_text(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text(
        "\n".join(
            [
                "# comment",
                "",
                "http://example.com/",
                "  http://EXAMPLE.com  ",
                '"https://other.example:8080"',
                "not-a-url",
                "ftp://example.com",
            ]
        ),
        encoding="utf-8",
    )

    assert xp.load_urls(path) == [
        "http://example.com",
        "https://other.example:8080",
    ]


def test_load_urls_from_csv_with_header(tmp_path):
    path = tmp_path / "urls.csv"
    path.write_text(
        "URL,notes\n"
        '"http://example.com/",primary\n'
        "http://backup.example.net,backup\n"
        "http://example.com,duplicate\n",
        encoding="utf-8",
    )

    assert xp.load_urls(path) == [
        "http://example.com",
        "http://backup.example.net",
    ]


def test_load_urls_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        xp.load_urls(tmp_path / "missing.txt")


def test_fetch_player_api_success(monkeypatch):
    body = json.dumps({"user_info": {"auth": 1}}).encode()
    calls = []
    monkeypatch.setattr(xp, "urlopen", fake_urlopen_factory(body, calls))

    payload, error = xp.fetch_player_api(
        "http://example.com", "user", "pass", 5
    )

    assert error is None
    assert payload == {"user_info": {"auth": 1}}
    request, timeout = calls[0]
    assert timeout == 5
    assert "username=user" in request.full_url
    assert request.get_header("User-agent") == xp.USER_AGENT


def test_fetch_player_api_http_error(monkeypatch):
    def raise_http_error(request, timeout=None):
        raise HTTPError(request.full_url, 403, "Forbidden", None, None)

    monkeypatch.setattr(xp, "urlopen", raise_http_error)

    payload, error = xp.fetch_player_api(
        "http://example.com", "user", "pass", 5
    )

    assert payload is None
    assert error == "HTTP 403"


def test_fetch_player_api_timeout(monkeypatch):
    def raise_timeout(request, timeout=None):
        raise URLError(TimeoutError("timed out"))

    monkeypatch.setattr(xp, "urlopen", raise_timeout)

    payload, error = xp.fetch_player_api(
        "http://example.com", "user", "pass", 5
    )

    assert payload is None
    assert error == "timed out"


def test_fetch_player_api_invalid_json(monkeypatch):
    monkeypatch.setattr(xp, "urlopen", fake_urlopen_factory(b"<html>oops"))

    payload, error = xp.fetch_player_api(
        "http://example.com", "user", "pass", 5
    )

    assert payload is None
    assert error == "invalid JSON response"


def test_format_expiry():
    assert xp.format_expiry(None) is None
    assert xp.format_expiry("null") is None
    assert xp.format_expiry(0) is None
    assert xp.format_expiry("-1") is None
    assert xp.format_expiry("abc") is None
    assert xp.format_expiry("1700000000") == (
        datetime.fromtimestamp(1700000000).date().isoformat()
    )


def test_evaluate_active_account():
    result = xp.evaluate_response(
        "http://example.com",
        {
            "user_info": {
                "auth": 1,
                "status": "Active",
                "exp_date": "1700000000",
            }
        },
    )

    assert result.status == "ok"
    assert result.auth == "1"
    assert result.account_status == "Active"
    assert result.expires == datetime.fromtimestamp(1700000000).date().isoformat()


def test_evaluate_auth_failure():
    result = xp.evaluate_response(
        "http://example.com",
        {"user_info": {"auth": 0, "status": "Disabled"}},
    )

    assert result.status == "auth-fail"
    assert result.auth == "0"
    assert result.account_status == "Disabled"


def test_evaluate_inactive_account():
    result = xp.evaluate_response(
        "http://example.com",
        {"user_info": {"auth": 1, "status": "Expired"}},
    )

    assert result.status == "auth-fail"
    assert result.account_status == "Expired"


def test_evaluate_down_and_unknown():
    down = xp.evaluate_response("http://example.com", None, "timed out")
    assert down.status == "down"
    assert down.error == "timed out"

    unknown = xp.evaluate_response("http://example.com", {"user_info": "nope"})
    assert unknown.status == "unknown"
    assert unknown.error == "missing user_info"


def test_write_working_urls_deduplicates(tmp_path):
    output = tmp_path / "nested" / "working.txt"
    results = [
        xp.CheckResult(url="http://one.example", status="ok"),
        xp.CheckResult(url="http://two.example", status="auth-fail"),
        xp.CheckResult(url="HTTP://ONE.example", status="ok"),
        xp.CheckResult(url="http://three.example", status="down"),
    ]

    written = xp.write_working_urls(output, results)

    assert written == ["http://one.example"]
    assert output.read_text(encoding="utf-8") == "http://one.example\n"


def test_main_success(tmp_path, monkeypatch, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text(
        "http://one.example\nhttp://two.example\nhttp://one.example/\n",
        encoding="utf-8",
    )
    output = tmp_path / "working.csv"

    monkeypatch.setattr(
        xp,
        "fetch_player_api",
        lambda base, user, password, timeout: (
            {"user_info": {"auth": 1, "status": "Active"}},
            None,
        ),
    )

    exit_code = xp.main(
        [
            "--username",
            "user",
            "--password",
            "pass",
            "--urls",
            str(urls),
            "--output",
            str(output),
            "--no-color",
        ]
    )

    assert exit_code == 0
    assert output.read_text(encoding="utf-8") == (
        "http://one.example\nhttp://two.example\n"
    )
    captured = capsys.readouterr()
    assert "2/2 servers OK" in captured.out
    assert "[OK]" in captured.out


def test_main_auth_failure_returns_one(tmp_path, monkeypatch, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text("http://one.example\n", encoding="utf-8")
    output = tmp_path / "working.csv"

    monkeypatch.setattr(
        xp,
        "fetch_player_api",
        lambda base, user, password, timeout: (
            {"user_info": {"auth": 0, "status": "Disabled"}},
            None,
        ),
    )

    exit_code = xp.main(
        [
            "--username",
            "user",
            "--password",
            "pass",
            "--urls",
            str(urls),
            "--output",
            str(output),
            "--no-color",
        ]
    )

    assert exit_code == 1
    assert output.read_text(encoding="utf-8") == ""
    captured = capsys.readouterr()
    assert "[AUTH-FAIL]" in captured.out
    assert "0/1 servers OK" in captured.out


def test_main_rejects_empty_url_list(tmp_path, capsys):
    urls = tmp_path / "urls.txt"
    urls.write_text("# only a comment\n", encoding="utf-8")

    exit_code = xp.main(
        [
            "--username",
            "user",
            "--password",
            "pass",
            "--urls",
            str(urls),
            "--output",
            str(tmp_path / "out.csv"),
        ]
    )

    assert exit_code == 2
    assert "no valid URLs" in capsys.readouterr().err


def test_format_result_without_color():
    result = xp.CheckResult(
        url="http://example.com",
        status="ok",
        account_status="Active",
        expires="2027-02-18",
    )

    line = xp.format_result(result, use_color=False)

    assert line.startswith("[OK]")
    assert "(Active, expires 2027-02-18)" in line
    assert "\033[" not in line
