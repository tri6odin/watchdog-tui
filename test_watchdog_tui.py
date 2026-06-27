import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from watchdog_config import (
    GlobalConfig,
    HaosConfig,
    NasConfig,
    PingConfig,
    TlsConfig,
    WatchdogConfig,
    load_watchdog_config,
)
from watchdog_core import (
    CHECK_ORDER,
    DISPLAY_ROWS,
    Screen,
    Status,
    TEN_MINUTES,
    aggregate_statuses,
    align_timestamp,
    check_haos,
    check_home_assistant_api,
    check_INT_net,
    check_http_endpoint,
    check_lcl_net,
    check_nas,
    check_smb_share_macos,
    check_tcp_port,
    check_tls_handshake,
    check_vpn,
    configure_checks,
    ping_host_macos,
    run_service_checks,
    seconds_until_next_history_bucket,
    smb_share_is_listed,
)
from watchdog_db import WatchdogDB


def make_test_config() -> WatchdogConfig:
    return WatchdogConfig(
        global_config=GlobalConfig(http_user_agent="watchdog-test/1.0"),
        lcl_net=PingConfig(address="192.168.50.1", timeout_seconds=1.0),
        int_net=TlsConfig(host="ya.ru", port=443, timeout_seconds=1.0),
        vpn=TlsConfig(host="instagram.com", port=443, timeout_seconds=5.0),
        haos=HaosConfig(
            address="192.168.50.4",
            port=8123,
            api_path="/api/config",
            timeout_seconds=2.0,
            token="ha-token",
        ),
        nas=NasConfig(
            address="192.168.50.3",
            port=445,
            tcp_timeout_seconds=1.0,
            smb_timeout_seconds=5.0,
            smb_user="watchdog",
            smb_pass="test-smb-pass",
            smb_share="BACKUP",
        ),
    )


