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
