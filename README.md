# Cross-Platform CPU Monitor

A lightweight, terminal-based system monitor for Raspberry Pi, Linux, macOS, and Windows systems.

This script shows real-time board identification, CPU temperature, Raspberry Pi SoC temperature, CPU utilization/frequency, Pi throttling or undervoltage health, fan speed/state, memory/storage usage, network throughput, connection details, Wi-Fi network metrics, and optional ping latency in a compact dashboard.

---

## Features

- **Live terminal dashboard** with 1-second refresh intervals.
- **Board identification** from Raspberry Pi / Linux device tree metadata, macOS hardware model data, or Windows platform metadata.
- **CPU temperature auto-detection** from CPU-like Linux thermal zones, with `N/A` fallback on platforms that do not expose temperature through standard library or shell interfaces.
- **Raspberry Pi SoC/GPU temperature** via `vcgencmd measure_temp` when available.
- **CPU usage** with colorized load thresholds.
- **CPU frequency** from Linux sysfs, Raspberry Pi `vcgencmd`, macOS `sysctl`, or Windows WMIC when available.
- **Raspberry Pi health** from `vcgencmd get_throttled`, including undervoltage, throttling, frequency capping, and soft temperature-limit flags.
- **Fan RPM/state** detection from common hwmon paths and fan-like thermal cooling devices.
- **Memory and storage** usage with human-readable units across Linux, macOS, and Windows, showing each mounted storage device except swap and firmware mounts in a table with used/free space and storage read/write throughput where available.
- **Network throughput** shown as bits, kilobits, and megabits per second for TX/RX using Linux `/proc`, macOS `netstat`, or Windows `netstat` counters.
- **Connection detection** (Wi-Fi vs Ethernet/Other vs Disconnected).
- **Wi-Fi details** when connected wirelessly:
  - connected network name (SSID)
  - signal level (dBm + derived quality %)
  - channel + channel width
  - inferred Wi-Fi standard (rough heuristic)
- **Configurable periodic latency checks** with selectable ping target/count or disabled ping.
- **Compact display mode** for small terminals, OLED/LCD projects, and emoji-free output.
- **Optional alert hook** for high temperature or Raspberry Pi health warnings.
- **Hostname display** and terminal-resize handling for cleaner redraws.
- **Logging support** via `cpu_monitor.log` (timestamped log format configured).

---

## Requirements

### Hardware / OS

- Raspberry Pi, Linux, macOS, or Windows system. Raspberry Pi/Linux exposes the most hardware-specific metrics; macOS and Windows use best-effort OS commands for portable metrics.
- On Linux/Raspberry Pi, a kernel exposing common files like:
  - `/proc/stat`
  - `/proc/meminfo`
  - `/proc/net/dev`
  - `/sys/class/thermal/thermal_zone*/type` and sibling `temp` files
  - `/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq` (optional CPU frequency source)
- On macOS, built-in commands such as `sysctl`, `vm_stat`, `netstat`, and `route`.
- On Windows, PowerShell and built-in commands such as `netstat`; WMIC is used for CPU frequency when present.

### Software

- **Python 3** (no third-party Python packages required).
- System commands used by the script:
  - `ping` (optional; disabled with `--no-ping`)
  - `ip` (from `iproute2`)
  - `iw` (for Wi-Fi details)
  - `lsblk` (for per-mount storage details)
  - `vcgencmd` (optional Raspberry Pi firmware command used for SoC temperature, CPU clock fallback, and throttling/undervoltage health)

> If optional commands are missing, the script still runs and affected metrics display as `N/A`, `Disabled`, or best-effort fallbacks.

---

## Installation

Clone or copy the project onto your Raspberry Pi, Linux, macOS, or Windows host:

```bash
git clone <your-repo-url>
cd cpu_monitor
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

### Command-line options

```bash
python3 cpu_monitor.py --help
```

Useful examples:

```bash
# Ping a local gateway instead of the default public resolver.
python3 cpu_monitor.py --ping-target 192.168.1.1

# Use five pings for each latency sample.
python3 cpu_monitor.py --ping-count 5

# Sample ping latency every 2 to 5 minutes instead of the default 60 to 600 seconds.
python3 cpu_monitor.py --ping-interval-min 120 --ping-interval-max 300

# Disable ICMP checks on isolated networks or locked-down environments.
python3 cpu_monitor.py --no-ping

# Use shorter, emoji-free output for a small display or narrow SSH session.
python3 cpu_monitor.py --compact

