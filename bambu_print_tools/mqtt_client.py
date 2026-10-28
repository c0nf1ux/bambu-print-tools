"""Live printer status over MQTTS, plus starting a print that's already on
the printer's storage (Stage 2 of the painting-station automation pipeline,
docs/AUTOMATION.md). Same no-MCP philosophy as ftp_client.py: short-lived
subscribe-and-watch calls driven directly from the CLI, nothing persistent.

Protocol details confirmed against a real Bambu A1 mini (2026-09-15):
same bblp/Access Code auth as FTP, self-signed cert (see the same narrow,
documented exception in ftp_client.py), port 8883.
"""
from __future__ import annotations

import json
import ssl
from dataclasses import dataclass, field
from typing import Callable

import paho.mqtt.client as mqtt

REPORT_TOPIC = "device/{serial}/report"
REQUEST_TOPIC = "device/{serial}/request"
TERMINAL_STATES = {"FAILED", "FINISH"}

# Fields confirmed present in a real report payload (2026-09-15 session).
_STATE_FIELDS = (
    "gcode_state",
    "mc_percent",
    "layer_num",
    "total_layer_num",
    "nozzle_temper",
    "bed_temper",
    "hms",
    "subtask_name",
    "mc_remaining_time",
    "ipcam",
)


def extract_state(report: dict) -> dict:
    """`report` is the `print` sub-object of a report message
    (`{"print": {...}}`), not the whole MQTT payload."""
    return {k: report.get(k) for k in _STATE_FIELDS if k in report}


@dataclass
class PrintMonitor:
    """Tracks whether a real RUNNING state has actually been observed this
    session before trusting a terminal state.

    Why: `gcode_state` on first connect is the printer's LAST STORED state,
    not necessarily anything live - it can be FAILED left over from a
    previous, already-cancelled print. A naive "stop watching as soon as I
    see FAILED/FINISH" check fires immediately on connect and never
    actually watches anything. Exception: if RUNNING itself is the very
    first state observed, that IS safe to trust immediately - the printer
    won't report RUNNING unless something is actively printing right now,
    unlike the terminal states which persist from the last job.
    """

    has_seen_running: bool = False
    state: dict = field(default_factory=dict)

    def update(self, report: dict) -> dict:
        self.state = extract_state(report)
        if self.state.get("gcode_state") == "RUNNING":
            self.has_seen_running = True
        return self.state

    def is_real_terminal(self) -> bool:
        return self.has_seen_running and self.state.get("gcode_state") in TERMINAL_STATES


def build_pushall_request() -> dict:
    """Forces the printer to dump its full current state on report."""
    return {"pushing": {"sequence_id": "0", "command": "pushall"}}


def build_start_print_payload(
    filename: str,
    plate_metadata_path: str = "Metadata/plate_1.gcode",
    use_ams: bool = False,
    sequence_id: str = "0",
) -> dict:
    """Minimal confirmed `project_file` command to start a print already
    uploaded to the printer's storage (captured from OrcaSlicer's own debug
    log, 2026-09-10, docs/AUTOMATION.md in a1-mini-painting-station).

    NOT independently verified against a real printer yet - the captured
    log line was truncated ("...") and may omit fields OrcaSlicer also
    sends (e.g. bed_leveling/flow_cali/timelapse flags, a project/task id).
    If the printer silently ignores or rejects this, re-capture the full,
    untruncated command from a slicer's debug log before adding guessed
    fields.
    """
    return {
        "print": {
            "sequence_id": sequence_id,
            "command": "project_file",
            "file": filename,
            "param": plate_metadata_path,
            "url": f"ftp://{filename}",
            "use_ams": use_ams,
        }
    }


def connect(config: dict, on_report: Callable[[dict], None]) -> mqtt.Client:
    """Connects and subscribes; `on_report` is called with the raw `print`
    sub-object of every report message. Caller owns the client's lifecycle
    (call .loop_start()/.loop_forever() and .disconnect() themselves) -
    this stays a thin wrapper, not a managed background service."""
    serial = config["serial"]

    def _on_connect(client, userdata, flags, rc, properties=None):
        client.subscribe(REPORT_TOPIC.format(serial=serial))
        client.publish(
            REQUEST_TOPIC.format(serial=serial),
            json.dumps(build_pushall_request()),
        )

    def _on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        report = payload.get("print")
        if report is not None:
            on_report(report)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set("bblp", config["access_code"])
    # Same narrow, documented cert exception as ftp_client.py: self-signed
    # printer cert, Access Code is the real auth boundary on this LAN device.
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect(config["ip"], config.get("mqtt_port", 8883), keepalive=30)
    return client


def start_print(client: mqtt.Client, config: dict, filename: str, **kwargs) -> None:
    client.publish(
        REQUEST_TOPIC.format(serial=config["serial"]),
        json.dumps(build_start_print_payload(filename, **kwargs)),
    )
