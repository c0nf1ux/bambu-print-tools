from bambu_print_tools.mqtt_client import (
    PrintMonitor,
    build_pushall_request,
    build_start_print_payload,
    extract_state,
)


def test_extract_state_pulls_only_confirmed_fields():
    raw = {
        "gcode_state": "RUNNING",
        "mc_percent": 42,
        "some_undocumented_field": "ignored",
    }
    state = extract_state(raw)
    assert state == {"gcode_state": "RUNNING", "mc_percent": 42}


def test_build_pushall_request_shape():
    assert build_pushall_request() == {"pushing": {"sequence_id": "0", "command": "pushall"}}


def test_build_start_print_payload_defaults():
    payload = build_start_print_payload("model.gcode.3mf")
    assert payload == {
        "print": {
            "sequence_id": "0",
            "command": "project_file",
            "file": "model.gcode.3mf",
            "param": "Metadata/plate_1.gcode",
            "url": "ftp://model.gcode.3mf",
            "use_ams": False,
        }
    }


def test_monitor_does_not_trust_stale_failed_on_first_connect():
    """gcode_state on first connect is the printer's LAST STORED state, not
    necessarily live — a stale FAILED from a prior, already-ended job must
    not be reported as this session's real terminal state."""
    monitor = PrintMonitor()
    monitor.update({"gcode_state": "FAILED"})
    assert monitor.is_real_terminal() is False


def test_monitor_trusts_running_immediately():
    """Unlike the terminal states, RUNNING on first connect IS safe to
    trust right away — the printer won't report it unless something is
    actively printing right now."""
    monitor = PrintMonitor()
    monitor.update({"gcode_state": "RUNNING"})
    assert monitor.has_seen_running is True


def test_monitor_trusts_terminal_state_after_seeing_running():
    monitor = PrintMonitor()
    monitor.update({"gcode_state": "RUNNING"})
    monitor.update({"gcode_state": "FINISH"})
    assert monitor.is_real_terminal() is True


def test_monitor_ignores_terminal_state_before_running_ever_seen():
    monitor = PrintMonitor()
    monitor.update({"gcode_state": "PREPARE"})
    monitor.update({"gcode_state": "FAILED"})
    assert monitor.is_real_terminal() is False
