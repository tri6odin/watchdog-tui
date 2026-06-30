import tempfile
import unittest
from datetime import datetime
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
from watchdog_checks import build_checkers
from watchdog_checks.haos import check_home_assistant_api, check_http_endpoint
from watchdog_checks.nas import check_smb_share_linux, smb_share_is_listed
from watchdog_checks.network import check_tcp_port, check_tls_handshake, ping_host_linux
from watchdog_checks.wifi import (
    active_wifi_ssid_linux,
    check_wifi_network_linux,
    connect_wifi_linux,
    detect_wifi_interface_linux,
    interface_ipv4_address_linux,
    wait_for_interface_no_ipv4_linux,
    wait_for_wifi_device_available_linux,
    wifi_profile_for_ssid_linux,
)
from watchdog_core import (
    CHECK_ORDER,
    DISPLAY_ROWS,
    FOUR_HOURS,
    ONE_DAY,
    Screen,
    Status,
    TEN_MINUTES,
    TOTAL_WIDTH,
    aggregate_statuses,
    align_timestamp,
    run_service_checks,
    seconds_until_next_history_bucket,
)
from watchdog_db import WatchdogDB
from watchdog_ui import footer_line, screen_last_update_at


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

    def test_screen_last_update_uses_screen_cell_boundary(self):
        now = align_timestamp(1_800_000_000, FOUR_HOURS) + TEN_MINUTES

        self.assertEqual(screen_last_update_at(Screen.DAYS, now), align_timestamp(now, FOUR_HOURS))
        self.assertNotEqual(screen_last_update_at(Screen.DAYS, now), align_timestamp(now, TEN_MINUTES))
        self.assertEqual(screen_last_update_at(Screen.WEEKS, now), align_timestamp(now, ONE_DAY))

    def test_footer_line_shows_navigation_left_and_last_update_right(self):
        now = align_timestamp(1_800_000_000, FOUR_HOURS) + TEN_MINUTES
        expected_time = datetime.fromtimestamp(align_timestamp(now, FOUR_HOURS)).strftime("%H:%M")
        line = footer_line(Screen.DAYS, now=now)

        self.assertEqual(len(line), TOTAL_WIDTH)
        self.assertTrue(line.startswith(" ▷ НЕДЕЛИ  ▶ ДНИ  ▷ ЧАСЫ  ▷ ОБНОВИТЬ"))
        self.assertTrue(line.endswith(" "))
        self.assertIn("ПОСЛЕДНЕЕ ОБНОВЛЕНИЕ В", line)
        self.assertIn(expected_time, line)

    def test_update_footer_has_no_last_update_text(self):
        line = footer_line(Screen.UPDATE, now=1_800_000_000)

        self.assertEqual(len(line), TOTAL_WIDTH)
        self.assertTrue(line.startswith(" ▷ НЕДЕЛИ  ▷ ДНИ  ▷ ЧАСЫ  ▶ ОБНОВИТЬ"))
        self.assertTrue(line.endswith(" "))
        self.assertNotIn("ПОСЛЕДНЕЕ ОБНОВЛЕНИЕ", line)

    def test_default_auto_update_aligns_to_next_history_bucket(self):
        bucket_start = align_timestamp(1_800_000_000, TEN_MINUTES)

        self.assertEqual(seconds_until_next_history_bucket(bucket_start), TEN_MINUTES)
        self.assertEqual(seconds_until_next_history_bucket(bucket_start + 125), TEN_MINUTES - 125)

    def test_ping_host_uses_linux_ping_flags(self):
        with patch("watchdog_checks.network.subprocess.run") as run:
            run.return_value.returncode = 0

            self.assertTrue(ping_host_linux("192.168.50.1", 1.0))

            run.assert_called_once()
            args = run.call_args.args[0]
            self.assertEqual(args, ["ping", "-c", "1", "-W", "1", "192.168.50.1"])
            self.assertEqual(run.call_args.kwargs["timeout"], 1.0)

    def test_ping_host_can_pin_wifi_interface(self):
        with patch("watchdog_checks.network.subprocess.run") as run:
            run.return_value.returncode = 0

            self.assertTrue(ping_host_linux("192.168.50.1", 1.0, interface="wlan0"))

            args = run.call_args.args[0]
            self.assertEqual(args, ["ping", "-c", "1", "-W", "1", "-I", "wlan0", "192.168.50.1"])

    def test_ping_host_can_send_multiple_packets(self):
        with patch("watchdog_checks.network.subprocess.run") as run:
            run.return_value.returncode = 0

            self.assertTrue(ping_host_linux("192.168.50.1", 1.0, interface="192.168.50.218", count=3))

            args = run.call_args.args[0]
            self.assertEqual(args, ["ping", "-c", "3", "-W", "1", "-I", "192.168.50.218", "192.168.50.1"])
            self.assertEqual(run.call_args.kwargs["timeout"], 5.0)

    def test_http_endpoint_uses_curl_with_hard_timeout(self):
        with patch("watchdog_checks.haos.subprocess.run") as run:
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
        with patch("watchdog_checks.haos.subprocess.run") as run:
            run.return_value.returncode = 28
            run.return_value.stdout = "000"

            self.assertFalse(check_http_endpoint("https://ya.ru/", 5.0, "watchdog-test/1.0"))

    def test_tls_handshake_uses_socket_ssl_and_sni(self):
        raw_socket = MagicMock()
        ssl_context = MagicMock()
        tls_socket = MagicMock()
        ssl_context.wrap_socket.return_value = tls_socket

        with patch("watchdog_checks.network.socket.create_connection", return_value=raw_socket) as create_connection:
            with patch("watchdog_checks.network.ssl.create_default_context", return_value=ssl_context):
                self.assertTrue(check_tls_handshake("instagram.com", 443, 5.0))

        create_connection.assert_called_once_with(("instagram.com", 443), timeout=5.0)
        raw_socket.settimeout.assert_called_once()
        self.assertGreater(raw_socket.settimeout.call_args.args[0], 0)
        self.assertLessEqual(raw_socket.settimeout.call_args.args[0], 5.0)
        ssl_context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="instagram.com")

    def test_home_assistant_api_uses_token_via_stdin_config(self):
        with patch("watchdog_checks.haos.subprocess.run") as run:
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
        with patch("watchdog_checks.haos.subprocess.run") as run:
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
        checkers = build_checkers(config)

        with patch("watchdog_checks.ping_host_linux", return_value=True) as ping:
            self.assertTrue(checkers["LCL NET"]())
            ping.assert_called_once_with(config.lcl_net.address, config.lcl_net.timeout_seconds)

        with patch("watchdog_checks.check_tls_handshake", return_value=True) as tls:
            self.assertTrue(checkers["INT NET"]())
            tls.assert_called_once_with(
                config.int_net.host,
                config.int_net.port,
                config.int_net.timeout_seconds,
            )

        with patch("watchdog_checks.check_tls_handshake", return_value=True) as tls:
            self.assertTrue(checkers["VPN"]())
            tls.assert_called_once_with(
                config.vpn.host,
                config.vpn.port,
                config.vpn.timeout_seconds,
            )

        with patch("watchdog_checks.check_home_assistant_api", return_value=True) as haos:
            self.assertTrue(checkers["HAOS"]())
            haos.assert_called_once_with(
                config.haos.address,
                config.haos.port,
                config.haos.token,
                config.haos.api_path,
                config.haos.timeout_seconds,
                config.global_config.http_user_agent,
            )

        with patch("watchdog_checks.check_wifi_pair", return_value={"WIFI": True, "IOT WIFI": True}) as wifi:
            self.assertEqual(checkers["WIFI"](), {"WIFI": True, "IOT WIFI": True})
            wifi.assert_called_once_with(config.wifi, config.iot_wifi)

    def test_tcp_port_check_uses_configured_timeout(self):
        connection = patch("watchdog_checks.network.socket.create_connection")
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
        with patch("watchdog_checks.nas.subprocess.run") as run:
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
        with patch("watchdog_checks.wifi.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "eth0:ethernet:connected\nwlan0:wifi:disconnected\n"

            self.assertEqual(detect_wifi_interface_linux("auto", 5.0), "wlan0")

            args = run.call_args.args[0]
            self.assertEqual(args, ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"])

    def test_interface_ipv4_detection_uses_ip_addr(self):
        with patch("watchdog_checks.wifi.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = (
                "3: wlan0    inet 192.168.50.218/24 brd 192.168.50.255 "
                "scope global dynamic noprefixroute wlan0\n"
            )

            self.assertEqual(interface_ipv4_address_linux("wlan0", 1.0), "192.168.50.218")

            args = run.call_args.args[0]
            self.assertEqual(args, ["ip", "-4", "-o", "addr", "show", "dev", "wlan0", "scope", "global"])
            self.assertEqual(run.call_args.kwargs["timeout"], 1.0)

    def test_active_wifi_ssid_uses_networkmanager_profile(self):
        with patch("watchdog_checks.wifi.nmcli_output") as output:
            output.side_effect = ["example-iot\n", "example-iot\n"]

            self.assertEqual(active_wifi_ssid_linux("wlan0", 1.0), "example-iot")

        self.assertEqual(
            output.call_args_list[0].args[0],
            ["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", "wlan0"],
        )
        self.assertEqual(
            output.call_args_list[1].args[0],
            ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "example-iot"],
        )

    def test_active_wifi_ssid_falls_back_to_iw_link(self):
        with patch("watchdog_checks.wifi.nmcli_output", return_value=None):
            with patch("watchdog_checks.wifi.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "Connected to aa:bb:cc:dd:ee:ff\n\tSSID: example-iot\n"

                self.assertEqual(active_wifi_ssid_linux("wlan0", 1.0), "example-iot")

        args = run.call_args.args[0]
        self.assertEqual(args, ["iw", "dev", "wlan0", "link"])

    def test_wait_for_wifi_device_available_waits_past_unavailable(self):
        with patch("watchdog_checks.wifi.wifi_device_state_linux", side_effect=["unavailable", "disconnected"]):
            with patch("watchdog_checks.wifi.time.sleep"):
                self.assertTrue(wait_for_wifi_device_available_linux("wlan0", 2.0))

    def test_wait_for_interface_no_ipv4_returns_when_address_is_gone(self):
        with patch("watchdog_checks.wifi.interface_ipv4_address_linux", return_value=None) as ip:
            self.assertTrue(wait_for_interface_no_ipv4_linux("wlan0", 1.0))

        ip.assert_called_once()
        self.assertEqual(ip.call_args.args[0], "wlan0")
        self.assertLessEqual(ip.call_args.args[1], 1.0)

    def test_wifi_profile_lookup_finds_saved_profile_by_ssid(self):
        with patch("watchdog_checks.wifi.nmcli_output") as output:
            output.side_effect = [
                "netplan-wlan0-example-wifi:wifi\nexample-iot:wifi\n",
                "example-wifi\n",
            ]

            self.assertEqual(wifi_profile_for_ssid_linux("example-wifi", 5.0), "netplan-wlan0-example-wifi")

        self.assertEqual(
            output.call_args_list[0].args[0],
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
        )
        self.assertEqual(
            output.call_args_list[1].args[0],
            ["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "netplan-wlan0-example-wifi"],
        )

    def test_connect_wifi_uses_saved_profile_before_device_connect(self):
        with patch("watchdog_checks.wifi.wifi_profile_for_ssid_linux", return_value="netplan-wlan0-example-wifi"):
            with patch("watchdog_checks.wifi.nmcli_run", return_value=True) as nmcli:
                self.assertTrue(connect_wifi_linux("example-wifi", "test-wifi-pass", "wlan0", 25.0))

        nmcli.assert_called_once_with(
            ["nmcli", "--wait", "25", "connection", "up", "id", "netplan-wlan0-example-wifi", "ifname", "wlan0"],
            25.0,
        )

    def test_connect_wifi_falls_back_to_device_connect(self):
        with patch("watchdog_checks.wifi.wifi_profile_for_ssid_linux", return_value=None):
            with patch("watchdog_checks.wifi.nmcli_run", return_value=True) as nmcli:
                self.assertTrue(connect_wifi_linux("example-wifi", "test-wifi-pass", "wlan0", 25.0))

        args = nmcli.call_args.args[0]
        self.assertIn("--ask", args)
        self.assertEqual(nmcli.call_args.kwargs["input_text"], "test-wifi-pass\n")

    def test_wifi_network_check_uses_profile_and_pings_via_source_ip(self):
        with patch("watchdog_checks.wifi.detect_wifi_interface_linux", return_value="wlan0") as detect:
            with patch("watchdog_checks.wifi.wifi_profile_for_ssid_linux", return_value="netplan-wlan0-example-wifi"):
                with patch("watchdog_checks.wifi.nmcli_run", return_value=True) as nmcli:
                    with patch("watchdog_checks.wifi.wait_for_wifi_device_available_linux", return_value=True) as wait_device:
                        with patch("watchdog_checks.wifi.wait_for_connected_ssid_linux", return_value=True) as wait_ssid:
                            with patch("watchdog_checks.wifi.active_wifi_ssid_linux", return_value="example-wifi"):
                                with patch("watchdog_checks.wifi.wait_for_interface_ipv4_linux", return_value="192.168.50.218") as wait_ip:
                                    with patch("watchdog_checks.wifi.wait_for_interface_no_ipv4_linux", return_value=True) as wait_no_ip:
                                        with patch("watchdog_checks.wifi.ping_host_linux", return_value=True) as ping:
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
        wait_device.assert_called_once_with("wlan0", 10.0)
        connect_command = nmcli.call_args_list[1].args[0]
        self.assertEqual(
            connect_command,
            ["nmcli", "--wait", "25", "connection", "up", "id", "netplan-wlan0-example-wifi", "ifname", "wlan0"],
        )
        self.assertNotIn("test-wifi-pass", connect_command)
        wait_ssid.assert_called_once_with("wlan0", "example-wifi", 5.0)
        wait_ip.assert_called_once_with("wlan0", 5.0)
        ping.assert_called_once_with("192.168.50.1", 1.0, interface="192.168.50.218", count=1)
        wait_no_ip.assert_called_once_with("wlan0", 5.0)
        self.assertEqual(nmcli.call_args_list[-2].args[0], ["nmcli", "--wait", "5", "device", "disconnect", "wlan0"])
        self.assertEqual(nmcli.call_args_list[-1].args[0], ["nmcli", "--wait", "5", "radio", "wifi", "off"])

    def test_wifi_network_check_turns_radio_off_when_connect_fails(self):
        with patch("watchdog_checks.wifi.detect_wifi_interface_linux", return_value="wlan0"):
            with patch("watchdog_checks.wifi.wifi_profile_for_ssid_linux", return_value=None):
                with patch("watchdog_checks.wifi.nmcli_run", side_effect=[True, True, False, True, True]) as nmcli:
                    with patch("watchdog_checks.wifi.wait_for_wifi_device_available_linux", return_value=True):
                        with patch("watchdog_checks.wifi.wait_for_connected_ssid_linux") as wait_ssid:
                            with patch("watchdog_checks.wifi.wait_for_interface_ipv4_linux") as wait_ip:
                                with patch("watchdog_checks.wifi.wait_for_interface_no_ipv4_linux", return_value=True):
                                    with patch("watchdog_checks.wifi.active_wifi_ssid_linux", return_value=None):
                                        with patch("watchdog_checks.wifi.ping_host_linux") as ping:
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

        wait_ssid.assert_not_called()
        wait_ip.assert_not_called()
        ping.assert_not_called()
        self.assertEqual(nmcli.call_args_list[-2].args[0], ["nmcli", "--wait", "5", "device", "disconnect", "wlan0"])
        self.assertEqual(nmcli.call_args_list[-1].args[0], ["nmcli", "--wait", "5", "radio", "wifi", "off"])

    def test_default_service_runner_batches_wifi_checks(self):
        fake_checkers = {service: (lambda: True) for service in CHECK_ORDER}
        fake_checkers["WIFI"] = lambda: {"WIFI": True, "IOT WIFI": False}
        progress = []

        results = run_service_checks(
            fake_checkers,
            progress_callback=lambda service, current: progress.append((service, dict(current))),
            step_delay_seconds=0,
        )

        self.assertEqual(results["WIFI"], Status.OK)
        self.assertEqual(results["IOT WIFI"], Status.FAIL)
        progress_services = [service for service, _current in progress]
        self.assertEqual(progress_services, CHECK_ORDER)

    def test_nas_checker_uses_configured_settings(self):
        config = make_test_config()
        checkers = build_checkers(config)

        with patch("watchdog_checks.check_tcp_port", return_value=True) as tcp:
            with patch("watchdog_checks.check_smb_share_linux", return_value=True) as smb:
                self.assertTrue(checkers["NAS"]())

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
