#!/usr/bin/env python3
import logging
import random
import subprocess
import time

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


def clear_terminal():
    """Clear the terminal window and move the cursor to the top left."""
    print(CLEAR_SCREEN, end="", flush=True)


def get_cpu_temp():
    """Read CPU temperature (°C) from system."""
    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
        millideg = int(f.read().strip())
    return millideg / 1000.0


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
    clear_terminal()

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

            # Calculate CPU usage
            idle, total = read_cpu_times()
            idle_delta  = idle  - prev_idle
            total_delta = total - prev_total
            prev_idle, prev_total = idle, total
            cpu_usage = (1.0 - idle_delta / total_delta) * 100.0 if total_delta else 0.0

            # Read and convert temperature
            temp_c = get_cpu_temp()
            temp_f = (temp_c * 9/5) + 32

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
                f"Net: ↑ {format_bytes_per_sec(tx_rate).strip()} ↓ {format_bytes_per_sec(rx_rate).strip()}",
            ]
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
                f"CPU Usage: {cpu_col}{cpu_usage:5.1f}%{RESET}{CLEAR_LINE}"
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
