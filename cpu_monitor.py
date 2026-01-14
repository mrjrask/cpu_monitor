#!/usr/bin/env python3
import logging
import random
import signal
import subprocess
import time
import shutil
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
        "/sys/devices/platform/cooling_fan/hwmon/hwmon*/fan1_input",  # Raspberry Pi 5
        "/sys/class/hwmon/hwmon*/fan1_input",  # Fallback for other systems
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
    idle = times[3] + times[4]    # idle + iowait
    total = sum(times)
    return idle, total


def read_network_bytes():
    """Return total received and transmitted bytes for non-loopback interfaces."""
    total_rx = 0
    total_tx = 0
    with open("/proc/net/dev", "r") as f:
        for line in f.readlines()[2:]:  # Skip headers
            iface, data = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo":
                continue
            fields = data.split()
            rx_bytes = int(fields[0])
            tx_bytes = int(fields[8])
            total_rx += rx_bytes
            total_tx += tx_bytes
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

            if mem_total is not None and mem_available is not None:
                break

    if mem_total is None or mem_available is None:
        raise RuntimeError("Unable to read memory information from /proc/meminfo")

    mem_used = max(mem_total - mem_available, 0)
    return mem_total, mem_used


def read_storage_usage(path="/"):
    """Return total and used storage in bytes for the given path."""
    usage = shutil.disk_usage(path)
    storage_used = max(usage.total - usage.free, 0)
    return usage.total, storage_used


def color_for_cpu(usage):
    """ANSI color based on CPU usage %."""
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
    """ANSI color based on temperature °C."""
    if temp_c >= 75.0:
        return RED
    if temp_c >= 68.0:
        return ORANGE
    if temp_c >= 60.0:
        return YELLOW
    return RESET


def format_bytes_per_sec(num_bytes_per_sec):
    """Return a human-friendly string for a bytes/sec value."""
    units = ["B/s", "KB/s", "MB/s", "GB/s", "TB/s"]
    value = float(max(num_bytes_per_sec, 0.0))
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:7.2f} {unit}"
        value /= 1024.0


def format_bytes(num_bytes):
    """Return a human-friendly string for a byte value."""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(max(num_bytes, 0.0))
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:7.2f} {unit}"
        value /= 1024.0


