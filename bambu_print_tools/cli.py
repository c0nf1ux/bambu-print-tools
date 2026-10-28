"""Single CLI surface over everything in this package. One process per
invocation, nothing persistent — see README.md for why that's the point.

    python -m bambu_print_tools send <config.json> <file.3mf>
    python -m bambu_print_tools list <config.json>
    python -m bambu_print_tools monitor <config.json> [--seconds N]
    python -m bambu_print_tools start-print <config.json> <filename.gcode.3mf>
    python -m bambu_print_tools check-license <url>
"""
import argparse
import json
import sys
import time
from pathlib import Path

from . import ftp_client, license_check, mqtt_client
from .config import load_config


def _cmd_send(args):
    config = load_config(args.config)
    path = Path(args.file)
    if not path.exists():
        sys.exit(f"File not found: {path}")
    print(f"Sending {path.name} ({path.stat().st_size} bytes) to {config['ip']}...")
    ftp_client.send(config, path)
    print("Upload verified. Select it from Print Files on the printer's touchscreen,")
    print("or run start-print to begin it over MQTT.")


def _cmd_list(args):
    config = load_config(args.config)
    ftp = ftp_client.connect(config)
    for line in ftp_client.list_files(ftp):
        print(line)
    ftp.quit()


def _cmd_monitor(args):
    config = load_config(args.config)
    monitor = mqtt_client.PrintMonitor()

    def on_report(report):
        state = monitor.update(report)
        print(json.dumps(state))
        if monitor.is_real_terminal():
            client.disconnect()

    client = mqtt_client.connect(config, on_report)
    client.loop_start()
    deadline = time.monotonic() + args.seconds if args.seconds else None
    try:
        while client.is_connected() and (deadline is None or time.monotonic() < deadline):
            time.sleep(0.5)
    finally:
        client.loop_stop()


def _cmd_start_print(args):
    config = load_config(args.config)
    monitor = mqtt_client.PrintMonitor()
    started = {"done": False}

    def on_report(report):
        monitor.update(report)
        if not started["done"]:
            mqtt_client.start_print(client, config, args.filename, use_ams=args.use_ams)
            started["done"] = True
            print(f"Sent project_file command for {args.filename}. Watch with 'monitor'.")
            client.disconnect()

    client = mqtt_client.connect(config, on_report)
    client.loop_start()
    time.sleep(2)  # give the initial pushall response time to arrive
    client.loop_stop()
    if not started["done"]:
        sys.exit("Never got a report back from the printer — check it's on and reachable.")


def _cmd_check_license(args):
    result = license_check.check_license(args.url, thingiverse_token=args.token)
    print(f"{result.platform}: {result.status}" + (f" ({result.license})" if result.license else ""))
    if result.note:
        print(f"  {result.note}")
    sys.exit(0 if result.status == "ok" else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("send", help="Upload a file to the printer over FTPS")
    p.add_argument("config")
    p.add_argument("file")
    p.set_defaults(func=_cmd_send)

    p = sub.add_parser("list", help="List files on the printer")
    p.add_argument("config")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("monitor", help="Watch live print state over MQTT")
    p.add_argument("config")
    p.add_argument("--seconds", type=float, default=None, help="Stop after N seconds (default: until a real terminal state)")
    p.set_defaults(func=_cmd_monitor)

    p = sub.add_parser("start-print", help="Start a print already uploaded to the printer (Stage 2)")
    p.add_argument("config")
    p.add_argument("filename", help="Exact filename as it appears on the printer, e.g. model.gcode.3mf")
    p.add_argument("--use-ams", action="store_true")
    p.set_defaults(func=_cmd_start_print)

    p = sub.add_parser("check-license", help="Check a sourced model's real license")
    p.add_argument("url")
    p.add_argument("--token", default=None, help="Thingiverse App Token (or set THINGIVERSE_TOKEN)")
    p.set_defaults(func=_cmd_check_license)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
