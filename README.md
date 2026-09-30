# Cross-Platform CPU Monitor

A lightweight, terminal-based system monitor for Raspberry Pi, Linux, macOS, and Windows systems.

This script shows real-time board identification, CPU temperature, Raspberry Pi SoC temperature, CPU utilization/frequency, GPU utilization, Neural Engine / NPU utilization, Pi throttling or undervoltage health, fan speed/state, memory/storage usage, network throughput, connection details, Wi-Fi network metrics, and optional ping latency in a compact dashboard.

---

## Features

- **Live terminal dashboard** with 1-second refresh intervals.
- **Board identification** from Raspberry Pi / Linux device tree metadata, macOS hardware model data, or Windows platform metadata.
- **CPU temperature auto-detection** from CPU-like Linux thermal zones, with `N/A` fallback on platforms that do not expose temperature through standard library or shell interfaces.
- **Raspberry Pi SoC/GPU temperature** via `vcgencmd measure_temp` when available.
- **CPU usage** with colorized load thresholds.
- **CPU frequency** from Linux sysfs, Raspberry Pi `vcgencmd`, macOS `sysctl`, or Windows WMIC when available.
- **GPU usage** with colorized load thresholds, read from `nvidia-smi` / AMD `gpu_busy_percent` (Linux), `ioreg` (macOS, no `sudo`), or `nvidia-smi` / the `\GPU Engine` performance counter (Windows); displays `N/A` when no source is available.
- **Neural Engine / NPU usage** (best-effort, colorized); displays `N/A` on platforms without an unprivileged utilization counter (e.g. the Apple Silicon Neural Engine requires root `powermetrics`). Disable both accelerator metrics with `--no-accel`.
- **Raspberry Pi health** from `vcgencmd get_throttled`, including undervoltage, throttling, frequency capping, and soft temperature-limit flags.
- **Fan RPM/state** detection from common hwmon paths and fan-like thermal cooling devices.
- **Memory and storage** usage with human-readable units across Linux, macOS, and Windows, showing each mounted storage device except swap and firmware mounts in a table with used/free space and storage read/write throughput where available.
- **Network throughput** shown as bits, kilobits, and megabits per second for TX/RX using Linux `/proc`, macOS `netstat`, or Windows `netstat` counters.
- **Connection detection** (Wi-Fi vs Ethernet/Other vs Disconnected) with active-interface IP address display.
- **Wi-Fi details** when connected wirelessly:
  - connected network name (SSID)
  - signal level (dBm + derived quality %)
  - channel + channel width
  - inferred Wi-Fi standard (rough heuristic)
