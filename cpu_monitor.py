#!/usr/bin/env python3
import logging
import os
import random
import signal
import subprocess
import time
import shutil
import socket
import re
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


def resize_terminal(cols=60, rows=13):
    """Request terminal resize via ANSI escape sequence when stdout is a TTY."""
    if not os.isatty(1):
        return
    # CSI 8 ; <rows> ; <cols> t  -> Resize terminal window in supporting emulators.
    print(f"\033[8;{rows};{cols}t", end="", flush=True)


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


def read_gpu_usage_percent():
    """Return GPU utilization percent when available, otherwise None."""
    def parse_gpu_busy_value(raw_value):
        """Parse a raw GPU busy string (e.g. '321', '32.1', '32%')."""
        match = re.search(r"[-+]?\d*\.?\d+", raw_value)
        if not match:
            return None

        value = float(match.group(0))

        # Heuristic:
        # - values in [0, 100] are usually already percentages
        # - larger values are often tenths of a percent
        if value > 100.0:
            value /= 10.0

        return max(0.0, min(100.0, value))

    # Common Linux GPU utilization paths:
    # - devfreq load files (often tenths of a percent on Raspberry Pi kernels)
    # - DRM gpu_busy_percent (already in whole percent on some drivers)
    candidate_paths = [
        *glob("/sys/class/devfreq/*gpu*/load"),
        *glob("/sys/class/devfreq/*v3d*/load"),
        *glob("/sys/class/drm/card*/device/gpu_busy_percent"),
        *glob("/sys/kernel/debug/dri/*/gpu_busy_percent"),
    ]

    # Add generic devfreq load paths when the device name/path suggests a GPU.
    gpu_keywords = ("gpu", "v3d", "mali", "panfrost", "adreno", "radeon", "nouveau")
    for devfreq_dir in glob("/sys/class/devfreq/*"):
        load_path = os.path.join(devfreq_dir, "load")
        if not os.path.exists(load_path):
            continue
        devfreq_name = os.path.basename(devfreq_dir).lower()
        real_path = os.path.realpath(devfreq_dir).lower()
        if any(keyword in devfreq_name or keyword in real_path for keyword in gpu_keywords):
            candidate_paths.append(load_path)

    # Keep order while removing duplicates.
    seen = set()
    deduped_candidate_paths = []
    for path in candidate_paths:
        if path in seen:
            continue
        seen.add(path)
        deduped_candidate_paths.append(path)

    for busy_path in deduped_candidate_paths:
        try:
            with open(busy_path, "r") as f:
                raw_value = f.read().strip()
            value = parse_gpu_busy_value(raw_value)
            if value is not None:
                return value
        except (FileNotFoundError, OSError, ValueError):
            continue

    return None


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


