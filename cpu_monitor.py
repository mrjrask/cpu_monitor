#!/usr/bin/env python3
import logging
import random
import signal
import subprocess
import time
import shutil
import socket
from glob import glob

# ANSI color codes
RESET   = "\033[0m"
YELLOW  = "\033[33m"
ORANGE  = "\033[38;5;208m"
RED     = "\033[31m"
PURPLE  = "\033[35m"

# Configure logging to append timestamped entries to cpu_monitor.log
logging.basicConfig(
    filename="cpu_monitor.log",
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

CLEAR_SCREEN = "\033[2J\033[H"
CURSOR_HOME = "\033[H"
CLEAR_LINE = "\033[K"

_needs_full_refresh = False


def _handle_resize(signum, frame):
    """Signal handler that flags the need for a full terminal refresh."""
    global _needs_full_refresh
    _needs_full_refresh = True


def clear_terminal():
    """Clear the terminal window and move the cursor to the top left."""
    print(CLEAR_SCREEN, end="", flush=True)


def get_cpu_temp():
    """Read CPU temperature (°C) from system."""
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        millideg = int(f.read().strip())
    return millideg / 1000.0


def read_fan_speed_rpm():
    """Return the first detected fan speed in RPM, or None if unavailable."""
    fan_paths = [
        "/sys/devices/platform/cooling_fan/hwmon/hwmon*/fan1_input",
        "/sys/class/hwmon/hwmon*/fan1_input",
    ]

    for pattern in fan_paths:
        for path in glob(pattern):
            try:
                with open(path, "r") as f:
                    rpm = int(f.read().strip())
                if rpm >= 0:
                    return rpm
            except (FileNotFoundError, OSError, ValueError):
                continue

    return None


def read_cpu_times():
    """Read aggregate CPU idle and total times."""
    with open("/proc/stat", "r") as f:
        parts = f.readline().split()[1:]
    times = list(map(int, parts))
    idle = times[3] + times[4]
    total = sum(times)
    return idle, total


def read_network_bytes():
    """Return total received and transmitted bytes for non-loopback interfaces."""
    total_rx = 0
    total_tx = 0
    with open("/proc/net/dev", "r") as f:
        for line in f.readlines()[2:]:
            iface, data = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo":
                continue
            fields = data.split()
            total_rx += int(fields[0])
            total_tx += int(fields[8])
    return total_rx, total_tx


def read_memory_usage():
    """Return total and used memory in bytes."""
    mem_total = None
    mem_available = None

    with open("/proc/meminfo", "r") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1]) * 1024
            elif line.startswith("MemAvailable:"):
                mem_available = int(line.split()[1]) * 1024
            if mem_total and mem_available:
                break

    mem_used = max(mem_total - mem_available, 0)
    return mem_total, mem_used


def read_storage_usage(path="/"):
    """Return total and used storage in bytes for the given path."""
    usage = shutil.disk_usage(path)
    return usage.total, max(usage.total - usage.free, 0)


def color_for_cpu(usage):
    if usage >= 90.0:
        return PURPLE
    if usage >= 70.0:
        return RED
    if usage >= 50.0:
        return ORANGE
    if usage >= 30.0:
        return YELLOW
    return RESET


def color_for_temp(temp_c):
    if temp_c >= 75.0:
        return RED
    if temp_c >= 68.0:
        return ORANGE
    if temp_c >= 60.0:
        return YELLOW
    return RESET


def format_bytes_per_sec(num_bytes_per_sec):
    units = ["B/s", "KB/s", "MB/s", "GB/s", "TB/s"]
    value = float(max(num_bytes_per_sec, 0.0))
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:7.2f} {unit}"
        value /= 1024.0


def format_bytes(num_bytes):
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(max(num_bytes, 0.0))
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:7.2f} {unit}"
        value /= 1024.0


def run_ping():
    try:
        result = subprocess.run(
            ["ping", "-c", "3", "-n", "1.1.1.1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        return None, str(exc)

    if result.returncode != 0:
        return None, result.stderr.strip()

    for line in result.stdout.splitlines():
        if "rtt" in line or "round-trip" in line:
            try:
                stats = line.split("=")[1].split()[0].split("/")
                return float(stats[1]), None
            except Exception:
                return None, "parse error"

    return None, "no stats found"


def main():
    global _needs_full_refresh

    hostname = socket.gethostname()

    clear_terminal()
    signal.signal(signal.SIGWINCH, _handle_resize)

    prev_idle, prev_total = read_cpu_times()
    prev_rx, prev_tx = read_network_bytes()
    prev_time = time.monotonic()

    next_ping_time = prev_time + random.uniform(10, 40)
    last_ping_avg = None
    last_ping_error = None

    try:
        while True:
            time.sleep(1)
            now = time.monotonic()

            if _needs_full_refresh:
                clear_terminal()
                _needs_full_refresh = False

            idle, total = read_cpu_times()
            cpu_usage = (1 - (idle - prev_idle) / (total - prev_total)) * 100 if total != prev_total else 0
            prev_idle, prev_total = idle, total

            temp_c = get_cpu_temp()
            temp_f = temp_c * 9 / 5 + 32
            fan_rpm = read_fan_speed_rpm()

            mem_total, mem_used = read_memory_usage()
            mem_pct = mem_used / mem_total * 100

            stor_total, stor_used = read_storage_usage("/")
            stor_pct = stor_used / stor_total * 100

            rx, tx = read_network_bytes()
            elapsed = now - prev_time
            rx_rate = (rx - prev_rx) / elapsed
            tx_rate = (tx - prev_tx) / elapsed
            prev_rx, prev_tx, prev_time = rx, tx, now

            if now >= next_ping_time:
                last_ping_avg, last_ping_error = run_ping()
                next_ping_time = now + random.uniform(10, 40)

            print(CURSOR_HOME, end="")
            print(f"Hostname: {hostname}{CLEAR_LINE}")
            print(f"CPU Temp: {color_for_temp(temp_c)}{temp_c:5.2f}°C / {temp_f:5.2f}°F{RESET}{CLEAR_LINE}")
            print(f"Fan Speed: {fan_rpm if fan_rpm is not None else 'N/A'}{CLEAR_LINE}")
            print(f"CPU Usage: {color_for_cpu(cpu_usage)}{cpu_usage:5.1f}%{RESET}{CLEAR_LINE}")
            print(f"Memory: {format_bytes(mem_used)} / {format_bytes(mem_total)} ({mem_pct:5.1f}%){CLEAR_LINE}")
            print(f"Storage: {format_bytes(stor_used)} / {format_bytes(stor_total)} ({stor_pct:5.1f}%){CLEAR_LINE}")
            print(f"Network: ↑ {format_bytes_per_sec(tx_rate)}   ↓ {format_bytes_per_sec(rx_rate)}{CLEAR_LINE}")
            print(
                f"Ping (avg of 3 to 1.1.1.1): "
                f"{'ERROR - ' + last_ping_error if last_ping_error else f'{last_ping_avg:.2f} ms' if last_ping_avg else 'Pending...'}"
                f"{CLEAR_LINE}",
                end="",
                flush=True,
            )

    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
