"""NAS/SMB checks."""

from __future__ import annotations

import os
import subprocess


def smb_share_is_listed(smb_output: str, share_name: str) -> bool:
    """Check that smbclient output contains the expected share."""

    expected = share_name.strip().lower()
    for line in smb_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "|" in stripped:
            columns = [column.strip().lower() for column in stripped.split("|")]
            if expected in columns:
                return True
        else:
            columns = stripped.split()
            if columns and columns[0].lower() == expected:
                return True
    return False


def check_smb_share_linux(
    host: str,
    user: str,
    password: str,
    share: str,
    timeout_seconds: float,
) -> bool:
    """Check SMB server availability and expected share visibility."""

    hard_timeout = max(0.001, timeout_seconds)
    env = os.environ.copy()
    env["PASSWD"] = password
    try:
        result = subprocess.run(
            [
                "smbclient",
                "-L",
                f"//{host}",
                "-U",
                user,
                "-g",
            ],
            env=env,
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
    return smb_share_is_listed(result.stdout, share)