def get_active_interface():
    """Return the default outbound network interface name, or None."""
    try:
        result = subprocess.run(
            ["ip", "route", "get", "1.1.1.1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    fields = result.stdout.split()
    if "dev" in fields:
        dev_index = fields.index("dev") + 1
        if dev_index < len(fields):
            return fields[dev_index]
    return None


def is_wireless_interface(interface):
    """Return True when the interface appears to be a wireless NIC."""
    if not interface:
        return False
    return os.path.isdir(f"/sys/class/net/{interface}/wireless")


def read_wireless_signal_dbm(interface):
    """
    Read signal level in dBm from /proc/net/wireless for an interface.

    Returns None when unavailable.
    """
    if not interface:
        return None

    try:
        with open("/proc/net/wireless", "r") as f:
            for line in f.readlines()[2:]:
                if ":" not in line:
                    continue
                iface, values = line.split(":", 1)
                if iface.strip() != interface:
                    continue
                fields = values.split()
                if len(fields) >= 4:
                    # Level is typically reported as a negative dBm value.
                    return int(float(fields[2]))
    except (FileNotFoundError, OSError, ValueError):
        return None

    return None


def infer_wifi_standard_from_link(link_text):
    """Infer Wi-Fi generation (b/g/n/ac/ax) from `iw ... link` output."""
    if not link_text:
        return None

    text = link_text.upper()
    if "EHT-" in text:
        return "be"
    if "HE-" in text:
        return "ax"
    if "VHT-" in text:
        return "ac"
    if "HT-" in text:
        return "n"

    # No MCS hints found, infer from frequency as a rough fallback.
    freq_match = re.search(r"freq:\s*(\d+)", link_text, flags=re.IGNORECASE)
    if not freq_match:
        return None
    freq_mhz = int(freq_match.group(1))
    if freq_mhz < 2500:
        return "b/g"
    return "a"


def get_wifi_details(interface):
    """
    Return Wi-Fi details for a wireless interface.

    Dict keys: signal_dbm, signal_quality, channel, channel_width_mhz, frequency_mhz, wifi_standard.
    """
    details = {
        "signal_dbm": None,
        "signal_quality": None,
        "channel": None,
        "channel_width_mhz": None,
        "frequency_mhz": None,
        "wifi_standard": None,
    }
    if not interface:
        return details

    signal_dbm = read_wireless_signal_dbm(interface)
    details["signal_dbm"] = signal_dbm
    if signal_dbm is not None:
        # Clamp into a friendly percentage-style quality range.
        quality = int(max(0, min(100, 2 * (signal_dbm + 100))))
        details["signal_quality"] = quality

    try:
        info_result = subprocess.run(
            ["iw", "dev", interface, "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
        if info_result.returncode == 0:
            channel_match = re.search(r"\bchannel\s+(\d+)", info_result.stdout, flags=re.IGNORECASE)
            width_match = re.search(r"width:\s*(\d+)\s*MHz", info_result.stdout, flags=re.IGNORECASE)
            if channel_match:
                details["channel"] = channel_match.group(1)
            if width_match:
                details["channel_width_mhz"] = width_match.group(1)
    except Exception:
        pass

    try:
        link_result = subprocess.run(
            ["iw", "dev", interface, "link"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
        if link_result.returncode == 0:
            details["wifi_standard"] = infer_wifi_standard_from_link(link_result.stdout)
            freq_match = re.search(r"freq:\s*(\d+)", link_result.stdout, flags=re.IGNORECASE)
            if freq_match:
                details["frequency_mhz"] = freq_match.group(1)
    except Exception:
        pass

    return details


def main():
    global _needs_full_refresh

    hostname = socket.gethostname()

    resize_terminal(cols=60, rows=13)
    clear_terminal()
    signal.signal(signal.SIGWINCH, _handle_resize)

    prev_idle, prev_total = read_cpu_times()
    prev_rx, prev_tx = read_network_bytes()
    prev_time = time.monotonic()

    next_ping_time = prev_time + random.uniform(10, 40)
    last_ping_avg = None
    last_ping_error = None
    next_network_details_time = prev_time
    active_interface = None
    connection_type = None
    wifi_details = {
        "signal_dbm": None,
        "signal_quality": None,
        "channel": None,
        "channel_width_mhz": None,
        "frequency_mhz": None,
        "wifi_standard": None,
    }

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
            gpu_usage = read_gpu_usage_percent()

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

            if now >= next_network_details_time:
                active_interface = get_active_interface()
                if active_interface:
                    if is_wireless_interface(active_interface):
                        connection_type = "Wi-Fi"
                        wifi_details = get_wifi_details(active_interface)
                    else:
                        connection_type = "Ethernet/Other"
                        wifi_details = {
                            "signal_dbm": None,
                            "signal_quality": None,
                            "channel": None,
                            "channel_width_mhz": None,
                            "frequency_mhz": None,
                            "wifi_standard": None,
                        }
                else:
                    connection_type = "Disconnected"
                    wifi_details = {
                        "signal_dbm": None,
                        "signal_quality": None,
                        "channel": None,
                        "channel_width_mhz": None,
                        "frequency_mhz": None,
                        "wifi_standard": None,
                    }
                next_network_details_time = now + 5

            print(CURSOR_HOME, end="")
            print(f"🖥️  Hostname: {hostname}{CLEAR_LINE}")
            print(f"🌡️  CPU Temp: {color_for_temp(temp_c)}{temp_c:5.2f}°C / {temp_f:5.2f}°F{RESET}{CLEAR_LINE}")
            print(f"🌀  Fan Speed: {fan_rpm if fan_rpm is not None else 'N/A'}{CLEAR_LINE}")
            gpu_text = f"{gpu_usage:5.1f}%" if gpu_usage is not None else "N/A"
            print(
                f"⚙️  CPU Usage: {color_for_cpu(cpu_usage)}{cpu_usage:5.1f}%{RESET}  / 🎮  GPU: {gpu_text}{CLEAR_LINE}"
            )
            print(f"🧠  Memory: {format_bytes(mem_used)} / {format_bytes(mem_total)} ({mem_pct:5.1f}%){CLEAR_LINE}")
            print(f"💾  Storage: {format_bytes(stor_used)} / {format_bytes(stor_total)} ({stor_pct:5.1f}%){CLEAR_LINE}")
            print(f"🌐  Network: ↑ {format_bytes_per_sec(tx_rate)}   ↓ {format_bytes_per_sec(rx_rate)}{CLEAR_LINE}")
            print(
                f"🔌  Connection: {connection_type or 'Unknown'}"
                f"{f' ({active_interface})' if active_interface else ''}{CLEAR_LINE}"
            )
            if connection_type == "Wi-Fi":
                signal_text = (
                    f"{wifi_details['signal_dbm']} dBm ({wifi_details['signal_quality']}%)"
                    if wifi_details["signal_dbm"] is not None and wifi_details["signal_quality"] is not None
                    else "N/A"
                )
                print(f"📶  Wi-Fi Signal: {signal_text}{CLEAR_LINE}")
                width = wifi_details["channel_width_mhz"]
                channel = wifi_details["channel"]
                width_text = f" ({width} MHz)" if width else ""
                channel_text = f"{channel}{width_text}" if channel else "N/A"
                frequency = wifi_details["frequency_mhz"]
                frequency_text = f"{frequency} MHz" if frequency else "N/A"
                print(f"📡  Wi-Fi Channel/Freq: {channel_text}  / {frequency_text}{CLEAR_LINE}")
            else:
                print(f"📶  Wi-Fi Signal: N/A{CLEAR_LINE}")
                print(f"📡  Wi-Fi Channel/Freq: N/A  / N/A{CLEAR_LINE}")
            print(
                f"🏓  Ping (avg of 3 to 1.1.1.1): "
                f"{'ERROR - ' + last_ping_error if last_ping_error else f'{last_ping_avg:.2f} ms' if last_ping_avg else 'Pending...'}"
                f"{CLEAR_LINE}",
                end="",
                flush=True,
            )

    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
