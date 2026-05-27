# Disk Monitor Python

A production-style Python disk usage monitoring tool for Linux systems with logging, threshold-based alerting, and optional SMTP email notifications.

## Features

- Monitors mounted filesystems
- Warning and critical disk thresholds
- Colorized terminal output
- Log file support
- SMTP email alerts
- Graceful error handling
- Exit codes for monitoring integration
- RHEL/Linux friendly

## Requirements

- Python 3.8+
- Linux system
- Optional:
  - psutil

Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

Basic usage:

```bash
python3 src/disk_monitor.py
```

Custom thresholds:

```bash
python3 src/disk_monitor.py --warn 75 --crit 85
```

Disable email alerts:

```bash
python3 src/disk_monitor.py --no-email
```

Enable SMTP alerts:

```bash
python3 src/disk_monitor.py \
  --smtp-host mail.company.com \
  --mail-from alerts@company.com \
  --mail-to ops@company.com
```

## Example Output

```text
[2026-05-26 12:00:00] OK        | /dev/sda1 | / | 45% used
[2026-05-26 12:00:00] WARNING   | /dev/sdb1 | /data | 82% used
```

## Exit Codes

| Code | Meaning |
|------|----------|
| 0 | OK |
| 1 | WARNING |
| 2 | CRITICAL |
| 3 | UNKNOWN |

## Cron Example

```cron
0 * * * * /usr/bin/python3 /opt/scripts/disk_monitor.py
```

## Future Improvements

- Slack notifications
- HTML email formatting
- Prometheus metrics export
- JSON output mode
- Multi-host monitoring
