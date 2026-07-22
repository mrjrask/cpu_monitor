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


def test_wait_for_network_idle_returns_true_when_active_interface_is_quiet(monkeypatch):
    samples = iter([(1000, 2000), (1500, 2500)])
    monkeypatch.setattr(cpu_monitor, "read_network_interface_bytes", lambda interface: next(samples))
    monkeypatch.setattr(cpu_monitor.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cpu_monitor.time, "monotonic", lambda: 0.0)

    idle, combined_rate = cpu_monitor.wait_for_network_idle("eth0", threshold_kbps=10.0, timeout_s=5.0)

    assert idle is True
    assert combined_rate == 1000.0


def test_wait_for_network_idle_times_out_when_active_interface_stays_busy(monkeypatch):
    samples = iter([(0, 0), (2000, 2000)])
    monotonic_values = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(cpu_monitor, "read_network_interface_bytes", lambda interface: next(samples))
    monkeypatch.setattr(cpu_monitor.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cpu_monitor.time, "monotonic", lambda: next(monotonic_values))

    idle, combined_rate = cpu_monitor.wait_for_network_idle("eth0", threshold_kbps=1.0, timeout_s=1.0)

    assert idle is False
    assert combined_rate == 4000.0


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
