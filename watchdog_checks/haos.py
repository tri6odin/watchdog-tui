"""Home Assistant HTTP API checks."""

from __future__ import annotations

import subprocess


def check_http_endpoint(url: str, timeout_seconds: float, user_agent: str) -> bool:
    """Check that an HTTP endpoint returns a successful status."""

    hard_timeout = max(0.001, timeout_seconds)
    timeout_arg = f"{hard_timeout:.3f}"
    try:
        result = subprocess.run(
            [
                "curl",
                "--location",
                "--silent",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--max-time",
                timeout_arg,
                "--connect-timeout",
                timeout_arg,
                "--max-redirs",
                "5",
                "--user-agent",
                user_agent,
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=hard_timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    if result.returncode != 0:
        return False
    try:
        status = int(result.stdout.strip())
    except ValueError:
        return False
    return 200 <= status < 400


def join_url(base_url: str, path: str) -> str:
    """Join base URL and path without duplicate slashes."""

    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def curl_config_value(value: str) -> str:
    """Escape a string for curl config value."""

    return value.replace("\\", "\\\\").replace('"', '\\"')


def check_home_assistant_api(
    address: str,
    port: int,
    token: str,
    api_path: str,
    timeout_seconds: float,
    user_agent: str,
) -> bool:
    """Check Home Assistant API with Bearer token via curl stdin config."""

    if not token.strip():
        return False

    hard_timeout = max(0.001, timeout_seconds)
    timeout_arg = f"{hard_timeout:.3f}"
    url = join_url(f"http://{address}:{port}", api_path)
    curl_config = (
        f'header = "Authorization: Bearer {curl_config_value(token)}"\n'
        'header = "Accept: application/json"\n'
    )
    try:
        result = subprocess.run(
            [
                "curl",
                "--config",
                "-",
                "--silent",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--max-time",
                timeout_arg,
                "--connect-timeout",
                timeout_arg,
                "--user-agent",
                user_agent,
                url,
            ],
            input=curl_config,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=hard_timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    if result.returncode != 0:
        return False
    try:
        status = int(result.stdout.strip())
    except ValueError:
        return False
    return status == 200
