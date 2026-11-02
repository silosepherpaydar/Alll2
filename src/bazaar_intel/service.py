"""نصب زمان‌بندی پایدار. ساعت‌ها به وقت تهران هستند."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bazaar_intel.config import REPO_ROOT


def unit_text(session: str) -> str:
    return f"""[Unit]
Description=Bazaar intel {session} cycle
After=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory={REPO_ROOT}
Environment=PYTHONPATH={REPO_ROOT / "src"}
ExecStart=/usr/bin/python3 -m bazaar_intel cycle --session {session}
"""


def timer_text(session: str, calendar: str) -> str:
    return f"""[Unit]
Description=Bazaar intel {session} timer

[Timer]
OnCalendar={calendar}
Timezone=Asia/Tehran
Persistent=true
Unit=bazaar-intel-{session}.service

[Install]
WantedBy=timers.target
"""


def cron_text() -> str:
    root = REPO_ROOT
    preopen = (
        f"20 5 * * 0,1,2,3,6 ubuntu cd {root} && PYTHONPATH=src "
        f"/usr/bin/flock -n {root / 'data' / 'bazaar.lock'} "
        f"/usr/bin/python3 -m bazaar_intel cycle --session preopen >> {root / 'data' / 'cron.log'} 2>&1"
    )
    close = (
        f"30 15 * * * ubuntu cd {root} && PYTHONPATH=src "
        f"/usr/bin/flock -n {root / 'data' / 'bazaar.lock'} "
        f"/usr/bin/python3 -m bazaar_intel cycle --session close >> {root / 'data' / 'cron.log'} 2>&1"
    )
    return (
        "SHELL=/bin/bash\n"
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
        "# 05:20 UTC = 08:50 Asia/Tehran, Saturday to Wednesday\n"
        f"{preopen}\n"
        "# 15:30 UTC = 19:00 Asia/Tehran, every day\n"
        f"{close}\n"
    )


def install_service() -> str:
    system = Path("/etc/systemd/system")
    specs = {
        "bazaar-intel-preopen.service": unit_text("preopen"),
        "bazaar-intel-close.service": unit_text("close"),
        "bazaar-intel-preopen.timer": timer_text("preopen", "Sat,Sun,Mon,Tue,Wed *-*-* 08:50:00"),
        "bazaar-intel-close.timer": timer_text("close", "*-*-* 19:00:00"),
    }
    for name, body in specs.items():
        path = system / name
        _write_root(path, body)
    cron_dir = Path("/etc/cron.d")
    _write_root(cron_dir / "bazaar-intel", cron_text())
    commands = [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", "bazaar-intel-preopen.timer"],
        ["systemctl", "enable", "--now", "bazaar-intel-close.timer"],
        ["systemctl", "enable", "--now", "cron"],
    ]
    lines = []
    for command in commands:
        result = subprocess.run(["sudo", *command], text=True, capture_output=True, check=False)
        lines.append(f"{' '.join(command)} -> {result.returncode} {(result.stdout or result.stderr).strip()}")
    lines.append(service_status())
    return "\n".join(lines)


def service_status() -> str:
    result = subprocess.run(
        ["systemctl", "is-enabled", "bazaar-intel-preopen.timer", "bazaar-intel-close.timer", "cron"],
        text=True,
        capture_output=True,
        check=False,
    )
    detail = (result.stdout or result.stderr).strip().replace("\n", " | ")
    pid1 = Path("/proc/1/comm").read_text(encoding="utf-8").strip()
    cron_on = subprocess.run(["pgrep", "-x", "cron"], capture_output=True, check=False).returncode == 0
    parts = [
        "فایل تایمر ۰۸:۵۰ شنبه تا چهارشنبه و ۱۹:۰۰ هر روز، با Timezone=Asia/Tehran، روی دیسک نوشته شده است.",
        f"systemctl is-enabled: {detail or 'خالی'}.",
    ]
    if pid1 != "systemd":
        parts.append(
            f"PID 1 این محیط {pid1} است، بنابراین تایمر systemd همین حالا اجرا نمی‌شود. "
            "symlink فعال‌سازی هست و روی ماشینی که با systemd بوت شود بالا می‌آید."
        )
    else:
        parts.append("systemd فرآیند اولیه است و تایمرهای فعال‌شده بعد از راه‌اندازی مجدد می‌مانند.")
    if cron_on:
        parts.append(
            "فرآیند cron در حال اجراست. ساعت ۰۵:۲۰ UTC معادل ۰۸:۵۰ تهران در شنبه تا چهارشنبه "
            "و ۱۵:۳۰ UTC معادل ۱۹:۰۰ تهران هر روز است. cron نوبت از دست‌رفته را جبران نمی‌کند."
        )
    else:
        parts.append("فرآیند cron در این لحظه دیده نشد.")
    return " ".join(parts)


def _write_root(path: Path, body: str) -> None:
    subprocess.run(["sudo", "tee", str(path)], input=body, text=True, capture_output=True, check=True)
    subprocess.run(["sudo", "chmod", "644", str(path)], check=True)
