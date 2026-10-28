"""Shared printer-config loading. Every tool in this package reads the same
JSON shape, so one gitignored file per project (or a path you pass
explicitly) covers FTP, MQTT, and the CLI.

Expected shape (see README.md for how to get each value):
{
  "ip": "192.168.0.54",
  "access_code": "XXXXXXXX",
  "ftp_port": 990,
  "mqtt_port": 8883,
  "serial": "XXXXXXXXXXXXXXX"
}
"""
import json
import sys
from pathlib import Path


def load_config(path) -> dict:
    path = Path(path)
    if not path.exists():
        sys.exit(
            f"Missing {path}. Create it with your printer's IP, Access Code, "
            "and serial (Settings -> Network on the printer screen; Developer "
            "Mode must be on in Settings -> General to see the Access Code)."
        )
    return json.loads(path.read_text())
