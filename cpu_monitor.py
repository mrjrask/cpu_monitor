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
import json
import unicodedata
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
TERMINAL_COLS = 80
STORAGE_PREFIX = "💾  Storage: "

_needs_full_refresh = False


def _handle_resize(signum, frame):
    """Signal handler that flags the need for a full terminal refresh."""
    global _needs_full_refresh
    _needs_full_refresh = True


def clear_terminal():
    """Clear the terminal window and move the cursor to the top left."""
    print(CLEAR_SCREEN, end="", flush=True)


def resize_terminal(cols=TERMINAL_COLS, rows=14):
    """Request terminal resize via ANSI escape sequence when stdout is a TTY."""
    if not os.isatty(1):
        return
    # CSI 8 ; <rows> ; <cols> t  -> Resize terminal window in supporting emulators.
    print(f"\033[8;{rows};{cols}t", end="", flush=True)


def calculate_required_rows(storage_line_count, extra_metric_rows=0):
    """Calculate terminal rows required for the current rendered output."""
    base_rows = 15
    extra_storage_rows = max(storage_line_count - 1, 0)
    return base_rows + extra_storage_rows + extra_metric_rows




def display_width(text):
    """Return rendered terminal cell width for a string."""
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def clamp_line_width(text, max_cols):
    """Clamp text to a fixed terminal cell width, appending an ellipsis when truncated."""
    if max_cols <= 0:
        return ""

    if display_width(text) <= max_cols:
        return text

    if max_cols == 1:
        return "…"

    target_width = max_cols - 1
    out = []
    used = 0
    for ch in text:
        if unicodedata.combining(ch):
            out.append(ch)
            continue
        ch_width = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + ch_width > target_width:
            break
        out.append(ch)
        used += ch_width

    return "".join(out) + "…"


def read_pi_model():
    """Return the board model from device tree metadata, or None if unavailable."""
    model_paths = [
        "/proc/device-tree/model",
        "/sys/firmware/devicetree/base/model",
    ]

    for path in model_paths:
        try:
            with open(path, "rb") as f:
                model = f.read().rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            if model:
                return model
        except (FileNotFoundError, OSError):
            continue

CPU_TEMP_TYPE_KEYWORDS = ("cpu", "soc", "thermal", "x86_pkg_temp")


def find_cpu_temp_path():
    """Return the best matching CPU temperature sysfs path, or None."""
    for type_path in glob("/sys/class/thermal/thermal_zone*/type"):
        try:
            with open(type_path, "r") as f:
                zone_type = f.read().strip().lower()
        except (FileNotFoundError, OSError):
            continue

        if any(keyword in zone_type for keyword in CPU_TEMP_TYPE_KEYWORDS):
            return os.path.join(os.path.dirname(type_path), "temp")

    return None


def get_cpu_temp():
    """Read CPU temperature (°C) from system, or None if unavailable."""
    temp_path = find_cpu_temp_path()
    if temp_path is None:
        return None

    try:
        with open(temp_path, "r") as f:
            millideg = int(f.read().strip())
    except (FileNotFoundError, OSError, ValueError):
        return None

    return millideg / 1000.0


