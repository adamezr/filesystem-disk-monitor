#!/usr/bin/env python3
"""
disk_monitor.py — Disk usage monitor for RHEL 8
================================================
Checks all mounted filesystems and emails a plain-text alert
via internal SMTP relay on WARNING or CRITICAL usage.

Usage:
    python3 disk_monitor.py
    python3 disk_monitor.py --warn 75 --crit 85
    python3 disk_monitor.py --smtp-host mail.company.com \\
                            --mail-from alerts@company.com \\
                            --mail-to ops@company.com

Cron (every hour):
    0 * * * * /usr/bin/python3 /opt/scripts/disk_monitor.py \\
              --smtp-host mail.company.com \\
              --mail-from disk-alerts@company.com \\
              --mail-to ops@company.com

Exit codes:
    0 = OK  |  1 = WARNING  |  2 = CRITICAL  |  3 = UNKNOWN
"""

import argparse
import logging
import os
import shutil
import smtplib
import socket
import sys
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from enum import IntEnum
from typing import List, Optional


# =============================================================================
# ENUMS
# =============================================================================
class ExitCode(IntEnum):
    OK       = 0
    WARNING  = 1
    CRITICAL = 2
    UNKNOWN  = 3


class Status(IntEnum):
    OK       = 0
    WARNING  = 1
    CRITICAL = 2


# =============================================================================
# COLORS — auto-disabled when stdout is not a terminal
# =============================================================================
class Colors:
    RESET  = "\033[0m"
    GREEN  = "\033[0;32m"
    YELLOW = "\033[1;33m"
    RED    = "\033[0;31m"
    BOLD   = "\033[1m"

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def colorize(self, text: str, *codes: str) -> str:
        if not self.enabled:
            return text
        return "".join(codes) + text + self.RESET


# =============================================================================
# DATA MODELS
# =============================================================================
@dataclass
class FilesystemResult:
    device:     str
    mountpoint: str
    usage_pct:  int
    total_gb:   float
    used_gb:    float
    free_gb:    float
    status:     Status = Status.OK


@dataclass
class CheckSummary:
    results:        List[FilesystemResult] = field(default_factory=list)
    ok_count:       int = 0
    warning_count:  int = 0
    critical_count: int = 0
    hostname:       str = ""
    timestamp:      str = ""

    @property
    def overall_status(self) -> ExitCode:
        if not self.results:
            return ExitCode.UNKNOWN
        return ExitCode(max(r.status for r in self.results))

    @property
    def alert_results(self) -> List[FilesystemResult]:
        """Filesystems in WARNING or CRITICAL state only."""
        return [r for r in self.results if r.status != Status.OK]