- **Configurable periodic latency checks** with selectable ping target/count, active-adapter idle waiting, or disabled ping.
- **Compact display mode** for small terminals, OLED/LCD projects, and emoji-free output.
- **Optional alert hook** for high temperature or Raspberry Pi health warnings.
- **Hostname display** and content-aware terminal resizing for cleaner redraws without fixed dashboard dimensions.
- **Logging support** via `cpu_monitor.log` (timestamped log format configured).
- **Metrics logging for graphing**: every sampled metric is also written to a JSON Lines file (default `cpu_monitor_metrics.jsonl`) that plots directly with pandas/jq — see [Metrics Log for Graphing](#metrics-log-for-graphing).

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
- On macOS, built-in commands such as `sysctl`, `vm_stat`, `netstat`, `route`, and `ioreg`.
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

# Only ping after the active adapter drops below 50 Kb/s, waiting up to 20 seconds.
python3 cpu_monitor.py --ping-idle-threshold-kbps 50 --ping-idle-timeout 20

# Disable ICMP checks on isolated networks or locked-down environments.
python3 cpu_monitor.py --no-ping

# Use shorter, emoji-free output for a small display or narrow SSH session.
python3 cpu_monitor.py --compact

# Disable the GPU and Neural Engine (NPU) utilization metrics (e.g. to avoid the
# extra counter lookups on platforms where they are expensive to read).
python3 cpu_monitor.py --no-accel

# Run a hook once when temperature or Pi health enters an alert state.
python3 cpu_monitor.py --temp-alert-c 70 --alert-command /home/pi/bin/cpu-alert.sh

# Write the sampled metrics to a specific file for graphing.
python3 cpu_monitor.py --metrics-log /var/log/cpu_metrics.jsonl

# Show only the on-screen dashboard, without the per-sample metrics log.
python3 cpu_monitor.py --no-metrics-log
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
- `GPU Usage`: aggregate GPU utilization percentage (0-100), colorized. On Linux it reads the busiest NVIDIA GPU via `nvidia-smi` or the busiest AMD card via `/sys/class/drm/card*/device/gpu_busy_percent`; on macOS it reads the IOAccelerator `Device Utilization %` statistic via `ioreg` (no `sudo`); on Windows it uses `nvidia-smi` or the `\GPU Engine(*)\Utilization Percentage` counter. Displays `N/A` when no source is available (e.g. Raspberry Pi, or an Intel iGPU with no driver counter).
- `NPU Usage`: Neural Engine / NPU utilization percentage (0-100), colorized, best-effort. Displays `N/A` on platforms without an unprivileged utilization counter (the Apple Silicon Neural Engine, and most Linux/Windows systems without a dedicated NPU counter).
- `CPU Freq`: current CPU frequency in MHz, read from sysfs, `vcgencmd`, macOS `sysctl`, or Windows WMIC; displays `N/A` if unavailable.
- `Memory`: used / total RAM and percentage.
- `Storage`: table of each mounted storage device with volume name, mount location, used space, free space, percentage free, per-device write speed, and per-device read speed where available, excluding swap and firmware mounts.
- `Network`: transmit (`↑`) and receive (`↓`) rates in `b/s`, `Kb/s`, or `Mb/s`.
- `Connection`: active outbound interface and type.
- `IP Address`: IPv4/IPv6 address(es) assigned to the active outbound interface, or `N/A` when unavailable.
- `Wi-Fi Network`: connected wireless network name / SSID (Wi-Fi only).
- `Wi-Fi Signal`: dBm and derived quality % (Wi-Fi only).
- `Wi-Fi Channel`: channel with optional channel width (Wi-Fi only).
- `Ping`: average round-trip time to the configured target, refreshed at random intervals, or `Disabled`.

---

## Color Thresholds

### CPU / GPU / NPU usage color

The same thresholds apply to the `CPU Usage`, `GPU Usage`, and `NPU Usage` metrics.

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
- **GPU usage**: on Linux it reads the busiest NVIDIA GPU via `nvidia-smi --query-gpu=utilization.gpu` or the busiest AMD card via `/sys/class/drm/card*/device/gpu_busy_percent`; on macOS it reads the IOAccelerator `Device Utilization %` statistic via `ioreg -c IOAccelerator` (no `sudo`); on Windows it uses `nvidia-smi` or the `\GPU Engine(*)\Utilization Percentage` performance counter (busiest engine). It displays `N/A` when no source is available (e.g. Raspberry Pi, or an Intel iGPU that exposes no utilization counter). GPU/NPU values refresh every 2 seconds to keep the 1-second dashboard responsive on platforms where the counters are slow to read.
- **Neural Engine / NPU usage**: best-effort. Apple does not expose an unprivileged utilization counter for the Apple Silicon Neural Engine, so this field displays `N/A` on Apple Silicon (root `powermetrics` can report ANE *power in watts*, but not a utilization percentage). On Linux/Windows it looks for vendor NPU counters and typically displays `N/A` unless your device exposes one. Use `--no-accel` to disable both accelerator metrics.
- **Fan speed**: checks common `fan1_input` paths under hwmon, then fan-like `/sys/class/thermal/cooling_device*` state files.
- **Raspberry Pi SoC/GPU temperature**: optionally runs `vcgencmd measure_temp` and parses output like `temp=52.1'C`.
- **Pi Health**: requires the optional Raspberry Pi `vcgencmd` command; without it, this field displays `N/A`.
- **IP Address**: depends on `ip` on Linux, `ifconfig` on macOS, or PowerShell network cmdlets on Windows; displays `N/A` when the active interface has no global address or the command is unavailable.
- **Wi-Fi details**: depends on interface support and `iw` output format.
- **Ping**: requires network reachability and permission to run `ping`; the script uses Unix/macOS `ping -c` and Windows `ping -n` automatically. Before each check, it uses non-blocking samples while the dashboard keeps refreshing, waiting for the active outbound adapter to stay under the configured combined TX/RX idle threshold (default 100 Kb/s for up to 30 seconds) so the latency sample is less likely to be skewed by local traffic. Use `--ping-target` to choose the host, `--ping-count` to choose echo requests per sample, `--ping-interval-min`/`--ping-interval-max` to choose the randomized seconds between samples (default 60 to 600), `--ping-idle-threshold-kbps` and `--ping-idle-timeout` to tune idle waiting, or `--no-ping` to disable.
- **Storage throughput**: Linux reads per-block-device counters from `/proc/diskstats` and matches mounted volumes to their parent devices; macOS uses best-effort aggregate `iostat`; Windows currently displays `0.00 B/s` when no portable counter source is available.
- **macOS/Windows**: CPU temperature, fan, Raspberry Pi health, and detailed Wi-Fi metrics may display `N/A` because they typically require platform-specific sensor APIs, vendor tools, or elevated permissions not provided by the Python standard library.

If a metric cannot be collected, the dashboard displays `N/A` rather than failing.

---

## Metrics Log for Graphing

Every sampled dashboard value is also written to a machine-readable **JSON Lines** log — one JSON object per line, one object per second — so you can build historical graphs without scraping the terminal. By default it is appended to `cpu_monitor_metrics.jsonl` next to the script.

Each record contains every metric the dashboard shows. Unavailable metrics are written as `null`, so plotting tools treat them as gaps rather than zeros.

| Field group | Fields |
|---|---|
| Time | `ts` (POSIX seconds), `time` (ISO-8601, local timezone) |
| Identity | `hostname`, `board_model` |
| CPU | `cpu_temp_c`, `soc_temp_c`, `cpu_usage_pct`, `cpu_freq_mhz` |
| Accelerators | `gpu_usage_pct`, `npu_usage_pct` |
| Cooling / health | `fan_rpm`, `fan_status`, `pi_health` |
| Memory | `mem_total_bytes`, `mem_used_bytes`, `mem_pct` |
| Storage I/O | `storage_read_bytes_per_s`, `storage_write_bytes_per_s`, `storage_devices` (per device) |
| Network | `net_rx_bytes_per_s`, `net_tx_bytes_per_s`, `net_rx_bits_per_s`, `net_tx_bits_per_s`, `net_interface`, `connection_type`, `net_ip_addresses` |
| Wi-Fi | `wifi_ssid`, `wifi_signal_dbm`, `wifi_signal_quality_pct`, `wifi_channel`, `wifi_channel_width_mhz`, `wifi_standard` |
| Latency | `ping_avg_ms`, `ping_error` |

### Controlling the log

```bash
# Default: append to ./cpu_monitor_metrics.jsonl.
python3 cpu_monitor.py

# Write to a specific file.
python3 cpu_monitor.py --metrics-log /var/log/cpu_metrics.jsonl

# Turn the metrics log off (on-screen dashboard only).
python3 cpu_monitor.py --no-metrics-log
```

### Plotting with Python

Because each line is self-contained JSON, you can plot directly with pandas + matplotlib:

```python
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_json("cpu_monitor_metrics.jsonl", lines=True)
df["time"] = pd.to_datetime(df["time"])

fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(12, 8))
ax1.plot(df["time"], df["cpu_usage_pct"], label="CPU %")
ax1.plot(df["time"], df["gpu_usage_pct"], label="GPU %")
ax1.set_ylabel("utilization %"); ax1.legend(); ax1.grid(True)
ax2.plot(df["time"], df["cpu_temp_c"], label="CPU temp", color="tab:red")
ax2.plot(df["time"], df["net_rx_bits_per_s"] / 1e6, label="Rx Mb/s", color="tab:green")
ax2.set_ylabel("value"); ax2.legend(); ax2.grid(True)
fig.autofmt_xdate(); plt.tight_layout(); plt.show()
```

### Quick peek with `jq`

```bash
# Follow the newest samples.
tail -f cpu_monitor_metrics.jsonl | jq '{time, cpu_usage_pct, gpu_usage_pct, cpu_temp_c, net_rx_bits_per_s}'

# Average CPU usage over the whole file.
jq -s 'map(.cpu_usage_pct | select(. != null)) | add / length' cpu_monitor_metrics.jsonl
```

The `cpu_monitor_metrics.jsonl` file is git-ignored by default. Because it is plain text appended one line at a time, you can rotate it with your normal log tooling.

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

### GPU or NPU usage shows `N/A`

- **GPU**: confirm a supported source exists — `nvidia-smi` (NVIDIA), `/sys/class/drm/card*/device/gpu_busy_percent` (AMD), `ioreg` (macOS), or the `\GPU Engine(*)\Utilization Percentage` counter (Windows). Intel iGPUs and Raspberry Pi GPUs typically expose no utilization counter, so `N/A` is expected there.
- **NPU / Neural Engine**: on Apple Silicon the Neural Engine has no unprivileged utilization counter, so `N/A` is expected. Root `powermetrics` reports ANE power (watts) rather than a percentage.

If a metric is not relevant or slow on your platform, hide it with `--no-accel`.

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
- Disable the GPU and NPU utilization metrics with `--no-accel`.
- Add custom GPIO, LED, buzzer, notification, or shutdown behavior with `--alert-command`.
- Add per-core CPU stats from `/proc/stat`.
- Track additional sensors via hwmon.
- The built-in `--metrics-log` JSON Lines log (see [Metrics Log for Graphing](#metrics-log-for-graphing)) already captures every sampled metric for historical trend analysis; point it at a different path or disable it with `--no-metrics-log`.

---

## License

Add your preferred license file (for example, MIT) if this project is intended for distribution.
