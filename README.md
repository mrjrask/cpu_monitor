# Raspberry Pi CPU Monitor

A lightweight, terminal-based system monitor for Raspberry Pi and Linux systems.

This script shows real-time CPU temperature, CPU/GPU utilization, fan speed, memory/storage usage, network throughput, connection details, Wi-Fi metrics, and periodic ping latency in a compact dashboard.

---

## Features

- **Live terminal dashboard** with 1-second refresh intervals.
- **CPU temperature** in °C and °F with colorized thermal thresholds.
- **CPU usage** with colorized load thresholds.
- **GPU usage** (best-effort autodetection across common Linux sysfs locations).
- **Fan RPM** detection from common hwmon paths.
- **Memory and storage** usage with human-readable units.
- **Network throughput** (TX/RX bytes per second).
- **Connection detection** (Wi-Fi vs Ethernet/Other vs Disconnected).
- **Wi-Fi details** when connected wirelessly:
  - signal level (dBm + derived quality %)
  - channel + channel width
  - frequency
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

### Software

- **Python 3** (no third-party Python packages required).
- System commands used by the script:
  - `ping`
  - `ip` (from `iproute2`)
  - `iw` (for Wi-Fi details)

> If `ip` or `iw` are missing, the script still runs, but some network/Wi-Fi details may show as unavailable.

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
- `CPU Temp`: CPU die temperature in °C / °F.
- `Fan Speed`: first detected fan RPM, or `N/A`.
- `CPU Usage`: aggregate CPU utilization percentage.
- `GPU`: utilization percentage if detected, otherwise `N/A`.
- `Memory`: used / total RAM and percentage.
- `Storage`: used / total storage for `/` and percentage.
- `Network`: transmit (`↑`) and receive (`↓`) rates.
- `Connection`: active outbound interface and type.
- `Wi-Fi Signal`: dBm and derived quality % (Wi-Fi only).
- `Wi-Fi Channel/Freq`: channel width and frequency (Wi-Fi only).
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

- **GPU usage**: tries multiple sysfs/debugfs paths and parses values heuristically.
- **Fan speed**: checks common `fan1_input` paths under hwmon.
- **Wi-Fi details**: depends on interface support and `iw` output format.
- **Ping**: requires network reachability and permission to run `ping`.

If a metric cannot be collected, the dashboard displays `N/A` rather than failing.

---

## Troubleshooting

### `CPU Temp` fails or script exits on startup

Your system may not expose `/sys/class/thermal/thermal_zone0/temp`.

- Confirm path exists:

```bash
cat /sys/class/thermal/thermal_zone0/temp
```

- If your board uses a different thermal zone, update `get_cpu_temp()` accordingly.

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

## Running at Boot (systemd example)

Create `/etc/systemd/system/cpu-monitor.service`:

```ini
[Unit]
Description=Raspberry Pi CPU Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/path/to/Rpi_cpu_monitor
ExecStart=/usr/bin/python3 /path/to/Rpi_cpu_monitor/cpu_monitor.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable cpu-monitor.service
sudo systemctl start cpu-monitor.service
```

Check logs:

```bash
sudo systemctl status cpu-monitor.service
journalctl -u cpu-monitor.service -f
```

---

## License

Add your preferred license file (for example, MIT) if this project is intended for distribution.
