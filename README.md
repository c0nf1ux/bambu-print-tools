# bambu-print-tools

A small CLI toolkit for Bambu Lab printer automation and OpenSCAD/STL
sanity checks — built specifically to **not** be an MCP server.

## Why no MCP

Every command here is a short-lived process: connect, do one thing, exit.
There's no daemon sitting in RAM between calls and no persistent token
overhead for a tool-use loop to carry around. If you're running Claude
Code (or anything else) on modest hardware, this gets you the same
capability an MCP integration like `bambu-mcp` would — an agent that can
check print status or start a print as part of a normal shell command —
without the standing cost. Point a CLI-executing agent at this package the
same way you'd point it at `git` or `curl`.

If you want a persistent dashboard/UI instead of CLI calls, look at
[Bambuddy](https://printer-hub.ru/en/posts/bambuddy-self-hosted-bambu-lab)
— it's self-hosted (not MCP either), but it is a standing service, which
is a different tradeoff than everything in this repo.

## What's here

- **`ftp_client`** — upload a sliced file to the printer over implicit
  FTPS, the same mechanism the official slicer's "Send" button uses.
- **`mqtt_client`** — subscribe to live print status (temps, progress,
  fault codes) and start a print that's already on the printer's
  storage, over MQTTS.
- **`slicer`** — resolve an OrcaSlicer preset's full `inherits` chain by
  hand. The OrcaSlicer CLI does **not** reliably do this itself for
  `--load-settings`/`--load-filaments` — it silently falls back to bare
  program defaults for any key the top-level preset file doesn't state
  outright. This caused a real failed print (a 135mm PLA part sliced at
  35°C/no-brim instead of the inherited 220°C/textured-PEI settings,
  peeled off the plate and spaghetti'd mid-print).
- **`mesh_check`** — bed-fit, watertightness, true-fusion (a CSG
  `union()` can produce two touching-but-disjoint watertight shells that
  both individually pass a watertight check), and point-containment
  checks (catches a tunnel cut straight through a wall, which a
  watertight check alone cannot see).
- **`license_check`** — check the real redistribution license on a
  sourced model before printing/reusing it. "Free to download" and
  "licensed for redistribution" are different questions on every one of
  these platforms.

None of this replaces actually looking at a render. Pair it with:

```bash
openscad -o preview.png --imgsize=1000,700 --render model.scad
```

and read the PNG — numeric checks alone have missed real bugs (overlapping
geometry, a cavity with no real depth) that were obvious on sight.

## Setup

```bash
pip install -e .
```

Printer config (per project, gitignored — never commit real credentials):

```json
{
  "ip": "192.168.0.54",
  "access_code": "XXXXXXXX",
  "ftp_port": 990,
  "mqtt_port": 8883,
  "serial": "XXXXXXXXXXXXXXX"
}
```

Get the IP and Access Code from the printer's **Settings → Network**
screen (**Developer Mode** must be on in **Settings → General** first —
this runs alongside cloud/Handy, it doesn't disable them). The serial is
on the same screen or the printer's physical label.

## Usage

```bash
python -m bambu_print_tools send config.json print-ready/model.gcode.3mf
python -m bambu_print_tools list config.json
python -m bambu_print_tools monitor config.json --seconds 60
python -m bambu_print_tools start-print config.json model.gcode.3mf
python -m bambu_print_tools check-license "https://www.myminifactory.com/object/..."
```

### `start-print` is not yet verified against real hardware

The `project_file` MQTT command it sends is the minimal shape captured
from OrcaSlicer's own debug log — that capture was truncated, so there may
be additional fields a real printer expects that aren't here yet. Try it
against your own printer with a cheap/disposable test file first, and if
it's rejected or ignored, re-capture the full command from a slicer's
debug log while it starts a real print, and extend
`mqtt_client.build_start_print_payload` accordingly.

### License checking: platform support changes over time

Confirmed by direct testing on **2026-09-07**: MakerWorld and
MyMiniFactory served real license data to a plain browser-UA request, no
Cloudflare challenge. Re-tested on **2026-10-28** while building this
package: **MakerWorld now returns the same Cloudflare "Just a moment" JS
challenge as Printables/Cults3D**, confirmed with both `curl` and this
package's own `requests`-based client — this is a real site-side change,
not a bug in this code. MyMiniFactory still returned a normal 200 as of
the same date. Treat the per-platform support list in `license_check.py`'s
docstring as a point-in-time claim, not a guarantee — if a platform that
used to work starts returning "blocked," that's most likely them, not you.

## Development

```bash
pip install -e ".[dev]"  # or just: pip install pytest
pytest tests/
```

Tests use self-authored fixtures (synthetic OrcaSlicer preset JSON with a
real `inherits` chain, generated trimesh primitives) — no mocked network
calls and no third-party model files. The FTP/MQTT device I/O itself isn't
unit-tested (there's no fake printer to test it against that would mean
anything); it's verified by hand against a real printer when one's
available.

## License

MIT.
