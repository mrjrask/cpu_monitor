import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "cpu_monitor.py"
spec = importlib.util.spec_from_file_location("cpu_monitor", MODULE_PATH)
cpu_monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpu_monitor)


def test_build_storage_lines_includes_per_device_and_aggregate_io(monkeypatch):
    monkeypatch.setattr(
        cpu_monitor,
        "read_mounted_storage_details",
        lambda: [
            {"disk_name": "sda", "mountpoint": "/", "total": 1024**3, "free": 256 * 1024**2, "fs_id": 1},
            {"disk_name": "sdb", "mountpoint": "/data", "total": 2 * 1024**3, "free": 1024**3, "fs_id": 2},
        ],
    )

    lines = cpu_monitor.build_storage_lines(
        {
            "sda": (1024, 2048),
            "sdb": (4096, 8192),
            "__total__": (5120, 10240),
        }
    )

    assert lines[0].split() == ["Volume", "Name", "Location", "Used", "Free", "%", "Free", "Write/s", "Read/s"]
    assert "sda" in lines[2]
    assert "2.00 KB" in lines[2]
    assert "1.00 KB" in lines[2]
    assert "sdb" in lines[3]
    assert "8.00 KB" in lines[3]
    assert "4.00 KB" in lines[3]
    assert lines[-1] == "Aggregate I/O: write 10.00 KB/s read 5.00 KB/s"


def test_build_storage_lines_uses_root_fallback(monkeypatch):
    monkeypatch.setattr(cpu_monitor, "read_mounted_storage_details", lambda: [])
    monkeypatch.setattr(cpu_monitor, "read_storage_usage", lambda path: (1000, 250))

    lines = cpu_monitor.build_storage_lines()

    assert "rootfs" in lines[2]
    assert "/" in lines[2]
    assert lines[-1] == "Aggregate I/O: write 0.00 B/s read 0.00 B/s"


def test_check_network_idle_returns_false_until_it_has_two_samples(monkeypatch):
    state = cpu_monitor.PingIdleState()
    monkeypatch.setattr(cpu_monitor, "read_network_interface_bytes", lambda interface: (1000, 2000))

    idle, combined_rate, has_sample = cpu_monitor.check_network_idle("eth0", state, threshold_kbps=10.0, now=1.0)

    assert idle is False
    assert combined_rate is None
    assert has_sample is False


def test_check_network_idle_returns_true_when_active_interface_is_quiet(monkeypatch):
    samples = iter([(1000, 2000), (1500, 2500)])
    state = cpu_monitor.PingIdleState()
    monkeypatch.setattr(cpu_monitor, "read_network_interface_bytes", lambda interface: next(samples))

    cpu_monitor.check_network_idle("eth0", state, threshold_kbps=10.0, now=1.0)
    idle, combined_rate, has_sample = cpu_monitor.check_network_idle("eth0", state, threshold_kbps=10.0, now=2.0)

    assert idle is True
    assert combined_rate == 1000.0
    assert has_sample is True


def test_check_network_idle_returns_false_when_active_interface_is_busy(monkeypatch):
    samples = iter([(0, 0), (2000, 2000)])
    state = cpu_monitor.PingIdleState()
    monkeypatch.setattr(cpu_monitor, "read_network_interface_bytes", lambda interface: next(samples))

    cpu_monitor.check_network_idle("eth0", state, threshold_kbps=1.0, now=1.0)
    idle, combined_rate, has_sample = cpu_monitor.check_network_idle("eth0", state, threshold_kbps=1.0, now=2.0)

    assert idle is False
    assert combined_rate == 4000.0
    assert has_sample is True


def test_format_ip_addresses_displays_addresses_or_na():
    assert cpu_monitor.format_ip_addresses(["192.168.1.10", "2001:db8::10"]) == "192.168.1.10, 2001:db8::10"
    assert cpu_monitor.format_ip_addresses([]) == "N/A"


def test_read_interface_ip_addresses_parses_linux_global_addresses(monkeypatch):
    class Result:
        returncode = 0
        stdout = """2: eth0    inet 192.168.1.10/24 brd 192.168.1.255 scope global eth0\n2: eth0    inet6 2001:db8::10/64 scope global dynamic\n"""

    monkeypatch.setattr(cpu_monitor.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cpu_monitor.subprocess, "run", lambda *args, **kwargs: Result())

    assert cpu_monitor.read_interface_ip_addresses("eth0") == ["192.168.1.10", "2001:db8::10"]


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _clamp_tests():
    assert cpu_monitor._clamp_percent(50) == 50.0
    assert cpu_monitor._clamp_percent(120) == 100.0
    assert cpu_monitor._clamp_percent(-5) == 0.0
    assert cpu_monitor._clamp_percent("82.5") == 82.5
    assert cpu_monitor._clamp_percent("abc") is None
    assert cpu_monitor._max_percent([30, 80, None, 12]) == 80.0
    assert cpu_monitor._max_percent([None, None]) is None


def test_clamp_percent_and_max():
    _clamp_tests()


