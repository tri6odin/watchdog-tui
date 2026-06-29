import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from watchdog_config import (
    GlobalConfig,
    HaosConfig,
    NasConfig,
    PingConfig,
    TouchConfig,
    TlsConfig,
    WifiConfig,
    WatchdogConfig,
    load_watchdog_config,
)
from touch_swipe import (
    ABS_X,
    ABS_Y,
    BTN_TOUCH,
    EV_ABS,
    EV_KEY,
    SWIPE_LEFT,
    TouchSwipeReader,
    touch_event_device_from_proc_text,
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
    check_smb_share_linux,
    check_tcp_port,
    check_tls_handshake,
    check_vpn,
    check_wifi,
    check_wifi_network_linux,
    configure_checks,
    detect_wifi_interface_linux,
    interface_ipv4_address_linux,
    ping_host_linux,
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
        wifi=WifiConfig(
            ssid="example-wifi",
            password="test-wifi-pass",
            gateway="192.168.50.1",
            interface="auto",
            connect_timeout_seconds=25.0,
            ping_timeout_seconds=1.0,
            disconnect_after_check=True,
        ),
        iot_wifi=WifiConfig(
            ssid="example-iot",
            password="test-wifi-pass",
            gateway="192.168.50.1",
            interface="auto",
            connect_timeout_seconds=25.0,
            ping_timeout_seconds=1.0,
            disconnect_after_check=True,
        ),
        touch=TouchConfig(
            enabled=True,
            event_device="auto",
            swipe_min_dx=1200,
            swipe_max_dt_sec=2.2,
            swipe_horizontal_ratio=1.1,
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

[wifi]
ssid = example-wifi
password = test-wifi-pass
gateway = 192.168.50.1
interface = auto
connect_timeout_seconds = 25.0
ping_timeout_seconds = 1.0
disconnect_after_check = true

[iot_wifi]
ssid = example-iot
password = test-wifi-pass
gateway = 192.168.50.1
interface = auto
connect_timeout_seconds = 25.0
ping_timeout_seconds = 1.0
disconnect_after_check = true

[touch]
enabled = true
event_device = auto
swipe_min_dx = 1200
swipe_max_dt_sec = 2.2
swipe_horizontal_ratio = 1.1
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
        self.assertEqual(config.wifi.ssid, "example-wifi")
        self.assertEqual(config.wifi.password, "test-wifi-pass")
        self.assertEqual(config.wifi.gateway, "192.168.50.1")
        self.assertEqual(config.iot_wifi.ssid, "example-iot")
        self.assertEqual(config.iot_wifi.gateway, "192.168.50.1")
        self.assertTrue(config.touch.enabled)
        self.assertEqual(config.touch.event_device, "auto")
        self.assertEqual(config.touch.swipe_min_dx, 1200)
        self.assertEqual(config.touch.swipe_max_dt_sec, 2.2)
        self.assertEqual(config.touch.swipe_horizontal_ratio, 1.1)

    def test_aggregate_priority(self):
        self.assertEqual(aggregate_statuses([]), Status.NO_DATA)
        self.assertEqual(aggregate_statuses([Status.OK, Status.OK]), Status.OK)
        self.assertEqual(aggregate_statuses([Status.OK, Status.UNKNOWN]), Status.UNKNOWN)
        self.assertEqual(aggregate_statuses([Status.OK, Status.FAIL, Status.UNKNOWN]), Status.FAIL)

    def test_touch_device_is_detected_from_proc_devices_text(self):
        proc_devices_text = """
I: Bus=0018 Vendor=0000 Product=0000 Version=0000
N: Name="Goodix Capacitive TouchScreen"
P: Phys=input/ts
H: Handlers=mouse0 event2
B: EV=b

I: Bus=0003 Vendor=0000 Product=0000 Version=0000
N: Name="USB Keyboard"
H: Handlers=sysrq kbd event0
B: EV=120013
"""

        self.assertEqual(touch_event_device_from_proc_text(proc_devices_text), "/dev/input/event2")

    def test_touch_swipe_reader_detects_horizontal_swipe(self):
        reader = TouchSwipeReader("auto", min_dx=1200, max_dt_sec=2.2, horizontal_ratio=1.1)

        with patch("touch_swipe.time.monotonic", side_effect=[10.0, 11.0]):
            reader._process_event(EV_ABS, ABS_X, 5000)
            reader._process_event(EV_ABS, ABS_Y, 2000)
            reader._process_event(EV_KEY, BTN_TOUCH, 1)
            reader._process_event(EV_ABS, ABS_X, 3500)
            reader._process_event(EV_ABS, ABS_Y, 2100)
            event = reader._process_event(EV_KEY, BTN_TOUCH, 0)

        self.assertEqual(event, SWIPE_LEFT)

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

    def test_ping_host_uses_linux_ping_flags(self):
        with patch("watchdog_core.subprocess.run") as run:
            run.return_value.returncode = 0

            self.assertTrue(ping_host_linux("192.168.50.1", 1.0))

            run.assert_called_once()
            args = run.call_args.args[0]
            self.assertEqual(args, ["ping", "-c", "1", "-W", "1", "192.168.50.1"])
            self.assertEqual(run.call_args.kwargs["timeout"], 1.0)

    def test_ping_host_can_pin_wifi_interface(self):
        with patch("watchdog_core.subprocess.run") as run:
            run.return_value.returncode = 0

            self.assertTrue(ping_host_linux("192.168.50.1", 1.0, interface="wlan0"))

            args = run.call_args.args[0]
            self.assertEqual(args, ["ping", "-c", "1", "-W", "1", "-I", "wlan0", "192.168.50.1"])

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

        with patch("watchdog_core.ping_host_linux", return_value=True) as ping:
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

        with patch("watchdog_core.check_wifi_network_linux", return_value=True) as wifi:
            self.assertTrue(check_wifi())
            wifi.assert_called_once_with(
                config.wifi.ssid,
                config.wifi.password,
                config.wifi.gateway,
                config.wifi.interface,
                config.wifi.connect_timeout_seconds,
                config.wifi.ping_timeout_seconds,
                config.wifi.disconnect_after_check,
            )

    def test_tcp_port_check_uses_configured_timeout(self):
        connection = patch("watchdog_core.socket.create_connection")
        with connection as create_connection:
            create_connection.return_value.__enter__.return_value = object()

            self.assertTrue(check_tcp_port("192.168.50.3", 445, 1.0))

            create_connection.assert_called_once_with(("192.168.50.3", 445), timeout=1.0)

    def test_smb_share_parser_finds_share_name(self):
        table_output = """
Share                                           Type    Comments
-------------------------------
BACKUP                                          Disk
homes                                           Disk
"""
        grepable_output = "Disk|BACKUP|\nDisk|homes|\n"

        self.assertTrue(smb_share_is_listed(table_output, "BACKUP"))
        self.assertTrue(smb_share_is_listed(grepable_output, "BACKUP"))
        self.assertFalse(smb_share_is_listed(grepable_output, "MISSING"))

    def test_smb_share_check_uses_smbclient_and_timeout(self):
        with patch("watchdog_core.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "Disk|BACKUP|\n"

            self.assertTrue(check_smb_share_linux("192.168.50.3", "watchdog", "test-smb-pass", "BACKUP", 5.0))

            run.assert_called_once()
            args = run.call_args.args[0]
            self.assertEqual(args, ["smbclient", "-L", "//192.168.50.3", "-U", "watchdog", "-g"])
            self.assertNotIn("test-smb-pass", args)
            self.assertEqual(run.call_args.kwargs["env"]["PASSWD"], "test-smb-pass")
            self.assertEqual(run.call_args.kwargs["timeout"], 5.0)

    def test_wifi_interface_detection_uses_nmcli_wifi_device(self):
        with patch("watchdog_core.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "eth0:ethernet:connected\nwlan0:wifi:disconnected\n"

            self.assertEqual(detect_wifi_interface_linux("auto", 5.0), "wlan0")

            args = run.call_args.args[0]
            self.assertEqual(args, ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"])

    def test_interface_ipv4_detection_uses_ip_addr(self):
        with patch("watchdog_core.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = (
                "3: wlan0    inet 192.168.50.218/24 brd 192.168.50.255 "
                "scope global dynamic noprefixroute wlan0\n"
            )

            self.assertEqual(interface_ipv4_address_linux("wlan0", 1.0), "192.168.50.218")

            args = run.call_args.args[0]
            self.assertEqual(args, ["ip", "-4", "-o", "addr", "show", "dev", "wlan0", "scope", "global"])
            self.assertEqual(run.call_args.kwargs["timeout"], 1.0)

    def test_wifi_network_check_uses_nmcli_ask_and_pings_via_source_ip(self):
        with patch("watchdog_core.detect_wifi_interface_linux", return_value="wlan0") as detect:
            with patch("watchdog_core.nmcli_run", return_value=True) as nmcli:
                with patch("watchdog_core.wait_for_interface_ipv4_linux", return_value="192.168.50.218") as wait_ip:
                    with patch("watchdog_core.ping_host_linux", return_value=True) as ping:
                        self.assertTrue(
                            check_wifi_network_linux(
                                "example-wifi",
                                "test-wifi-pass",
                                "192.168.50.1",
                                "auto",
                                25.0,
                                1.0,
                                True,
                            )
                        )

        detect.assert_called_once_with("auto", 5.0)
        connect_command = nmcli.call_args_list[1].args[0]
        self.assertIn("--ask", connect_command)
        self.assertNotIn("test-wifi-pass", connect_command)
        self.assertEqual(nmcli.call_args_list[1].kwargs["input_text"], "test-wifi-pass\n")
        wait_ip.assert_called_once_with("wlan0", 5.0)
        ping.assert_called_once_with("192.168.50.1", 1.0, interface="192.168.50.218")
        self.assertEqual(nmcli.call_args_list[-2].args[0], ["nmcli", "--wait", "5", "device", "disconnect", "wlan0"])
        self.assertEqual(nmcli.call_args_list[-1].args[0], ["nmcli", "--wait", "5", "radio", "wifi", "off"])

    def test_wifi_network_check_turns_radio_off_when_connect_fails(self):
        with patch("watchdog_core.detect_wifi_interface_linux", return_value="wlan0"):
            with patch("watchdog_core.nmcli_run", side_effect=[True, False, True, True]) as nmcli:
                with patch("watchdog_core.wait_for_interface_ipv4_linux") as wait_ip:
                    with patch("watchdog_core.ping_host_linux") as ping:
                        self.assertFalse(
                            check_wifi_network_linux(
                                "example-wifi",
                                "test-wifi-pass",
                                "192.168.50.1",
                                "auto",
                                25.0,
                                1.0,
                                True,
                            )
                        )

        wait_ip.assert_not_called()
        ping.assert_not_called()
        self.assertEqual(nmcli.call_args_list[-2].args[0], ["nmcli", "--wait", "5", "device", "disconnect", "wlan0"])
        self.assertEqual(nmcli.call_args_list[-1].args[0], ["nmcli", "--wait", "5", "radio", "wifi", "off"])

    def test_nas_checker_uses_configured_settings(self):
        config = make_test_config()
        configure_checks(config)

        with patch("watchdog_core.check_tcp_port", return_value=True) as tcp:
            with patch("watchdog_core.check_smb_share_linux", return_value=True) as smb:
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
