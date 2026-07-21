#!/usr/bin/env python3
import argparse
import json
import logging
import os
import platform
import random
import re
import shlex
import shutil
import signal
import socket
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from glob import glob
from typing import Optional

# ANSI color codes
RESET = "\033[0m"
YELLOW = "\033[33m"
ORANGE = "\033[38;5;208m"
RED = "\033[31m"
PURPLE = "\033[35m"

# Configure logging to append timestamped entries to cpu_monitor.log
logging.basicConfig(
    filename="cpu_monitor.log",
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

CLEAR_SCREEN = "\033[2J\033[H"
CURSOR_HOME = "\033[H"
CLEAR_LINE = "\033[K"
TERMINAL_COLS = 80
STORAGE_PREFIX = "💾  Storage: "
COMPACT_COLS = 64
CPU_TEMP_TYPE_KEYWORDS = ("cpu", "soc", "thermal", "x86_pkg_temp")
DEFAULT_PING_INTERVAL_MIN_S = 60.0
DEFAULT_PING_INTERVAL_MAX_S = 600.0

_needs_full_refresh = False


@dataclass
class MonitorConfig:
    ping_target: str = "1.1.1.1"
    ping_count: int = 3
    ping_interval_min_s: float = DEFAULT_PING_INTERVAL_MIN_S
    ping_interval_max_s: float = DEFAULT_PING_INTERVAL_MAX_S
    ping_enabled: bool = True
    compact: bool = False
    temp_alert_c: float = 75.0
    alert_command: Optional[str] = None


def parse_args():
    """Parse monitor configuration from command-line flags."""
    parser = argparse.ArgumentParser(description="Terminal CPU monitor for Raspberry Pi, Linux, macOS, and Windows systems.")
    parser.add_argument("--ping-target", default="1.1.1.1", help="Host/IP to ping for latency checks.")
    parser.add_argument("--ping-count", type=int, default=3, help="Number of echo requests per ping check.")
    parser.add_argument(
        "--ping-interval-min",
        type=float,
        default=DEFAULT_PING_INTERVAL_MIN_S,
        metavar="SECONDS",
        help="Minimum seconds between periodic ping latency checks.",
    )
    parser.add_argument(
        "--ping-interval-max",
        type=float,
        default=DEFAULT_PING_INTERVAL_MAX_S,
        metavar="SECONDS",
        help="Maximum seconds between periodic ping latency checks.",
    )
    parser.add_argument("--no-ping", action="store_true", help="Disable periodic ping latency checks.")
    parser.add_argument("--compact", action="store_true", help="Use shorter, emoji-free output for small displays.")
    parser.add_argument("--temp-alert-c", type=float, default=75.0, help="Temperature threshold for alert hooks.")
    parser.add_argument(
        "--alert-command",
        help="Shell command to run when entering alert state. Environment includes CPU_MONITOR_ALERT_REASON.",
    )
    args = parser.parse_args()
    if args.ping_interval_min <= 0:
        parser.error("--ping-interval-min must be greater than 0")
    if args.ping_interval_max <= 0:
        parser.error("--ping-interval-max must be greater than 0")
    if args.ping_interval_max < args.ping_interval_min:
        parser.error("--ping-interval-max must be greater than or equal to --ping-interval-min")

    return MonitorConfig(
        ping_target=args.ping_target,
        ping_count=max(args.ping_count, 1),
        ping_interval_min_s=args.ping_interval_min,
        ping_interval_max_s=args.ping_interval_max,
        ping_enabled=not args.no_ping,
        compact=args.compact,
        temp_alert_c=args.temp_alert_c,
        alert_command=args.alert_command,
    )


def _handle_resize(signum, frame):
    """Signal handler that flags the need for a full terminal refresh."""
    global _needs_full_refresh
    _needs_full_refresh = True


def supports_ansi():
    """Return True when ANSI terminal control sequences should be emitted."""
    return os.name != "nt" or bool(os.environ.get("WT_SESSION") or os.environ.get("ANSICON") or os.environ.get("TERM"))


def enable_windows_virtual_terminal():
    """Enable ANSI escape sequence handling on modern Windows consoles when possible."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def clear_terminal():
    """Clear the terminal window and move the cursor to the top left."""
    if supports_ansi():
        print(CLEAR_SCREEN, end="", flush=True)
    else:
        os.system("cls" if os.name == "nt" else "clear")


def resize_terminal(cols=TERMINAL_COLS, rows=14):
    """Request terminal resize via ANSI escape sequence when stdout is a TTY."""
    if not os.isatty(1):
        return
    # CSI 8 ; <rows> ; <cols> t  -> Resize terminal window in supporting emulators.
    print(f"\033[8;{rows};{cols}t", end="", flush=True)


def calculate_required_rows(storage_line_count, show_soc_temp=False, compact=False):
    """Calculate terminal rows required for the current rendered output."""
    if compact:
        return 7
    base_rows = 18
    extra_storage_rows = max(storage_line_count, 0)
    return base_rows + extra_storage_rows + (1 if show_soc_temp else 0)


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
    """Return the hardware model or platform description, when available."""
    for path in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            with open(path, "rb") as f:
                model = f.read().rstrip(b"\x00").decode("utf-8", errors="replace").strip()
            if model:
                return model
        except (FileNotFoundError, OSError):
            continue
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(["sysctl", "-n", "hw.model"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            if result.returncode == 0 and result.stdout.strip():
                return f"Mac ({result.stdout.strip()})"
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            pass
    if platform.system() == "Windows":
        return platform.platform()
    return platform.platform() or None


def find_cpu_temp_path():
    """Return the best matching CPU temperature sysfs path, or None."""
    fallback = None
    for type_path in glob("/sys/class/thermal/thermal_zone*/type"):
        try:
            with open(type_path, "r") as f:
                zone_type = f.read().strip().lower()
        except (FileNotFoundError, OSError):
            continue
        temp_path = os.path.join(os.path.dirname(type_path), "temp")
        if fallback is None:
            fallback = temp_path
        if any(keyword in zone_type for keyword in CPU_TEMP_TYPE_KEYWORDS):
            return temp_path
    return fallback


def read_millidegree_temp(path):
    """Read a sysfs millidegree Celsius file as degrees Celsius."""
    try:
        with open(path, "r") as f:
            millideg = int(f.read().strip())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return millideg / 1000.0


def get_cpu_temp():
    """Read CPU temperature (°C) from system, or None if unavailable."""
    temp_path = find_cpu_temp_path()
    return read_millidegree_temp(temp_path) if temp_path else None


def read_pi_vcgencmd_temp():
    """Read Raspberry Pi SoC/GPU temperature (°C) via vcgencmd, if available."""
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
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


def read_cpu_frequency_mhz():
    """Return current CPU frequency in MHz, or None if unavailable."""
    system = platform.system()
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "r") as f:
            freq_khz = int(f.read().strip())
        if freq_khz > 0:
            return freq_khz / 1000.0
    except (FileNotFoundError, OSError, ValueError):
        pass

    if system == "Darwin":
        try:
            result = subprocess.run(["sysctl", "-n", "hw.cpufrequency"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            if result.returncode == 0:
                freq_hz = int(result.stdout.strip())
                return freq_hz / 1_000_000.0 if freq_hz > 0 else None
        except (FileNotFoundError, OSError, ValueError, subprocess.SubprocessError):
            pass
    if system == "Windows":
        try:
            result = subprocess.run(["wmic", "cpu", "get", "CurrentClockSpeed", "/value"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            if result.returncode == 0:
                match = re.search(r"CurrentClockSpeed=(\d+)", result.stdout)
                if match:
                    return float(match.group(1))
        except (FileNotFoundError, OSError, ValueError, subprocess.SubprocessError):
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
    match = re.search(r"frequency\(\d+\)=(\d+)", result.stdout.strip())
    if not match:
        return None
    try:
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
    """Return the first fan-like thermal cooling-device state, or None."""
    for type_path in glob("/sys/class/thermal/cooling_device*/type"):
        device_dir = os.path.dirname(type_path)
        try:
            with open(type_path, "r") as f:
                cooling_type = f.read().strip()
        except (FileNotFoundError, OSError):
            continue
        if not any(keyword in cooling_type.lower() for keyword in ("fan", "pwm-fan", "gpio-fan")):
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
        return {"type": cooling_type, "cur_state": cur_state, "max_state": max_state, "path": device_dir}
    return None


def format_fan_status(rpm, cooling_state=None):
    """Format fan RPM or cooling-state fallback for display."""
    if rpm is not None:
        return f"{rpm} RPM"
    if cooling_state is None:
        return "N/A"
    cur_state = cooling_state["cur_state"]
    max_state = cooling_state.get("max_state")
    return f"state {cur_state}/{max_state}" if max_state is not None else f"state {cur_state}"


def read_cpu_times():
    """Read aggregate CPU idle and total times across supported platforms."""
    try:
        with open("/proc/stat", "r") as f:
            parts = f.readline().split()[1:]
        times = list(map(int, parts))
        idle = times[3] + times[4]
        total = sum(times)
        return idle, total
    except (FileNotFoundError, OSError, ValueError, IndexError):
        pass

    system = platform.system()
    if system == "Darwin":
        try:
            result = subprocess.run(["sysctl", "-n", "kern.cp_time"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            if result.returncode == 0:
                user, nice, system_time, interrupt, idle = [int(value) for value in result.stdout.split()[:5]]
                return idle, user + nice + system_time + interrupt + idle
        except (FileNotFoundError, OSError, ValueError, subprocess.SubprocessError):
            pass

    if system == "Windows":
        try:
            import ctypes

            idle_time = ctypes.c_ulonglong()
            kernel_time = ctypes.c_ulonglong()
            user_time = ctypes.c_ulonglong()
            if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return idle_time.value, kernel_time.value + user_time.value
        except Exception:
            pass

    times = os.times()
    idle = int(getattr(times, "elapsed", 0) * os.cpu_count())
    total = idle + int((times.user + times.system + times.children_user + times.children_system) * 100)
    return idle, total


def read_network_bytes():
    """Return total received and transmitted bytes for non-loopback interfaces."""
    total_rx = 0
    total_tx = 0
    try:
        with open("/proc/net/dev", "r") as f:
            for line in f.readlines()[2:]:
                iface, data = line.split(":", 1)
                if iface.strip() == "lo":
                    continue
                fields = data.split()
                total_rx += int(fields[0])
                total_tx += int(fields[8])
        return total_rx, total_tx
    except (FileNotFoundError, OSError, ValueError, IndexError):
        pass

    if platform.system() == "Darwin":
        try:
            result = subprocess.run(["netstat", "-ibn"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            if result.returncode == 0:
                seen = set()
                for line in result.stdout.splitlines()[1:]:
                    fields = line.split()
                    if len(fields) >= 9 and fields[0] != "lo0" and fields[0] not in seen:
                        seen.add(fields[0])
                        total_rx += int(fields[-5])
                        total_tx += int(fields[-2])
                return total_rx, total_tx
        except (FileNotFoundError, OSError, ValueError, subprocess.SubprocessError):
            pass

    if platform.system() == "Windows":
        try:
            result = subprocess.run(["netstat", "-e"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    fields = line.split()
                    if len(fields) == 3 and fields[0].lower() == "bytes":
                        return int(fields[1]), int(fields[2])
        except (FileNotFoundError, OSError, ValueError, subprocess.SubprocessError):
            pass
    return 0, 0


def linux_whole_block_devices():
    """Return Linux top-layer whole-block devices to avoid double-counting stacks."""
    devices = set()
    try:
        block_paths = glob("/sys/block/*")
    except OSError:
        return devices

    for path in block_paths:
        name = os.path.basename(path)
        if name.startswith(("loop", "zram", "ram")):
            continue
        try:
            if os.listdir(os.path.join(path, "holders")):
                continue
        except OSError:
            pass
        devices.add(name)
    return devices


def read_storage_io_bytes():
    """Return total storage read and write bytes across physical block devices."""
    total_read = 0
    total_write = 0
    try:
        whole_devices = linux_whole_block_devices()
        with open("/proc/diskstats", "r") as f:
            for line in f:
                fields = line.split()
                if len(fields) < 14:
                    continue
                name = fields[2]
                if whole_devices and name not in whole_devices:
                    continue
                if not whole_devices and name.startswith(("loop", "zram", "ram")):
                    continue
                try:
                    read_sectors = int(fields[5])
                    write_sectors = int(fields[9])
                except ValueError:
                    continue
                total_read += read_sectors * 512
                total_write += write_sectors * 512
        return total_read, total_write
    except (FileNotFoundError, OSError):
        pass

    if platform.system() == "Darwin":
        try:
            result = subprocess.run(["iostat", "-Id"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    fields = line.split()
                    if len(fields) >= 4 and fields[0].startswith("disk"):
                        try:
                            total_read += int(float(fields[2]) * 1024 * 1024)
                            total_write += int(float(fields[3]) * 1024 * 1024)
                        except ValueError:
                            continue
                return total_read, total_write
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            pass

    return 0, 0

def read_memory_usage():
    """Return total and used memory in bytes."""
    mem_total = None
    mem_available = None
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1]) * 1024
                if mem_total and mem_available:
                    break
        if mem_total is not None and mem_available is not None:
            return mem_total, max(mem_total - mem_available, 0)
    except (FileNotFoundError, OSError, ValueError):
        pass

    if platform.system() == "Darwin":
        try:
            total_result = subprocess.run(["sysctl", "-n", "hw.memsize"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            page_result = subprocess.run(["pagesize"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            vm_result = subprocess.run(["vm_stat"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            if total_result.returncode == page_result.returncode == vm_result.returncode == 0:
                total = int(total_result.stdout.strip())
                page_size = int(page_result.stdout.strip())
                free_pages = 0
                for line in vm_result.stdout.splitlines():
                    if line.startswith(("Pages free:", "Pages speculative:")):
                        free_pages += int(re.sub(r"[^0-9]", "", line))
                return total, max(total - free_pages * page_size, 0)
        except (FileNotFoundError, OSError, ValueError, subprocess.SubprocessError):
            pass

    if platform.system() == "Windows":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys, max(status.ullTotalPhys - status.ullAvailPhys, 0)
        except Exception:
            pass
    return 0, 0


def read_storage_usage(path="/"):
    """Return total and used storage in bytes for the given path."""
    usage = shutil.disk_usage(path)
    return usage.total, max(usage.total - usage.free, 0)


def is_excluded_storage_mount(mountpoint, fstype=None):
    """Return True for swap or firmware mounts that should not be shown as storage."""
    normalized_mount = str(mountpoint or "").strip()
    normalized_type = str(fstype or "").strip().lower()
    if not normalized_mount:
        return True
    if normalized_mount == "[SWAP]" or normalized_type == "swap":
        return True
    firmware_mounts = ("/boot/firmware", "/firmware")
    return any(normalized_mount == mount or normalized_mount.startswith(f"{mount}/") for mount in firmware_mounts)


def read_mounted_storage_details():
    """Return per-mount storage usage, excluding loop/zram, swap, and firmware devices."""
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,TYPE,SIZE,MOUNTPOINTS,PKNAME,FSTYPE"],
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
    details = []
    stack = list(devices)
    while stack:
        dev = stack.pop()
        stack.extend(dev.get("children") or [])
        name = dev.get("name")
        dev_type = dev.get("type")
        if not name or name.startswith(("loop", "zram")):
            continue
        mountpoints_value = dev.get("mountpoints") or []
        if isinstance(mountpoints_value, str):
            mountpoints_value = [mountpoints_value]
        mountpoints = [mp for mp in mountpoints_value if not is_excluded_storage_mount(mp, dev.get("fstype"))]
        if not mountpoints:
            continue
        disk_name = dev.get("pkname") if dev_type in {"part", "lvm", "crypt"} else name
        disk_name = disk_name or name
        for mountpoint in mountpoints:
            try:
                usage = shutil.disk_usage(mountpoint)
                fs_id = os.stat(mountpoint).st_dev
            except OSError:
                continue
            details.append({"disk_name": disk_name, "mountpoint": mountpoint, "total": usage.total, "free": usage.free, "fs_id": fs_id})
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
        result = subprocess.run(["vcgencmd", "get_throttled"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return "N/A"
    if result.returncode != 0:
        return "N/A"
    match = re.search(r"throttled=0x([0-9a-fA-F]+)", result.stdout.strip())
    if not match:
        return "N/A"
    throttled_bits = int(match.group(1), 16)
    active_messages = [message for bit, message in bit_messages if throttled_bits & (1 << bit)]
    return ", ".join(active_messages) if active_messages else "OK"


def pi_health_is_alert(pi_health):
    """Return True when Raspberry Pi health text indicates an active alert."""
    if not pi_health or pi_health in {"OK", "N/A"}:
        return False
    return any(token in pi_health for token in (" now", "Undervoltage", "Throttled", "Throttling", "Soft temperature"))


def maybe_run_alert(config, reasons, alert_active):
    """Run an alert command once when crossing into an alert state."""
    if not config.alert_command or not reasons or alert_active:
        return bool(reasons)
    env = os.environ.copy()
    env["CPU_MONITOR_ALERT_REASON"] = "; ".join(reasons)
    try:
        subprocess.Popen(shlex.split(config.alert_command), env=env)
        logging.info("Alert command started: %s | %s", config.alert_command, env["CPU_MONITOR_ALERT_REASON"])
    except Exception as exc:
        logging.info("Alert command failed: %s", exc)
    return True


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


def format_temp(temp_c):
    """Format Celsius/Fahrenheit temperature, or N/A."""
    if temp_c is None:
        return "N/A"
    return f"{temp_c:5.2f}°C / {temp_c * 9 / 5 + 32:5.2f}°F"


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
    return f"{value:7.2f} PB"


def run_ping(target, count):
    ping_cmd = ["ping", "-n", str(count), target] if platform.system() == "Windows" else ["ping", "-c", str(count), "-n", target]
    try:
        result = subprocess.run(
            ping_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(5, count * 5),
        )
    except Exception as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, result.stderr.strip() or result.stdout.strip()
    for line in result.stdout.splitlines():
        if "rtt" in line or "round-trip" in line:
            try:
                stats = line.split("=")[1].split()[0].split("/")
                return float(stats[1]), None
            except Exception:
                return None, "parse error"
        average_match = re.search(r"Average\s*=\s*(\d+(?:\.\d+)?)\s*ms", line, flags=re.IGNORECASE)
        if average_match:
            return float(average_match.group(1)), None
    return None, "no stats found"


def get_active_interface(route_target="1.1.1.1"):
    """Return the default outbound network interface name, or None."""
    system = platform.system()
    if system == "Windows":
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        f"$target = '{route_target.replace("'", "''")}'; "
                        "$ip = [System.Net.Dns]::GetHostAddresses($target) | "
                        "Where-Object { $_.AddressFamily -in 'InterNetwork','InterNetworkV6' } | "
                        "Select-Object -First 1; "
                        "if ($ip) { (Find-NetRoute -RemoteIPAddress $ip.IPAddressToString | "
                        "Select-Object -First 1).InterfaceAlias }"
                    ),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().splitlines()[0]
        except Exception:
            return None
        return None

    command = ["route", "get", route_target] if system == "Darwin" else ["ip", "route", "get", route_target]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    fields = result.stdout.split()
    if "dev" in fields:
        dev_index = fields.index("dev") + 1
        if dev_index < len(fields):
            return fields[dev_index]
    if "interface:" in result.stdout:
        match = re.search(r"interface:\s*(\S+)", result.stdout)
        if match:
            return match.group(1)
    return None


def is_wireless_interface(interface):
    """Return True when the OS reports the interface as a wireless NIC."""
    if not interface:
        return False
    if os.path.isdir(f"/sys/class/net/{interface}/wireless"):
        return True

    system = platform.system()
    if system == "Darwin":
        try:
            result = subprocess.run(
                ["networksetup", "-listallhardwareports"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
        hardware_port = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("Hardware Port:"):
                hardware_port = line.split(":", 1)[1].strip().lower()
            elif line.startswith("Device:"):
                device = line.split(":", 1)[1].strip()
                if device == interface and hardware_port in {"wi-fi", "airport"}:
                    return True
        return False

    if system == "Windows":
        escaped_interface = interface.replace("'", "''")
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-NetAdapter -Name '{escaped_interface}' -ErrorAction SilentlyContinue).NdisPhysicalMedium",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
        media_type = result.stdout.strip().lower()
        return any(token in media_type for token in ("802.11", "wireless", "wi-fi", "wifi"))

    return False


def read_wireless_signal_dbm(interface):
    """Read signal level in dBm from /proc/net/wireless for an interface."""
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
                    return int(float(fields[2]))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return None


def infer_wifi_standard_from_link(link_text):
    """Infer Wi-Fi generation (b/g/n/ac/ax/be) from `iw ... link` output."""
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
    freq_match = re.search(r"freq:\s*(\d+)", link_text, flags=re.IGNORECASE)
    if not freq_match:
        return None
    return "b/g" if int(freq_match.group(1)) < 2500 else "a"


def empty_wifi_details():
    return {"ssid": None, "signal_dbm": None, "signal_quality": None, "channel": None, "channel_width_mhz": None, "wifi_standard": None}


def get_wifi_details(interface):
    """Return Wi-Fi details for a wireless interface."""
    details = empty_wifi_details()
    if not interface:
        return details
    signal_dbm = read_wireless_signal_dbm(interface)
    details["signal_dbm"] = signal_dbm
    if signal_dbm is not None:
        details["signal_quality"] = int(max(0, min(100, 2 * (signal_dbm + 100))))
    try:
        info_result = subprocess.run(["iw", "dev", interface, "info"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
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
        link_result = subprocess.run(["iw", "dev", interface, "link"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        if link_result.returncode == 0:
            ssid_match = re.search(r"^\s*SSID:\s*(.+)$", link_result.stdout, flags=re.MULTILINE)
            if ssid_match:
                details["ssid"] = ssid_match.group(1).strip()
            details["wifi_standard"] = infer_wifi_standard_from_link(link_result.stdout)
    except Exception:
        pass
    return details


def fit_table_cell(text, width, align="left"):
    """Fit text inside a table cell, preserving the requested rendered width."""
    text = clamp_line_width(str(text), width)
    padding = max(width - display_width(text), 0)
    if align == "right":
        return " " * padding + text
    return text + " " * padding


def build_storage_lines(read_rate=0, write_rate=0):
    """Build storage dashboard table lines with one row per mounted device."""
    stor_details = read_mounted_storage_details()
    if not stor_details:
        stor_total, stor_used = read_storage_usage("/")
        stor_details = [{"disk_name": "rootfs", "mountpoint": "/", "total": stor_total, "free": stor_total - stor_used}]

    unique_details = {}
    for item in sorted(stor_details, key=lambda item: (item["disk_name"], item["mountpoint"])):
        fs_key = item.get("fs_id", (item.get("disk_name"), item.get("total"), item.get("free")))
        unique_details.setdefault(fs_key, item)

    columns = [
        ("Volume Name", 11, "left"),
        ("Location", 13, "left"),
        ("Used", 9, "right"),
        ("Free", 9, "right"),
        ("% Free", 6, "right"),
        ("Write/s", 10, "right"),
        ("Read/s", 10, "right"),
    ]

    def table_line(values):
        return " ".join(
            fit_table_cell(value, width, align)
            for value, (_, width, align) in zip(values, columns)
        )

    lines = [table_line([heading for heading, _, _ in columns])]
    lines.append(table_line(["-" * width for _, width, _ in columns]))
    for item in unique_details.values():
        item_total = item["total"]
        item_free = item["free"]
        item_used = max(item_total - item_free, 0)
        item_free_pct = item_free / item_total * 100 if item_total else 0
        lines.append(
            table_line(
                [
                    item["disk_name"],
                    item["mountpoint"],
                    format_bytes(item_used).strip(),
                    format_bytes(item_free).strip(),
                    f"{item_free_pct:5.1f}%",
                    f"{format_bytes(write_rate).strip()}/s",
                    f"{format_bytes(read_rate).strip()}/s",
                ]
            )
        )
    return lines


def render_full_dashboard(state):
    """Render the full emoji dashboard."""
    print(CURSOR_HOME, end="")
    print(f"🖥️  Hostname: {state['hostname']}{CLEAR_LINE}")
    print(f"🥧  Board: {state['board_model'] or 'N/A'}{CLEAR_LINE}")
    if state["display_temp_c"] is not None:
        print(f"🌡️  CPU Temp: {color_for_temp(state['display_temp_c'])}{format_temp(state['display_temp_c'])}{RESET}{CLEAR_LINE}")
    else:
        print(f"🌡️  CPU Temp: N/A{CLEAR_LINE}")
    if state["temp_c"] is not None and state["pi_soc_temp_c"] is not None:
        print(f"🔥  SoC Temp: {color_for_temp(state['pi_soc_temp_c'])}{format_temp(state['pi_soc_temp_c'])}{RESET}{CLEAR_LINE}")
    print(f"🌀  Fan Speed: {state['fan_status']}{CLEAR_LINE}")
    print(f"⚡  Pi Health: {state['pi_health']}{CLEAR_LINE}")
    print(f"⚙️  CPU Usage: {color_for_cpu(state['cpu_usage'])}{state['cpu_usage']:5.1f}%{RESET}{CLEAR_LINE}")
    print(f"⏱️  CPU Freq: {state['cpu_freq_text']}{CLEAR_LINE}")
    print(f"🧠  Memory: {format_bytes(state['mem_used'])} / {format_bytes(state['mem_total'])} ({state['mem_pct']:5.1f}%){CLEAR_LINE}")
    print(f"{STORAGE_PREFIX.rstrip()}{CLEAR_LINE}")
    max_storage_chars = max(TERMINAL_COLS - 2, 0)
    for storage_line in state["storage_lines"]:
        print(f"  {clamp_line_width(storage_line, max_storage_chars)}{CLEAR_LINE}")
    print(f"🌐  Network: ↑ {format_network_bits(state['tx_rate'])}{CLEAR_LINE}")
    print(f"             ↓ {format_network_bits(state['rx_rate'])}{CLEAR_LINE}")
    interface_suffix = f" ({state['active_interface']})" if state['active_interface'] else ""
    print(f"🔌  Connection: {state['connection_type'] or 'Unknown'}{interface_suffix}{CLEAR_LINE}")
    if state["connection_type"] == "Wi-Fi":
        wifi_details = state["wifi_details"]
        signal_text = (
            f"{wifi_details['signal_dbm']} dBm ({wifi_details['signal_quality']}%)"
            if wifi_details["signal_dbm"] is not None and wifi_details["signal_quality"] is not None
            else "N/A"
        )
        width = wifi_details["channel_width_mhz"]
        channel = wifi_details["channel"]
        channel_text = f"{channel}{f' ({width} MHz)' if width else ''}" if channel else "N/A"
        print(f"📶  Wi-Fi Network: {wifi_details['ssid'] or 'N/A'}{CLEAR_LINE}")
        print(f"📶  Wi-Fi Signal: {signal_text}{CLEAR_LINE}")
        print(f"📡  Wi-Fi Channel: {channel_text}{CLEAR_LINE}")
    else:
        print(f"📶  Wi-Fi Network: N/A{CLEAR_LINE}")
        print(f"📶  Wi-Fi Signal: N/A{CLEAR_LINE}")
        print(f"📡  Wi-Fi Channel: N/A{CLEAR_LINE}")
    print(f"🏓  Ping ({state['ping_label']}): {state['ping_text']}{CLEAR_LINE}", end="", flush=True)


def render_compact_dashboard(state):
    """Render compact emoji-free output for small terminals and displays."""
    print(CURSOR_HOME, end="")
    temp_text = f"{state['display_temp_c']:.1f}C" if state["display_temp_c"] is not None else "N/A"
    soc_text = f" SOC {state['pi_soc_temp_c']:.1f}C" if state["temp_c"] is not None and state["pi_soc_temp_c"] is not None else ""
    print(clamp_line_width(f"HOST {state['hostname']} | {state['board_model'] or 'N/A'}", COMPACT_COLS) + CLEAR_LINE)
    print(clamp_line_width(f"CPU {temp_text}{soc_text} {state['cpu_usage']:.1f}% {state['cpu_freq_text']}", COMPACT_COLS) + CLEAR_LINE)
    print(clamp_line_width(f"PI {state['pi_health']} | FAN {state['fan_status']}", COMPACT_COLS) + CLEAR_LINE)
    print(clamp_line_width(f"MEM {state['mem_pct']:.1f}% | DISK {state['storage_lines'][0]}", COMPACT_COLS) + CLEAR_LINE)
    print(clamp_line_width(f"NET up {format_network_bits(state['tx_rate']).strip()} down {format_network_bits(state['rx_rate']).strip()}", COMPACT_COLS) + CLEAR_LINE)
    print(clamp_line_width(f"CONN {state['connection_type'] or 'Unknown'} {state['active_interface'] or ''}", COMPACT_COLS) + CLEAR_LINE)
    print(clamp_line_width(f"PING {state['ping_label']}: {state['ping_text']}", COMPACT_COLS) + CLEAR_LINE, end="", flush=True)


def main():
    global _needs_full_refresh
    config = parse_args()
    hostname = socket.gethostname()
    board_model = read_pi_model()
    route_target = config.ping_target if config.ping_enabled else "1.1.1.1"

    last_resize_rows = None
    alert_active = False
    enable_windows_virtual_terminal()
    clear_terminal()
    if hasattr(signal, "SIGWINCH"):
        signal.signal(signal.SIGWINCH, _handle_resize)

    prev_idle, prev_total = read_cpu_times()
    prev_rx, prev_tx = read_network_bytes()
    prev_storage_read, prev_storage_write = read_storage_io_bytes()
    prev_time = time.monotonic()

    next_ping_time = prev_time + random.uniform(config.ping_interval_min_s, config.ping_interval_max_s) if config.ping_enabled else float("inf")
    last_ping_avg = None
    last_ping_error = None
    next_network_details_time = prev_time
    active_interface = None
    connection_type = None
    wifi_details = empty_wifi_details()

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
            pi_soc_temp_c = read_pi_vcgencmd_temp()
            display_temp_c = temp_c if temp_c is not None else pi_soc_temp_c
            fan_rpm = read_fan_speed_rpm()
            fan_status = format_fan_status(fan_rpm, None if fan_rpm is not None else read_fan_cooling_state())
            cpu_freq_mhz = read_cpu_frequency_mhz()
            cpu_freq_text = f"{cpu_freq_mhz:.0f} MHz" if cpu_freq_mhz is not None else "N/A"
            pi_health = read_pi_throttled_status()

            alert_reasons = []
            if display_temp_c is not None and display_temp_c >= config.temp_alert_c:
                alert_reasons.append(f"temperature {display_temp_c:.1f}C >= {config.temp_alert_c:.1f}C")
            if pi_health_is_alert(pi_health):
                alert_reasons.append(f"Pi health: {pi_health}")
            alert_active = maybe_run_alert(config, alert_reasons, alert_active)
            if not alert_reasons:
                alert_active = False

            mem_total, mem_used = read_memory_usage()
            mem_pct = mem_used / mem_total * 100 if mem_total else 0

            storage_read, storage_write = read_storage_io_bytes()
            elapsed = max(now - prev_time, 0.001)
            storage_read_rate = max(storage_read - prev_storage_read, 0) / elapsed
            storage_write_rate = max(storage_write - prev_storage_write, 0) / elapsed
            prev_storage_read, prev_storage_write = storage_read, storage_write
            storage_lines = build_storage_lines(storage_read_rate, storage_write_rate)

            required_rows = calculate_required_rows(len(storage_lines), temp_c is not None and pi_soc_temp_c is not None, config.compact)
            cols = COMPACT_COLS if config.compact else TERMINAL_COLS
            if required_rows != last_resize_rows:
                resize_terminal(cols=cols, rows=required_rows)
                last_resize_rows = required_rows

            rx, tx = read_network_bytes()
            elapsed = max(now - prev_time, 0.001)
            rx_rate = (rx - prev_rx) / elapsed
            tx_rate = (tx - prev_tx) / elapsed
            prev_rx, prev_tx, prev_time = rx, tx, now

            if config.ping_enabled and now >= next_ping_time:
                last_ping_avg, last_ping_error = run_ping(config.ping_target, config.ping_count)
                next_ping_time = now + random.uniform(config.ping_interval_min_s, config.ping_interval_max_s)

            if now >= next_network_details_time:
                active_interface = get_active_interface(route_target)
                if active_interface and is_wireless_interface(active_interface):
                    connection_type = "Wi-Fi"
                    wifi_details = get_wifi_details(active_interface)
                elif active_interface:
                    connection_type = "Ethernet/Other"
                    wifi_details = empty_wifi_details()
                else:
                    connection_type = "Disconnected"
                    wifi_details = empty_wifi_details()
                next_network_details_time = now + 5

            if not config.ping_enabled:
                ping_text = "Disabled"
                ping_label = "disabled"
            elif last_ping_error:
                ping_text = "ERROR - " + last_ping_error
                ping_label = f"avg of {config.ping_count} to {config.ping_target}"
            elif last_ping_avg is not None:
                ping_text = f"{last_ping_avg:.2f} ms"
                ping_label = f"avg of {config.ping_count} to {config.ping_target}"
            else:
                ping_text = "Pending..."
                ping_label = f"avg of {config.ping_count} to {config.ping_target}"

            state = {
                "hostname": hostname,
                "board_model": board_model,
                "temp_c": temp_c,
                "pi_soc_temp_c": pi_soc_temp_c,
                "display_temp_c": display_temp_c,
                "fan_status": fan_status,
                "pi_health": pi_health,
                "cpu_usage": cpu_usage,
                "cpu_freq_text": cpu_freq_text,
                "mem_total": mem_total,
                "mem_used": mem_used,
                "mem_pct": mem_pct,
                "storage_lines": storage_lines,
                "tx_rate": tx_rate,
                "rx_rate": rx_rate,
                "active_interface": active_interface,
                "connection_type": connection_type,
                "wifi_details": wifi_details,
                "ping_label": ping_label,
                "ping_text": ping_text,
            }
            if config.compact:
                render_compact_dashboard(state)
            else:
                render_full_dashboard(state)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