def test_read_gpu_usage_darwin_parses_ioreg(monkeypatch):
    ioreg = (
        '"PerformanceStatistics" = {"Device Utilization %"=82,"Renderer Utilization %"=80};\n'
        '"PerformanceStatistics" = {"Device Utilization %"=33};'
    )
    monkeypatch.setattr(cpu_monitor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cpu_monitor.subprocess, "run", lambda *a, **k: _Result(0, ioreg))
    assert cpu_monitor.read_gpu_usage_pct() == 82.0


def test_read_gpu_usage_darwin_unavailable(monkeypatch):
    monkeypatch.setattr(cpu_monitor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cpu_monitor.subprocess, "run", lambda *a, **k: _Result(1, ""))
    assert cpu_monitor.read_gpu_usage_pct() is None


def test_read_gpu_usage_linux_nvidia(monkeypatch):
    monkeypatch.setattr(cpu_monitor.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cpu_monitor.subprocess, "run", lambda *a, **k: _Result(0, "45\n91\n"))
    monkeypatch.setattr(cpu_monitor, "glob", lambda pattern: [])  # no AMD sysfs
    assert cpu_monitor.read_gpu_usage_pct() == 91.0


def test_read_gpu_usage_linux_amd_sysfs(monkeypatch, tmp_path):
    card0 = tmp_path / "card0"
    card1 = tmp_path / "card1"
    for card in (card0, card1):
        (card / "device").mkdir(parents=True)
        (card / "device" / "gpu_busy_percent").write_text("17\n")
    (card1 / "device" / "gpu_busy_percent").write_text("64\n")
    monkeypatch.setattr(cpu_monitor.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cpu_monitor.subprocess, "run", lambda *a, **k: _Result(127, ""))  # no nvidia-smi
    # Emulate the two /sys/class/drm/card*/device/gpu_busy_percent matches the reader globs.
    monkeypatch.setattr(
        cpu_monitor,
        "glob",
        lambda pattern: [str(card0 / "device" / "gpu_busy_percent"), str(card1 / "device" / "gpu_busy_percent")],
    )
    assert cpu_monitor.read_gpu_usage_pct() == 64.0


def test_read_gpu_usage_windows_counter(monkeypatch):
    monkeypatch.setattr(cpu_monitor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cpu_monitor.subprocess, "run", lambda *a, **k: _Result(0, "12.5 8.0 30.2 4.0"))
    # nvidia-smi call returns the same stdout; a single unparseable line -> None, then the counter branch parses.
    assert cpu_monitor.read_gpu_usage_pct() == 30.2


def test_read_gpu_usage_unknown_platform_none(monkeypatch):
    monkeypatch.setattr(cpu_monitor.platform, "system", lambda: "SunOS")
    assert cpu_monitor.read_gpu_usage_pct() is None


def test_read_npu_usage_darwin_best_effort_none(monkeypatch):
    monkeypatch.setattr(cpu_monitor.platform, "system", lambda: "Darwin")
    assert cpu_monitor.read_npu_usage_pct() is None


def test_read_npu_usage_linux_no_counter(monkeypatch):
    monkeypatch.setattr(cpu_monitor.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cpu_monitor, "glob", lambda pattern: [])
    assert cpu_monitor.read_npu_usage_pct() is None


def test_colorize_usage_pct():
    assert cpu_monitor.colorize_usage_pct(None) == "N/A"
    text = cpu_monitor.colorize_usage_pct(12.3)
    assert "12.3" in text and "%" in text


def test_color_for_usage_thresholds():
    assert cpu_monitor.color_for_usage(10) == cpu_monitor.RESET
    assert cpu_monitor.color_for_usage(35) == cpu_monitor.YELLOW
    assert cpu_monitor.color_for_usage(55) == cpu_monitor.ORANGE
    assert cpu_monitor.color_for_usage(75) == cpu_monitor.RED
    assert cpu_monitor.color_for_usage(95) == cpu_monitor.PURPLE


def _state():
    return {
        "hostname": "host", "board_model": "M1 Max", "temp_c": None, "pi_soc_temp_c": None,
        "display_temp_c": None, "fan_status": "N/A", "pi_health": "N/A",
        "cpu_usage": 12.0, "cpu_freq_text": "3200 MHz", "gpu_usage": 82.0, "npu_usage": None,
        "mem_total": 1 << 30, "mem_used": 1 << 29, "mem_pct": 12.5,
        "storage_lines": ["Volume Name"], "tx_rate": 0.0, "rx_rate": 0.0,
        "active_interface": None, "active_ip_addresses": [], "connection_type": "Ethernet/Other",
        "wifi_details": cpu_monitor.empty_wifi_details(), "ping_label": "disabled", "ping_text": "Disabled",
    }


def test_render_full_dashboard_includes_gpu_and_npu(capsys):
    cpu_monitor.render_full_dashboard(_state())
    out = capsys.readouterr().out
    assert "GPU Usage:" in out and "82.0%" in out
    assert "NPU Usage: N/A" in out
    assert out.index("GPU Usage") < out.index("NPU Usage") < out.index("CPU Freq")