# =============================================================================
# CLASS: DiskMonitor — collects and evaluates filesystem data
# =============================================================================
class DiskMonitor:

    EXCLUDED_FS_TYPES = {"tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs"}

    def __init__(self, warn_threshold: int = 80, crit_threshold: int = 90):
        if not (0 < warn_threshold < crit_threshold <= 100):
            raise ValueError(f"Invalid thresholds: warn={warn_threshold}, crit={crit_threshold}.")
        self.warn_threshold = warn_threshold
        self.crit_threshold = crit_threshold

    def _get_partitions(self) -> list:
        """psutil if available, /proc/mounts as stdlib fallback."""
        try:
            import psutil
            return [p for p in psutil.disk_partitions(all=False)
                    if p.fstype not in self.EXCLUDED_FS_TYPES]
        except ImportError:
            return self._partitions_from_proc()

    def _partitions_from_proc(self) -> list:
        from types import SimpleNamespace
        results = []
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                device, mountpoint, fstype = parts[0], parts[1], parts[2]
                if fstype in self.EXCLUDED_FS_TYPES or not device.startswith("/dev/"):
                    continue
                results.append(SimpleNamespace(device=device, mountpoint=mountpoint))
        return results

    def _evaluate(self, pct: int) -> Status:
        if pct >= self.crit_threshold:
            return Status.CRITICAL
        if pct >= self.warn_threshold:
            return Status.WARNING
        return Status.OK

    def check_filesystem(self, device: str, mountpoint: str) -> Optional[FilesystemResult]:
        try:
            u = shutil.disk_usage(mountpoint)
        except (OSError, PermissionError) as e:
            logging.warning("Skipping %s: %s", mountpoint, e)
            return None

        pct    = int(u.used / u.total * 100) if u.total else 0
        result = FilesystemResult(
            device     = device,
            mountpoint = mountpoint,
            usage_pct  = pct,
            total_gb   = round(u.total / 1024**3, 1),
            used_gb    = round(u.used  / 1024**3, 1),
            free_gb    = round(u.free  / 1024**3, 1),
        )
        result.status = self._evaluate(pct)
        return result

    def run(self) -> CheckSummary:
        summary = CheckSummary(
            hostname  = os.uname().nodename,
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        partitions = self._get_partitions()
        if not partitions:
            raise RuntimeError("No eligible filesystems found.")

        seen = set()
        for p in partitions:
            if p.mountpoint in seen:
                continue
            seen.add(p.mountpoint)
            result = self.check_filesystem(p.device, p.mountpoint)
            if result is None:
                continue
            summary.results.append(result)
            if result.status == Status.CRITICAL:
                summary.critical_count += 1
            elif result.status == Status.WARNING:
                summary.warning_count += 1
            else:
                summary.ok_count += 1

        return summary


# =============================================================================
# CLASS: EmailAlerter — plain-text SMTP alert, one email per run
# =============================================================================
class EmailAlerter:

    def __init__(self, smtp_host: str, smtp_port: int,
                 mail_from: str, mail_to: List[str]):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.mail_from = mail_from
        self.mail_to   = mail_to

    def _build_body(self, summary: CheckSummary) -> str:
        """Builds a simple plain-text message body."""
        sep  = "-" * 72
        rows = "\n".join(
            f"  {r.status.name:<8}  {r.device:<20}  {r.mountpoint:<20}"
            f"  {r.usage_pct:>3}%  {r.used_gb}/{r.total_gb} GB"
            for r in summary.alert_results
        )
        return (
            f"Disk Monitor Alert\n"
            f"Host      : {summary.hostname}\n"
            f"Time      : {summary.timestamp}\n"
            f"Overall   : {summary.overall_status.name}\n"
            f"{sep}\n"
            f"  {'STATUS':<8}  {'DEVICE':<20}  {'MOUNTPOINT':<20}"
            f"  {'USE%':>4}  USED/TOTAL\n"
            f"{sep}\n"
            f"{rows}\n"
            f"{sep}\n"
            f"Summary   : OK={summary.ok_count}  "
            f"WARNING={summary.warning_count}  "
            f"CRITICAL={summary.critical_count}\n"
            f"Thresholds: WARNING>=80%  CRITICAL>=90%\n"
            f"Log       : /var/log/disk_monitor.log\n"
        )

    def send(self, summary: CheckSummary) -> bool:
        """Send alert. Returns True on success. Never raises — logs errors instead."""
        if not summary.alert_results:
            return True

        prefix  = "[CRITICAL]" if summary.critical_count else "[WARNING]"
        subject = (
            f"{prefix} Disk alert on {summary.hostname} — "
            f"{summary.critical_count} critical, {summary.warning_count} warning"
        )

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"]    = self.mail_from
        msg["To"]      = ", ".join(self.mail_to)
        msg.set_content(self._build_body(summary))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as smtp:
                smtp.sendmail(self.mail_from, self.mail_to, msg.as_string())
            logging.info("Email sent → %s", ", ".join(self.mail_to))
            return True
        except (smtplib.SMTPException, OSError, socket.timeout) as e:
            logging.warning("Email failed: %s", e)
            return False


# =============================================================================
# CLASS: DiskReporter — terminal output and log file
# =============================================================================
class DiskReporter:

    LABELS = {Status.OK: "OK      ", Status.WARNING: "WARNING ", Status.CRITICAL: "CRITICAL"}

    def __init__(self, log_file: str, colors: Colors):
        self.colors = colors
        self._setup_logging(log_file)

    def _setup_logging(self, log_file: str):
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s",
                                datefmt="%Y-%m-%d %H:%M:%S")
        fh = logging.FileHandler(log_file)
        sh = logging.StreamHandler(sys.stdout)
        fh.setFormatter(fmt)
        sh.setFormatter(fmt)
        logging.basicConfig(level=logging.INFO, handlers=[fh, sh])

    def report_result(self, r: FilesystemResult):
        color = {Status.OK: Colors.GREEN,
                 Status.WARNING: Colors.YELLOW,
                 Status.CRITICAL: Colors.RED + Colors.BOLD}[r.status]
        msg = (f"{self.LABELS[r.status]} | {r.device:<20} | {r.mountpoint:<20} | "
               f"{r.usage_pct:>3}% used | {r.used_gb}/{r.total_gb} GB | {r.free_gb} GB free")
        print(self.colors.colorize(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", color))
        logging.log({Status.OK: logging.INFO,
                     Status.WARNING: logging.WARNING,
                     Status.CRITICAL: logging.CRITICAL}[r.status], msg)

    def report_summary(self, summary: CheckSummary):
        sep = "─" * 60
        logging.info(sep)
        logging.info("SUMMARY  Host: %s | OK: %d  WARNING: %d  CRITICAL: %d",
                     summary.hostname, summary.ok_count,
                     summary.warning_count, summary.critical_count)
        logging.info("Overall: %s", summary.overall_status.name)
        logging.info(sep)


# =============================================================================
# ARGS + ENTRY POINT
# =============================================================================
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Disk usage monitor for RHEL 8.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--warn",      type=int, default=80,          help="Warning threshold %%")
    p.add_argument("--crit",      type=int, default=90,          help="Critical threshold %%")
    p.add_argument("--log",       default="/var/log/disk_monitor.log", help="Log file path")
    p.add_argument("--no-color",  action="store_true",           help="Disable terminal colors")
    p.add_argument("--smtp-host", default="localhost",           help="SMTP relay host")
    p.add_argument("--smtp-port", type=int, default=25,          help="SMTP relay port")
    p.add_argument("--mail-from", default=f"disk-monitor@{socket.getfqdn()}", help="Sender address")
    p.add_argument("--mail-to",   nargs="+", default=[],         help="Recipient(s)")
    p.add_argument("--no-email",  action="store_true",           help="Disable email alerts")
    return p.parse_args()


def main() -> int:
    args     = parse_args()
    colors   = Colors(enabled=not args.no_color and sys.stdout.isatty())
    reporter = DiskReporter(log_file=args.log, colors=colors)

    try:
        summary = DiskMonitor(args.warn, args.crit).run()
    except (ValueError, RuntimeError) as e:
        logging.error("Fatal: %s", e)
        return ExitCode.UNKNOWN

    logging.info("disk_monitor v1.2.0 — %s", summary.hostname)
    print()

    for result in summary.results:
        reporter.report_result(result)
    reporter.report_summary(summary)

    # Send email only when there are alerts and recipients are configured
    if not args.no_email and args.mail_to:
        EmailAlerter(args.smtp_host, args.smtp_port,
                     args.mail_from, args.mail_to).send(summary)
    elif not args.mail_to and not args.no_email:
        logging.info("Email: no recipients set — use --mail-to to enable")

    return int(summary.overall_status)


if __name__ == "__main__":
    sys.exit(main())