def run_ping():
    """Run a 3-count ping to 1.1.1.1 and return the average latency in ms."""
    try:
        result = subprocess.run(
            ["ping", "-c", "3", "-n", "1.1.1.1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return None, str(exc)

    if result.returncode != 0:
        error_msg = result.stderr.strip() or f"ping exited with {result.returncode}"
        return None, error_msg

    stats_line = None
    for line in result.stdout.splitlines():
        if "rtt" in line or "round-trip" in line:
            stats_line = line
            break
    if not stats_line:
        return None, "unable to parse ping output"

    try:
        stats = stats_line.split("=")[1].strip().split()[0]
        parts = stats.split("/")
        avg_ms = float(parts[1])
    except (IndexError, ValueError):
        return None, "invalid ping average"

    return avg_ms, None


def main():
    global _needs_full_refresh

    clear_terminal()
    _needs_full_refresh = False
    signal.signal(signal.SIGWINCH, _handle_resize)

    # Take an initial snapshot of CPU times
    prev_idle, prev_total = read_cpu_times()
    prev_rx, prev_tx = read_network_bytes()
    prev_time = time.monotonic()

    next_ping_time = prev_time + random.uniform(10.0, 40.0)
    last_ping_avg = None
    last_ping_error = None

    try:
        while True:
            time.sleep(1)

            now = time.monotonic()

            if _needs_full_refresh:
                clear_terminal()
                _needs_full_refresh = False

            # Calculate CPU usage
            idle, total = read_cpu_times()
            idle_delta  = idle  - prev_idle
            total_delta = total - prev_total
            prev_idle, prev_total = idle, total
            cpu_usage = (1.0 - idle_delta / total_delta) * 100.0 if total_delta else 0.0

            # Read and convert temperature
            temp_c = get_cpu_temp()
            temp_f = (temp_c * 9/5) + 32

            # Read fan speed (if available)
            fan_rpm = read_fan_speed_rpm()

            # Read memory usage
            mem_total_bytes, mem_used_bytes = read_memory_usage()
            mem_used_percent = (mem_used_bytes / mem_total_bytes) * 100.0 if mem_total_bytes else 0.0

            # Read storage usage
            storage_total_bytes, storage_used_bytes = read_storage_usage("/")
            storage_used_percent = (
                (storage_used_bytes / storage_total_bytes) * 100.0 if storage_total_bytes else 0.0
            )

            # Calculate network throughput
            rx_bytes, tx_bytes = read_network_bytes()
            elapsed = now - prev_time
            prev_time = now
            rx_delta = max(rx_bytes - prev_rx, 0)
            tx_delta = max(tx_bytes - prev_tx, 0)
            rx_rate = rx_delta / elapsed if elapsed > 0 else 0.0
            tx_rate = tx_delta / elapsed if elapsed > 0 else 0.0
            prev_rx, prev_tx = rx_bytes, tx_bytes

            # Maybe run ping test
            if now >= next_ping_time:
                last_ping_avg, last_ping_error = run_ping()
                next_ping_time = now + random.uniform(10.0, 40.0)

            # Log to file
            log_parts = [
                f"Temp: {temp_c:.2f}°C/{temp_f:.2f}°F",
                f"CPU: {cpu_usage:.1f}%",
                f"Mem: {mem_used_percent:.1f}% ({format_bytes(mem_used_bytes).strip()} used of {format_bytes(mem_total_bytes).strip()})",
                f"Storage: {storage_used_percent:.1f}% ({format_bytes(storage_used_bytes).strip()} used of {format_bytes(storage_total_bytes).strip()})",
                f"Net: ↑ {format_bytes_per_sec(tx_rate).strip()} ↓ {format_bytes_per_sec(rx_rate).strip()}",
            ]
            log_parts.append(f"Fan: {fan_rpm} RPM" if fan_rpm is not None else "Fan: N/A")
            if last_ping_error:
                log_parts.append(f"Ping error: {last_ping_error}")
            elif last_ping_avg is not None:
                log_parts.append(f"Ping avg: {last_ping_avg:.2f} ms")
            else:
                log_parts.append("Ping: pending")
            logging.info("   ".join(log_parts))

            # Determine colors
            cpu_col  = color_for_cpu(cpu_usage)
            temp_col = color_for_temp(temp_c)

            # Prepare display values
            upload_display = format_bytes_per_sec(tx_rate)
            download_display = format_bytes_per_sec(rx_rate)
            fan_display = f"{fan_rpm} RPM" if fan_rpm is not None else "N/A"
            if last_ping_error:
                ping_display = f"ERROR - {last_ping_error}"
            elif last_ping_avg is None:
                ping_display = "Pending..."
            else:
                ping_display = f"{last_ping_avg:.2f} ms"

            # Print colored, in-place multi-line update
            print(CURSOR_HOME, end="")
            print(
                f"CPU Temp: {temp_col}{temp_c:5.2f}°C / {temp_f:5.2f}°F{RESET}{CLEAR_LINE}"
            )
            print(
                f"Fan Speed: {fan_display}{CLEAR_LINE}"
            )
            print(
                f"CPU Usage: {cpu_col}{cpu_usage:5.1f}%{RESET}{CLEAR_LINE}"
            )
            print(
                "Memory: "
                f"{format_bytes(mem_used_bytes)} used / {format_bytes(mem_total_bytes)} "
                f"({mem_used_percent:5.1f}%){CLEAR_LINE}"
            )
            print(
                "Storage: "
                f"{format_bytes(storage_used_bytes)} used / {format_bytes(storage_total_bytes)} "
                f"({storage_used_percent:5.1f}%){CLEAR_LINE}"
            )
            print(
                f"Network: ↑ {upload_display}   ↓ {download_display}{CLEAR_LINE}"
            )
            print(
                f"Ping (avg of 3 to 1.1.1.1): {ping_display}{CLEAR_LINE}",
                end="",
                flush=True,
            )

    except KeyboardInterrupt:
        # Move to next line on exit
        print()


if __name__ == "__main__":
    main()