def test_render_compact_dashboard_includes_gpu_and_npu(capsys):
    state = _state()
    state["display_cols"] = cpu_monitor.COMPACT_COLS
    cpu_monitor.render_compact_dashboard(state)
    out = capsys.readouterr().out
    assert "GPU 82.0% NPU N/A" in out


def test_calculate_required_rows_accounts_for_new_lines():
    assert cpu_monitor.calculate_required_rows(1, False, False) == 20  # 19 base + 1 storage
    assert cpu_monitor.calculate_required_rows(1, False, True) == 9  # compact


def test_calculate_required_cols_handles_none_gpu_and_npu():
    state = _state()
    state["gpu_usage"] = None
    state["npu_usage"] = None
    assert isinstance(cpu_monitor.calculate_required_cols(state, False), int)
    state["display_cols"] = cpu_monitor.COMPACT_COLS
    assert isinstance(cpu_monitor.calculate_required_cols(state, True), int)


def test_build_metrics_record_maps_raw_values_and_nulls():
    state = {
        "hostname": "host", "board_model": "M1 Max",
        "temp_c": 55.234, "pi_soc_temp_c": None,
        "fan_status": "1200 RPM", "fan_rpm": 1200, "pi_health": "OK",
        "cpu_usage": 12.3456, "cpu_freq_mhz": 1804.7,
        "gpu_usage": 82.0, "npu_usage": None,
        "mem_total": 8 << 30, "mem_used": 3 << 30, "mem_pct": 37.5,
        "storage_lines": ["x"],
        "storage_io_rates": {"sda": (2048.0, 4096.0), "__total__": (2048.0, 4096.0)},
        "tx_rate": 1234.5, "rx_rate": 5678.0,
        "active_interface": "en0", "active_ip_addresses": ["192.168.1.5"],
        "connection_type": "Ethernet/Other",
        "wifi_details": cpu_monitor.empty_wifi_details(),
        "ping_avg": 9.12, "ping_error": None,
    }
    rec = cpu_monitor.build_metrics_record(state, 1717000000.1234)

    assert rec["ts"] == 1717000000.123
    assert rec["time"].startswith("2024-05-29")
    assert rec["hostname"] == "host"
    assert rec["cpu_temp_c"] == 55.23
    assert rec["soc_temp_c"] is None
    assert rec["cpu_usage_pct"] == 12.346
    assert rec["cpu_freq_mhz"] == 1804.7
    assert rec["fan_rpm"] == 1200
    assert rec["npu_usage_pct"] is None
    assert rec["mem_pct"] == 37.5
    assert rec["storage_read_bytes_per_s"] == 2048.0
    assert rec["storage_write_bytes_per_s"] == 4096.0
    assert rec["storage_devices"] == {"sda": {"read_bytes_per_s": 2048.0, "write_bytes_per_s": 4096.0}}
    assert rec["net_rx_bits_per_s"] == round(5678.0 * 8.0, 1)
    assert rec["net_tx_bits_per_s"] == round(1234.5 * 8.0, 1)
    assert rec["wifi_ssid"] is None
    assert rec["net_ip_addresses"] == ["192.168.1.5"]
    assert rec["ping_avg_ms"] == 9.12


def test_build_metrics_record_handles_missing_keys_as_null():
    rec = cpu_monitor.build_metrics_record({}, 1000.0)
    assert rec["hostname"] is None
    assert rec["cpu_usage_pct"] is None
    assert rec["storage_devices"] == {}
    assert rec["net_ip_addresses"] == []
    assert rec["net_rx_bytes_per_s"] == 0.0
    assert rec["net_rx_bits_per_s"] == 0.0


def test_jsonl_metrics_logger_appends_valid_json(tmp_path):
    import json

    path = tmp_path / "m.jsonl"
    logger = cpu_monitor.JsonlMetricsLogger(str(path), enabled=True)
    rec = {"a": 1, "b": None}
    logger.log(rec)
    logger.log(rec)

    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 2
    assert [json.loads(line) for line in lines] == [rec, rec]


def test_jsonl_metrics_logger_disabled_is_noop(tmp_path):
    path = tmp_path / "m.jsonl"
    cpu_monitor.JsonlMetricsLogger(str(path), enabled=False).log({"a": 1})
    assert not path.exists()


def test_parse_args_metrics_log_flags(monkeypatch):
    import sys

    old = sys.argv
    try:
        monkeypatch.setattr(sys, "argv", ["cpu_monitor.py", "--no-metrics-log"])
        cfg = cpu_monitor.parse_args()
        assert cfg.metrics_log_enabled is False
        assert cfg.metrics_log_path == cpu_monitor.DEFAULT_METRICS_LOG_PATH

        monkeypatch.setattr(sys, "argv", ["cpu_monitor.py", "--metrics-log", "/tmp/x.jsonl"])
        cfg = cpu_monitor.parse_args()
        assert cfg.metrics_log_enabled is True
        assert cfg.metrics_log_path == "/tmp/x.jsonl"
    finally:
        sys.argv = old
