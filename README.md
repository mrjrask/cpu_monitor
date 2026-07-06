# Raspberry Pi CPU Monitor

A lightweight, terminal-based system monitor for Raspberry Pi and Linux systems.

This script shows real-time board identification, CPU temperature, CPU utilization, fan speed, memory/storage usage, network throughput, connection details, Wi-Fi network name/metrics, and periodic ping latency in a compact dashboard.

---

## Features

- **Live terminal dashboard** with 1-second refresh intervals.
- **Board identification** from Raspberry Pi / Linux device tree metadata.
- **CPU temperature** in °C and °F with colorized thermal thresholds.
- **Raspberry Pi SoC/GPU temperature** via `vcgencmd measure_temp` when available, shown separately when sysfs CPU temperature is also available or used as a fallback when sysfs is missing.
- **CPU usage** with colorized load thresholds.
- **Fan RPM/state** detection from common hwmon paths and fan-like thermal cooling devices.
- **Memory and storage** usage with human-readable units.
- **Network throughput** shown as bits, kilobits, and megabits per second for TX/RX.
- **Connection detection** (Wi-Fi vs Ethernet/Other vs Disconnected).
- **Wi-Fi details** when connected wirelessly:
  - connected network name (SSID)
  - signal level (dBm + derived quality %)
  - channel + channel width
  - inferred Wi-Fi standard (rough heuristic)
- **Periodic latency checks** by running ping to `1.1.1.1` (average of 3 pings).
- **Hostname display** and terminal-resize handling for cleaner redraws.
- **Logging support** via `cpu_monitor.log` (timestamped log format configured).

---

## Requirements

### Hardware / OS

- Raspberry Pi (recommended) or Linux system with compatible proc/sysfs interfaces.
- Linux kernel exposing common files like:
  - `/proc/stat`
  - `/proc/meminfo`
  - `/proc/net/dev`
  - `/sys/class/thermal/thermal_zone0/temp`
  - `/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq` (optional CPU frequency source)

### Software

- **Python 3** (no third-party Python packages required).
- System commands used by the script:
  - `ping`
  - `ip` (from `iproute2`)
  - `iw` (for Wi-Fi details)
  - `vcgencmd` (optional, Raspberry Pi firmware command used for SoC/GPU temperature)

> If `ip` or `iw` are missing, the script still runs, but some network/Wi-Fi details may show as unavailable. If `vcgencmd` is missing, the script skips the Raspberry Pi SoC/GPU temperature reading and continues using sysfs CPU temperature when available.

---

## Installation

Clone or copy the project onto your Raspberry Pi / Linux host:

```bash
git clone <your-repo-url>
cd Rpi_cpu_monitor
```

Make the script executable (optional):

```bash
chmod +x cpu_monitor.py
```

---

## Usage

Run directly with Python:

```bash
python3 cpu_monitor.py
```

Or run as an executable:

```bash
./cpu_monitor.py
```

Stop with `Ctrl+C`.

---

## Dashboard Fields

- `Hostname`: system hostname.
- `Board`: board model reported by device tree metadata, or `N/A`.
- `CPU Temp`: CPU die temperature in °C / °F.
- `Fan Speed`: first detected fan RPM, or `N/A`.
- `Pi Health`: Raspberry Pi throttling/undervoltage status from `vcgencmd get_throttled`, `OK` when no common flags are set, or `N/A` when unavailable.
- `CPU Usage`: aggregate CPU utilization percentage.
- `CPU Freq`: current CPU frequency in MHz, read from sysfs or `vcgencmd`; displays `N/A` if unavailable.
- `Memory`: used / total RAM and percentage.
- `Storage`: used / total storage for `/` and percentage.
- `Network`: transmit (`↑`) and receive (`↓`) rates in `b/s`, `Kb/s`, and `Mb/s`.
- `Connection`: active outbound interface and type.
- `Wi-Fi Network`: connected wireless network name / SSID (Wi-Fi only).
- `Wi-Fi Signal`: dBm and derived quality % (Wi-Fi only).
- `Wi-Fi Channel`: channel with optional channel width (Wi-Fi only).
- `Ping`: average round-trip time from 3 pings to `1.1.1.1`, refreshed at random intervals.

---

## Color Thresholds

### CPU usage color

- `< 30%`: default terminal color
- `30–49.9%`: yellow
- `50–69.9%`: orange
- `70–89.9%`: red
- `>= 90%`: purple

### Temperature color

- `< 60°C`: default terminal color
- `60–67.9°C`: yellow
- `68–74.9°C`: orange
- `>= 75°C`: red

---

## Notes on Metric Detection

Because Linux hardware interfaces vary by board, kernel, and distro, some metrics are best-effort:

- **CPU frequency**: prefers `/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq` in kHz, then falls back to `vcgencmd measure_clock arm` in Hz.
- **Fan speed**: checks common `fan1_input` paths under hwmon.
- **Raspberry Pi SoC/GPU temperature**: optionally runs `vcgencmd measure_temp` and parses output like `temp=52.1'C`.
- **Wi-Fi details**: depends on interface support and `iw` output format.
- **Ping**: requires network reachability and permission to run `ping`.
- **Pi Health**: requires the optional Raspberry Pi `vcgencmd` command; without it, this field displays `N/A`.

If a metric cannot be collected, the dashboard displays `N/A` rather than failing.

---

## Troubleshooting

### `CPU Temp` shows `N/A`

The monitor auto-detects CPU-related thermal zones by checking `/sys/class/thermal/thermal_zone*/type` for names such as `cpu-thermal`, `soc-thermal`, and `x86_pkg_temp`, then reading the sibling `temp` file.

- Confirm thermal zones exist and inspect their labels:

```bash
for zone in /sys/class/thermal/thermal_zone*; do echo "$zone: $(cat "$zone/type")"; done
```

- If no CPU-related thermal zone is exposed by your kernel/device, temperature remains unavailable and the dashboard displays `N/A`.

### Raspberry Pi SoC/GPU temperature not shown

The optional `SoC Temp` line requires the Raspberry Pi firmware command `vcgencmd`. Install the package that provides it for your Raspberry Pi OS release, then verify it works:

```bash
vcgencmd measure_temp
```

Expected output looks similar to `temp=52.1'C`. If `vcgencmd` is missing or returns an error, the dashboard continues without the SoC/GPU temperature line.

### No Wi-Fi data shown

- Ensure active interface is wireless.
- Verify `iw` is installed:

```bash
iw dev
```

### No connection/interface detected

- Verify `ip` is installed and routing exists:

```bash
ip route get 1.1.1.1
```

### Ping shows errors

- Check general connectivity and ICMP availability.
- Some environments block ICMP echo requests.

### Fan speed always `N/A`

- Your fan controller may expose a different hwmon path or label.
- Some Raspberry Pi fans do not report RPM at all; if they expose `/sys/class/thermal/cooling_device*/cur_state`, the dashboard shows that state instead.

---

## Customization Ideas

You can easily adapt the script for your setup:

- Change refresh rate (`time.sleep(1)`).
- Adjust thermal/load color thresholds.
- Switch ping target from `1.1.1.1` to another host.
- Add per-core CPU stats from `/proc/stat`.
- Track additional sensors via hwmon.
- Add CSV/JSON logging if historical trend analysis is needed.

---

## License

Add your preferred license file (for example, MIT) if this project is intended for distribution.