# Run a hook once when temperature or Pi health enters an alert state.
python3 cpu_monitor.py --temp-alert-c 70 --alert-command /home/pi/bin/cpu-alert.sh
```

When `--alert-command` is used, the command receives `CPU_MONITOR_ALERT_REASON` in its environment. The hook runs once when entering alert state and can run again only after the alert clears and reappears.

---

## Dashboard Fields

- `Hostname`: system hostname.
- `Board`: board/model metadata reported by Linux device tree, macOS `sysctl`, Windows platform data, or `N/A`.
- `CPU Temp`: CPU die temperature in °C / °F. If Linux sysfs CPU temperature is unavailable, the script falls back to Raspberry Pi `vcgencmd measure_temp` when available; macOS and Windows commonly show `N/A` without vendor-specific sensor tools.
- `SoC Temp`: Raspberry Pi SoC/GPU temperature from `vcgencmd measure_temp`, shown separately when both CPU and SoC temperatures are available.
- `Fan Speed`: first detected fan RPM, fan cooling state, or `N/A`.
- `Pi Health`: Raspberry Pi throttling/undervoltage status from `vcgencmd get_throttled`, `OK` when no common flags are set, or `N/A` when unavailable.
- `CPU Usage`: aggregate CPU utilization percentage.
- `CPU Freq`: current CPU frequency in MHz, read from sysfs, `vcgencmd`, macOS `sysctl`, or Windows WMIC; displays `N/A` if unavailable.
- `Memory`: used / total RAM and percentage.
- `Storage`: table of each mounted storage device with volume name, mount location, used space, free space, percentage free, aggregate write speed, and aggregate read speed, excluding swap and firmware mounts.
- `Network`: transmit (`↑`) and receive (`↓`) rates in `b/s`, `Kb/s`, or `Mb/s`.
- `Connection`: active outbound interface and type.
- `Wi-Fi Network`: connected wireless network name / SSID (Wi-Fi only).
- `Wi-Fi Signal`: dBm and derived quality % (Wi-Fi only).
- `Wi-Fi Channel`: channel with optional channel width (Wi-Fi only).
- `Ping`: average round-trip time to the configured target, refreshed at random intervals, or `Disabled`.

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

Because hardware and operating-system interfaces vary by board, kernel, distro, and platform, some metrics are best-effort:

- **CPU temperature**: scans `/sys/class/thermal/thermal_zone*/type` for CPU-like thermal zones and falls back to the first thermal zone if no CPU-like label is found.
- **CPU frequency**: prefers `/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq` in kHz, then falls back to `vcgencmd measure_clock arm` in Hz.
- **Fan speed**: checks common `fan1_input` paths under hwmon, then fan-like `/sys/class/thermal/cooling_device*` state files.
- **Raspberry Pi SoC/GPU temperature**: optionally runs `vcgencmd measure_temp` and parses output like `temp=52.1'C`.
- **Pi Health**: requires the optional Raspberry Pi `vcgencmd` command; without it, this field displays `N/A`.
- **Wi-Fi details**: depends on interface support and `iw` output format.
- **Ping**: requires network reachability and permission to run `ping`; the script uses Unix/macOS `ping -c` and Windows `ping -n` automatically. Use `--ping-target` to choose the host, `--ping-count` to choose echo requests per sample, `--ping-interval-min`/`--ping-interval-max` to choose the randomized seconds between samples (default 60 to 600), or `--no-ping` to disable.
- **Storage throughput**: Linux reads aggregate block-device counters from `/proc/diskstats`; macOS uses best-effort `iostat`; Windows currently displays `0.00 B/s` when no portable counter source is available.
- **macOS/Windows**: CPU temperature, fan, Raspberry Pi health, and detailed Wi-Fi metrics may display `N/A` because they typically require platform-specific sensor APIs, vendor tools, or elevated permissions not provided by the Python standard library.

If a metric cannot be collected, the dashboard displays `N/A` rather than failing.

---

## Run at Boot on Raspberry Pi

The live dashboard is most useful in an SSH session, but you can run it under systemd for persistent logs or when paired with a terminal/display service.

Create `/etc/systemd/system/cpu-monitor.service`:

```ini
[Unit]
Description=Raspberry Pi CPU Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/Rpi_cpu_monitor
ExecStart=/usr/bin/python3 /home/pi/Rpi_cpu_monitor/cpu_monitor.py --no-ping --compact
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable cpu-monitor.service
sudo systemctl start cpu-monitor.service
journalctl -u cpu-monitor.service -f
```

For a terminal dashboard on a directly attached display, run the script from a user session, kiosk terminal, tmux session, or display-specific service instead of a plain background service.

---

## Troubleshooting

### `CPU Temp` shows `N/A`

The monitor auto-detects CPU-related thermal zones by checking `/sys/class/thermal/thermal_zone*/type` for names such as `cpu-thermal`, `soc-thermal`, and `x86_pkg_temp`, then reading the sibling `temp` file.

Inspect thermal zones:

```bash
for zone in /sys/class/thermal/thermal_zone*; do echo "$zone: $(cat "$zone/type")"; done
```

If no readable thermal zone is exposed by your kernel/device, temperature remains unavailable and the dashboard displays `N/A`.

### Raspberry Pi SoC/GPU temperature or Pi Health is unavailable

The optional SoC temperature and Pi health lines require the Raspberry Pi firmware command `vcgencmd`.

```bash
vcgencmd measure_temp
vcgencmd get_throttled
vcgencmd measure_clock arm
```

If `vcgencmd` is missing or returns an error, the dashboard continues with available sysfs data.

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
- Use `--ping-target` for a local target, tune frequency with `--ping-interval-min` and `--ping-interval-max`, or use `--no-ping` to disable ping checks.

### Fan speed always `N/A`

- Your fan controller may expose a different hwmon path or label.
- Some Raspberry Pi fans do not report RPM at all; if they expose `/sys/class/thermal/cooling_device*/cur_state`, the dashboard shows that state instead.

---

## Customization Ideas

You can adapt the script for your setup:

- Change refresh rate (`time.sleep(1)`).
- Adjust thermal/load color thresholds.
- Switch ping target with `--ping-target`.
- Adjust randomized ping frequency with `--ping-interval-min` and `--ping-interval-max`.
- Disable ping with `--no-ping`.
- Use compact mode with `--compact` for small displays.
- Add custom GPIO, LED, buzzer, notification, or shutdown behavior with `--alert-command`.
- Add per-core CPU stats from `/proc/stat`.
- Track additional sensors via hwmon.
- Add CSV/JSON logging if historical trend analysis is needed.

---

## License

Add your preferred license file (for example, MIT) if this project is intended for distribution.