def read_pi_vcgencmd_temp():
    """Read Raspberry Pi SoC/GPU temperature (°C) via vcgencmd, if available."""
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
def read_cpu_frequency_mhz():
    """Return current CPU frequency in MHz, or None if unavailable."""
    sysfs_path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"

    try:
        with open(sysfs_path, "r") as f:
            freq_khz = int(f.read().strip())
        if freq_khz > 0:
            return freq_khz / 1000.0
    except (FileNotFoundError, OSError, ValueError):
        pass

    try:
        result = subprocess.run(
            ["vcgencmd", "measure_clock", "arm"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    match = re.search(r"temp=([+-]?\d+(?:\.\d+)?)'C", result.stdout.strip())
    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None

        freq_hz = int(match.group(1))
    except ValueError:
        return None

    return freq_hz / 1_000_000.0 if freq_hz > 0 else None


def read_fan_speed_rpm():
    """Return the first detected fan speed in RPM, or None if unavailable."""
    fan_paths = [
        "/sys/devices/platform/cooling_fan/hwmon/hwmon*/fan1_input",
        "/sys/class/hwmon/hwmon*/fan1_input",
    ]

    # Prefer true tachometer data when a driver exposes it through hwmon.
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


def read_fan_cooling_state():
    """Return the first fan-like thermal cooling-device state, or None.

    Some Raspberry Pi fan drivers expose a thermal cooling device instead of a
    tachometer. In that case there is no RPM value, but ``cur_state`` and
    ``max_state`` still describe the requested cooling level.
    """
    fan_type_keywords = ("fan", "pwm-fan", "gpio-fan")

    for type_path in glob("/sys/class/thermal/cooling_device*/type"):
        device_dir = os.path.dirname(type_path)
        try:
            with open(type_path, "r") as f:
                cooling_type = f.read().strip()
        except (FileNotFoundError, OSError):
            continue

        cooling_type_lower = cooling_type.lower()
        if not any(keyword in cooling_type_lower for keyword in fan_type_keywords):
            continue

        try:
            with open(os.path.join(device_dir, "cur_state"), "r") as f:
                cur_state = int(f.read().strip())
        except (FileNotFoundError, OSError, ValueError):
            continue

        max_state = None
        try:
            with open(os.path.join(device_dir, "max_state"), "r") as f:
                max_state = int(f.read().strip())
        except (FileNotFoundError, OSError, ValueError):
            pass

        time_in_state = {}
        try:
            with open(os.path.join(device_dir, "stats", "time_in_state"), "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        time_in_state[int(parts[0])] = int(parts[1])
        except (FileNotFoundError, OSError, ValueError):
            pass

        return {
            "type": cooling_type,
            "cur_state": cur_state,
            "max_state": max_state,
            "time_in_state": time_in_state,
            "path": device_dir,
        }

    return None


def format_fan_status(rpm, cooling_state=None):
    """Format fan RPM or cooling-state fallback for display."""
    if rpm is not None:
        return str(rpm)
    if cooling_state is None:
        return "N/A"

    cur_state = cooling_state["cur_state"]
    max_state = cooling_state.get("max_state")
    if max_state is not None:
        return f"state {cur_state}/{max_state}"
    return f"state {cur_state}"


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


def read_mounted_storage_details():
    """Return per-mount storage usage, excluding loop/zram devices."""
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,TYPE,SIZE,MOUNTPOINTS,PKNAME"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []

    try:
        devices = json.loads(result.stdout).get("blockdevices", [])
    except Exception:
        return []

    disk_sizes = {}
    details = []
    stack = list(devices)
    while stack:
        dev = stack.pop()
        stack.extend(dev.get("children") or [])

        name = dev.get("name")
        dev_type = dev.get("type")
        if not name:
            continue
        if name.startswith("loop") or name.startswith("zram"):
            continue

        try:
            size = int(dev.get("size", 0) or 0)
        except (TypeError, ValueError):
            size = 0
        if dev_type == "disk":
            disk_sizes[name] = size

        mountpoints = [mp for mp in (dev.get("mountpoints") or []) if mp]
        if not mountpoints:
            continue

        disk_name = dev.get("pkname") if dev_type in {"part", "lvm", "crypt"} else name
        disk_name = disk_name or name

        for mountpoint in mountpoints:
            try:
                usage = shutil.disk_usage(mountpoint)
            except OSError:
                continue
            details.append(
                {
                    "disk_name": disk_name,
                    "mountpoint": mountpoint,
                    "total": usage.total,
                    "free": usage.free,
                }
            )

    return sorted(details, key=lambda item: (item["disk_name"], item["mountpoint"]))


def read_pi_throttled_status():
    """Return Raspberry Pi throttling/undervoltage status text, or N/A if unavailable."""
    bit_messages = [
        (0, "Undervoltage now"),
        (1, "Frequency capped now"),
        (2, "Throttled now"),
        (3, "Soft temperature limit now"),
        (16, "Undervoltage occurred"),
        (17, "Frequency capped occurred"),
        (18, "Throttling occurred"),
        (19, "Soft temperature limit occurred"),
    ]

    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
    except FileNotFoundError:
        return "N/A"
    except Exception:
        return "N/A"

    if result.returncode != 0:
        return "N/A"

    match = re.search(r"throttled=0x([0-9a-fA-F]+)", result.stdout.strip())
    if not match:
        return "N/A"

    throttled_bits = int(match.group(1), 16)
    active_messages = [message for bit, message in bit_messages if throttled_bits & (1 << bit)]
    return ", ".join(active_messages) if active_messages else "OK"

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


def format_network_bits(num_bytes_per_sec):
    """Format throughput as a single human-readable bits/sec unit."""
    bits_per_sec = max(num_bytes_per_sec, 0.0) * 8.0
    if bits_per_sec >= 1_000_000.0:
        return f"{bits_per_sec / 1_000_000.0:8.2f} Mb/s"
    if bits_per_sec >= 1_000.0:
        return f"{bits_per_sec / 1_000.0:8.2f} Kb/s"
    return f"{bits_per_sec:8.2f} b/s"


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

    Dict keys: ssid, signal_dbm, signal_quality, channel, channel_width_mhz, wifi_standard.
    """
    details = {
        "ssid": None,
        "signal_dbm": None,
        "signal_quality": None,
        "channel": None,
        "channel_width_mhz": None,
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
            ssid_match = re.search(r"^\s*SSID:\s*(.+)$", link_result.stdout, flags=re.MULTILINE)
            if ssid_match:
                details["ssid"] = ssid_match.group(1).strip()
            details["wifi_standard"] = infer_wifi_standard_from_link(link_result.stdout)
    except Exception:
        pass

    return details


def main():
    global _needs_full_refresh

    hostname = socket.gethostname()
    board_model = read_pi_model()

    last_resize_rows = None
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
        "ssid": None,
        "signal_dbm": None,
        "signal_quality": None,
        "channel": None,
        "channel_width_mhz": None,
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
            try:
                temp_c = get_cpu_temp()
            except (FileNotFoundError, OSError, ValueError):
                temp_c = None
            pi_soc_temp_c = read_pi_vcgencmd_temp()
            display_temp_c = temp_c if temp_c is not None else pi_soc_temp_c
            display_temp_f = display_temp_c * 9 / 5 + 32 if display_temp_c is not None else None
            fan_rpm = read_fan_speed_rpm()
            fan_cooling_state = None if fan_rpm is not None else read_fan_cooling_state()
            cpu_freq_mhz = read_cpu_frequency_mhz()
            pi_health = read_pi_throttled_status()

            mem_total, mem_used = read_memory_usage()
            mem_pct = mem_used / mem_total * 100

            stor_details = read_mounted_storage_details()
            if stor_details:
                storage_lines = []
                for item in stor_details:
                    total = item["total"]
                    free = item["free"]
                    used_pct = ((total - free) / total * 100) if total else 0
                    storage_lines.append(
                        f"{item['mountpoint']} | {item['disk_name']} | "
                        f"{format_bytes(total)} | {format_bytes(free)} free ({used_pct:4.1f}% used)"
                    )
            else:
                stor_total, stor_used = read_storage_usage("/")
                stor_pct = stor_used / stor_total * 100
                storage_lines = [
                    f"/ | rootfs | {format_bytes(stor_total)} | "
                    f"{format_bytes(stor_total - stor_used)} free ({stor_pct:5.1f}% used)"
                ]

            required_rows = calculate_required_rows(
                len(storage_lines),
                extra_metric_rows=1 if temp_c is not None and pi_soc_temp_c is not None else 0,
            )
            if required_rows != last_resize_rows:
                resize_terminal(cols=TERMINAL_COLS, rows=required_rows)
                last_resize_rows = required_rows

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
                            "ssid": None,
                            "signal_dbm": None,
                            "signal_quality": None,
                            "channel": None,
                            "channel_width_mhz": None,
                            "wifi_standard": None,
                        }
                else:
                    connection_type = "Disconnected"
                    wifi_details = {
                        "ssid": None,
                        "signal_dbm": None,
                        "signal_quality": None,
                        "channel": None,
                        "channel_width_mhz": None,
                        "wifi_standard": None,
                    }
                next_network_details_time = now + 5

            print(CURSOR_HOME, end="")
            print(f"🖥️  Hostname: {hostname}{CLEAR_LINE}")
            print(f"🥧  Board: {board_model or 'N/A'}{CLEAR_LINE}")
            print(f"🌡️  CPU Temp: {color_for_temp(temp_c)}{temp_c:5.2f}°C / {temp_f:5.2f}°F{RESET}{CLEAR_LINE}")
            if display_temp_c is not None:
                print(
                    f"🌡️  CPU Temp: {color_for_temp(display_temp_c)}"
                    f"{display_temp_c:5.2f}°C / {display_temp_f:5.2f}°F{RESET}{CLEAR_LINE}"
                )
            else:
                print(f"🌡️  CPU Temp: N/A{CLEAR_LINE}")
            if temp_c is not None and pi_soc_temp_c is not None:
                pi_soc_temp_f = pi_soc_temp_c * 9 / 5 + 32
                print(
                    f"🔥  SoC Temp: {color_for_temp(pi_soc_temp_c)}"
                    f"{pi_soc_temp_c:5.2f}°C / {pi_soc_temp_f:5.2f}°F{RESET}{CLEAR_LINE}"
                )
            print(f"🌡️  CPU Temp: {temp_text}{CLEAR_LINE}")
            print(f"🌀  Fan Speed: {fan_rpm if fan_rpm is not None else 'N/A'}{CLEAR_LINE}")
            print(f"⚡  Pi Health: {pi_health}{CLEAR_LINE}")
            print(f"⚙️  CPU Usage: {color_for_cpu(cpu_usage)}{cpu_usage:5.1f}%{RESET}{CLEAR_LINE}")
            cpu_freq_text = f"{cpu_freq_mhz:.0f} MHz" if cpu_freq_mhz is not None else "N/A"
            print(f"⏱️  CPU Freq: {cpu_freq_text}{CLEAR_LINE}")
            print(f"🧠  Memory: {format_bytes(mem_used)} / {format_bytes(mem_total)} ({mem_pct:5.1f}%){CLEAR_LINE}")
            max_storage_chars = max(TERMINAL_COLS - display_width(STORAGE_PREFIX), 0)
            first_storage = clamp_line_width(storage_lines[0], max_storage_chars)
            print(f"{STORAGE_PREFIX}{first_storage}{CLEAR_LINE}")
            storage_indent = " " * display_width(STORAGE_PREFIX)
            for extra_line in storage_lines[1:]:
                clamped_line = clamp_line_width(extra_line, max_storage_chars)
                print(f"{storage_indent}{clamped_line}{CLEAR_LINE}")
            print(f"🌐  Network: ↑ {format_network_bits(tx_rate)}{CLEAR_LINE}")
            print(f"             ↓ {format_network_bits(rx_rate)}{CLEAR_LINE}")
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
                print(f"📶  Wi-Fi Network: {wifi_details['ssid'] or 'N/A'}{CLEAR_LINE}")
                print(f"📶  Wi-Fi Signal: {signal_text}{CLEAR_LINE}")
                width = wifi_details["channel_width_mhz"]
                channel = wifi_details["channel"]
                width_text = f" ({width} MHz)" if width else ""
                channel_text = f"{channel}{width_text}" if channel else "N/A"
                print(f"📡  Wi-Fi Channel: {channel_text}{CLEAR_LINE}")
            else:
                print(f"📶  Wi-Fi Network: N/A{CLEAR_LINE}")
                print(f"📶  Wi-Fi Signal: N/A{CLEAR_LINE}")
                print(f"📡  Wi-Fi Channel: N/A{CLEAR_LINE}")
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