class WatchdogLogicTest(unittest.TestCase):
    def test_config_file_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "watchdog.conf"
            config_path.write_text(
                """
[global]
http_user_agent = watchdog-test/1.0

[lcl_net]
address = 192.168.50.1
timeout_seconds = 1.0

[int_net]
host = ya.ru
port = 443
timeout_seconds = 1.0

[vpn]
host = instagram.com
port = 443
timeout_seconds = 5.0

[haos]
address = 192.168.50.4
port = 8123
api_path = /api/config
timeout_seconds = 2.0
token = test-token

[nas]
address = 192.168.50.3
port = 445
tcp_timeout_seconds = 1.0
smb_timeout_seconds = 5.0
smb_user = watchdog
smb_pass = test-pass
smb_share = BACKUP
""",
                encoding="utf-8",
            )

            config = load_watchdog_config(config_path)

        self.assertEqual(config.global_config.http_user_agent, "watchdog-test/1.0")
        self.assertEqual(config.lcl_net.address, "192.168.50.1")
        self.assertEqual(config.int_net.host, "ya.ru")
        self.assertEqual(config.int_net.port, 443)
        self.assertEqual(config.vpn.host, "instagram.com")
        self.assertEqual(config.vpn.port, 443)
        self.assertEqual(config.haos.address, "192.168.50.4")
        self.assertEqual(config.haos.port, 8123)
        self.assertEqual(config.haos.token, "test-token")
        self.assertEqual(config.nas.smb_share, "BACKUP")

    def test_aggregate_priority(self):
        self.assertEqual(aggregate_statuses([]), Status.NO_DATA)
        self.assertEqual(aggregate_statuses([Status.OK, Status.OK]), Status.OK)
        self.assertEqual(aggregate_statuses([Status.OK, Status.UNKNOWN]), Status.UNKNOWN)
        self.assertEqual(aggregate_statuses([Status.OK, Status.FAIL, Status.UNKNOWN]), Status.FAIL)

    def test_service_order_matches_watchdog_layout(self):
        expected_order = [
            "LCL NET",
            "INT NET",
            "VPN",
            "WIFI",
            "IOT WIFI",
            "NAS",
            "HAOS",
            "ZIGBEE",
        ]

        display_order = [row[0] for row in DISPLAY_ROWS] + [row[1] for row in DISPLAY_ROWS]

        self.assertEqual(CHECK_ORDER, expected_order)
        self.assertEqual(display_order, expected_order)

    def test_lcl_net_failure_makes_everything_else_unknown(self):
        checkers = {service: (lambda: True) for service in CHECK_ORDER}
        checkers["LCL NET"] = lambda: False

        results = run_service_checks(checkers=checkers, step_delay_seconds=0)

        self.assertEqual(results["LCL NET"], Status.FAIL)
        for service in CHECK_ORDER[1:]:
            self.assertEqual(results[service], Status.UNKNOWN)

    def test_int_net_failure_does_not_skip_vpn(self):
        checkers = {service: (lambda: True) for service in CHECK_ORDER}
        checkers["INT NET"] = lambda: False

        results = run_service_checks(checkers=checkers, step_delay_seconds=0)

        self.assertEqual(results["INT NET"], Status.FAIL)
        self.assertEqual(results["VPN"], Status.OK)
        self.assertEqual(results["HAOS"], Status.OK)
        self.assertEqual(results["ZIGBEE"], Status.OK)

    def test_haos_failure_does_not_skip_zigbee(self):
        checkers = {service: (lambda: True) for service in CHECK_ORDER}
        checkers["HAOS"] = lambda: False

        results = run_service_checks(checkers=checkers, step_delay_seconds=0)

        self.assertEqual(results["HAOS"], Status.FAIL)
        self.assertEqual(results["ZIGBEE"], Status.OK)
        self.assertEqual(results["WIFI"], Status.OK)

    def test_sqlite_history_uses_empty_cells_for_missing_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WatchdogDB(Path(tmpdir) / "watchdog.sqlite3")
            now = align_timestamp(1_800_000_000, TEN_MINUTES)
            db.save_results({"LCL NET": Status.OK}, checked_at=now)

            history = db.load_history(Screen.HOURS, now=now)

            self.assertEqual(history["LCL NET"][-1], Status.OK)
            self.assertEqual(history["LCL NET"][0], Status.NO_DATA)
            db.close()

    def test_sqlite_keeps_fail_when_same_bucket_is_rechecked_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WatchdogDB(Path(tmpdir) / "watchdog.sqlite3")
            now = align_timestamp(1_800_000_000, TEN_MINUTES)

            db.save_results({"LCL NET": Status.FAIL}, checked_at=now)
            db.save_results({"LCL NET": Status.OK}, checked_at=now + 30)
            history = db.load_history(Screen.HOURS, now=now)

            self.assertEqual(history["LCL NET"][-1], Status.FAIL)
            db.close()

    def test_sqlite_keeps_unknown_when_same_bucket_is_rechecked_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WatchdogDB(Path(tmpdir) / "watchdog.sqlite3")
            now = align_timestamp(1_800_000_000, TEN_MINUTES)

            db.save_results({"VPN": Status.UNKNOWN}, checked_at=now)
            db.save_results({"VPN": Status.OK}, checked_at=now + 30)
            history = db.load_history(Screen.HOURS, now=now)

            self.assertEqual(history["VPN"][-1], Status.UNKNOWN)
            db.close()

    def test_hours_history_shifts_and_new_bucket_is_filled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = WatchdogDB(Path(tmpdir) / "watchdog.sqlite3")
            first_bucket = align_timestamp(1_800_000_000, TEN_MINUTES)
            second_bucket = first_bucket + TEN_MINUTES

            db.save_results({"LCL NET": Status.OK}, checked_at=first_bucket)
            before_new_check = db.load_history(Screen.HOURS, now=second_bucket)
            self.assertEqual(before_new_check["LCL NET"][-2], Status.OK)
            self.assertEqual(before_new_check["LCL NET"][-1], Status.NO_DATA)

            db.save_results({"LCL NET": Status.FAIL}, checked_at=second_bucket)
            after_new_check = db.load_history(Screen.HOURS, now=second_bucket)
            self.assertEqual(after_new_check["LCL NET"][-2], Status.OK)
            self.assertEqual(after_new_check["LCL NET"][-1], Status.FAIL)
            db.close()

    def test_default_auto_update_aligns_to_next_history_bucket(self):
        bucket_start = align_timestamp(1_800_000_000, TEN_MINUTES)

        self.assertEqual(seconds_until_next_history_bucket(bucket_start), TEN_MINUTES)
        self.assertEqual(seconds_until_next_history_bucket(bucket_start + 125), TEN_MINUTES - 125)

    def test_ping_host_uses_macos_ping_flags(self):
        with patch("watchdog_core.subprocess.run") as run:
            run.return_value.returncode = 0

            self.assertTrue(ping_host_macos("192.168.50.1", 1.0))

            run.assert_called_once()
            args = run.call_args.args[0]
            self.assertEqual(args, ["ping", "-c", "1", "-W", "1000", "192.168.50.1"])
            self.assertEqual(run.call_args.kwargs["timeout"], 1.0)

    def test_http_endpoint_uses_curl_with_hard_timeout(self):
        with patch("watchdog_core.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "200"

            self.assertTrue(check_http_endpoint("https://ya.ru/", 5.0, "watchdog-test/1.0"))

            run.assert_called_once()
            args = run.call_args.args[0]
            self.assertEqual(args[0], "curl")
            self.assertIn("--max-time", args)
            self.assertEqual(args[args.index("--max-time") + 1], "5.000")
            self.assertIn("--connect-timeout", args)
            self.assertEqual(args[args.index("--connect-timeout") + 1], "5.000")
            self.assertEqual(args[-1], "https://ya.ru/")
            self.assertEqual(run.call_args.kwargs["timeout"], 5.0)

    def test_http_endpoint_rejects_curl_error(self):
        with patch("watchdog_core.subprocess.run") as run:
            run.return_value.returncode = 28
            run.return_value.stdout = "000"

            self.assertFalse(check_http_endpoint("https://ya.ru/", 5.0, "watchdog-test/1.0"))

    def test_tls_handshake_uses_socket_ssl_and_sni(self):
        raw_socket = MagicMock()
        ssl_context = MagicMock()
        tls_socket = MagicMock()
        ssl_context.wrap_socket.return_value = tls_socket

        with patch("watchdog_core.socket.create_connection", return_value=raw_socket) as create_connection:
            with patch("watchdog_core.ssl.create_default_context", return_value=ssl_context):
                self.assertTrue(check_tls_handshake("instagram.com", 443, 5.0))

        create_connection.assert_called_once_with(("instagram.com", 443), timeout=5.0)
        raw_socket.settimeout.assert_called_once()
        self.assertGreater(raw_socket.settimeout.call_args.args[0], 0)
        self.assertLessEqual(raw_socket.settimeout.call_args.args[0], 5.0)
        ssl_context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="instagram.com")

    def test_home_assistant_api_uses_token_via_stdin_config(self):
        with patch("watchdog_core.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "200"

            self.assertTrue(
                check_home_assistant_api(
                    "192.168.50.4",
                    8123,
                    "secret-token",
                    "/api/config",
                    2.0,
                    "watchdog-test/1.0",
                )
            )

            run.assert_called_once()
            args = run.call_args.args[0]
            self.assertEqual(args[:3], ["curl", "--config", "-"])
            self.assertEqual(args[-1], "http://192.168.50.4:8123/api/config")
            self.assertNotIn("secret-token", args)
            self.assertIn("Authorization: Bearer secret-token", run.call_args.kwargs["input"])
            self.assertEqual(run.call_args.kwargs["timeout"], 2.0)

    def test_home_assistant_api_rejects_unauthorized(self):
        with patch("watchdog_core.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "401"

            self.assertFalse(
                check_home_assistant_api(
                    "192.168.50.4",
                    8123,
                    "bad-token",
                    "/api/config",
                    2.0,
                    "watchdog-test/1.0",
                )
            )

    def test_real_checkers_use_configured_addresses_and_timeouts(self):
        config = make_test_config()
        configure_checks(config)

        with patch("watchdog_core.ping_host_macos", return_value=True) as ping:
            self.assertTrue(check_lcl_net())
            ping.assert_called_once_with(config.lcl_net.address, config.lcl_net.timeout_seconds)

        with patch("watchdog_core.check_tls_handshake", return_value=True) as tls:
            self.assertTrue(check_INT_net())
            tls.assert_called_once_with(
                config.int_net.host,
                config.int_net.port,
                config.int_net.timeout_seconds,
            )

        with patch("watchdog_core.check_tls_handshake", return_value=True) as tls:
            self.assertTrue(check_vpn())
            tls.assert_called_once_with(
                config.vpn.host,
                config.vpn.port,
                config.vpn.timeout_seconds,
            )

        with patch("watchdog_core.check_home_assistant_api", return_value=True) as haos:
            self.assertTrue(check_haos())
            haos.assert_called_once_with(
                config.haos.address,
                config.haos.port,
                config.haos.token,
                config.haos.api_path,
                config.haos.timeout_seconds,
                config.global_config.http_user_agent,
            )

    def test_tcp_port_check_uses_configured_timeout(self):
        connection = patch("watchdog_core.socket.create_connection")
        with connection as create_connection:
            create_connection.return_value.__enter__.return_value = object()

            self.assertTrue(check_tcp_port("192.168.50.3", 445, 1.0))

            create_connection.assert_called_once_with(("192.168.50.3", 445), timeout=1.0)

    def test_smb_share_parser_finds_share_name(self):
        output = """
Share                                           Type    Comments
-------------------------------
BACKUP                                          Disk
homes                                           Disk
"""
        self.assertTrue(smb_share_is_listed(output, "BACKUP"))
        self.assertFalse(smb_share_is_listed(output, "MISSING"))

    def test_smb_share_check_uses_smbutil_and_timeout(self):
        with patch("watchdog_core.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "BACKUP Disk\n"

            self.assertTrue(check_smb_share_macos("192.168.50.3", "watchdog", "test-smb-pass", "BACKUP", 5.0))

            run.assert_called_once()
            args = run.call_args.args[0]
            self.assertEqual(args[:4], ["smbutil", "view", "-N", "-f"])
            self.assertEqual(args[4], "//watchdog:test-smb-pass@192.168.50.3")
            self.assertEqual(run.call_args.kwargs["timeout"], 5.0)

    def test_nas_checker_uses_configured_settings(self):
        config = make_test_config()
        configure_checks(config)

        with patch("watchdog_core.check_tcp_port", return_value=True) as tcp:
            with patch("watchdog_core.check_smb_share_macos", return_value=True) as smb:
                self.assertTrue(check_nas())

        tcp.assert_called_once_with(
            config.nas.address,
            config.nas.port,
            config.nas.tcp_timeout_seconds,
        )
        smb.assert_called_once_with(
            config.nas.address,
            config.nas.smb_user,
            config.nas.smb_pass,
            config.nas.smb_share,
            config.nas.smb_timeout_seconds,
        )


if __name__ == "__main__":
    unittest.main()
