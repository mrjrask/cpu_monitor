#!/usr/bin/env python3
import time
import logging

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
  
def main():
    # Take an initial snapshot of CPU times
    prev_idle, prev_total = read_cpu_times()

    try:
        while True:
            time.sleep(1)

            # Calculate CPU usage
            idle, total = read_cpu_times()
            idle_delta  = idle  - prev_idle
            total_delta = total - prev_total
            prev_idle, prev_total = idle, total
            cpu_usage = (1.0 - idle_delta / total_delta) * 100.0

            # Read and convert temperature
            temp_c = get_cpu_temp()
            temp_f = (temp_c * 9/5) + 32

            # Log to file
            logging.info(f"Temp: {temp_c:.2f}°C/{temp_f:.2f}°F   CPU: {cpu_usage:.1f}%")

            # Determine colors
            cpu_col  = color_for_cpu(cpu_usage)
            temp_col = color_for_temp(temp_c)

            # Print colored, in-place update
            print(
                f"\rCPU Temp: {temp_col}{temp_c:5.2f}°C / {temp_f:5.2f}°F{RESET}   "
                f"CPU Usage: {cpu_col}{cpu_usage:5.1f}%{RESET}",
                end="", flush=True
            )

    except KeyboardInterrupt:
        # Move to next line on exit
        print()

if __name__ == "__main__":
    main()
