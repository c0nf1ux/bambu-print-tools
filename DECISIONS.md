# Design decisions

Why this exists, and the real problems it ran into. Written as the
project went, not reconstructed after the fact.

## Why not an MCP server

The obvious way to let an AI agent control a 3D printer is an MCP server:
`bambu-mcp` already exists and does roughly this. I didn't use it, on
purpose.

I run Claude Code on an 8GB machine with a real budget constraint, not a
theoretical one. An MCP server is a standing process: it sits in memory
between calls, and every tool-use turn in an agent session carries its
schema as token overhead whether or not that turn touches the printer.
Neither cost buys me anything here, because the actual job (check status,
start a print, verify a license) is a few seconds of network I/O that a
plain CLI call does just as well. So the architecture here is one
process per command: connect, do the one thing, exit. Same capability an
MCP integration would give an agent, at close to zero standing cost.

This generalizes past printers. Before reaching for an MCP server, I now
check whether the thing it exposes already has a CLI, a REST endpoint, or
a protocol I can script directly, same way this toolkit talks to the
printer's own FTPS and MQTT ports instead of going through anything else.
A heavier integration is the right call when it buys you a real capability
(a persistent dashboard, multi-client coordination). It's the wrong call
when it's just wrapping a call you could make yourself for free.

## A real bug this caught: the OrcaSlicer CLI doesn't resolve its own presets

OrcaSlicer's bundled presets inherit from each other (a PLA filament
preset inherits temperature defaults from a generic parent, a process
preset inherits most of its settings from a base profile). The GUI
resolves that chain correctly. The CLI, passed the same preset names via
`--load-settings`/`--load-filaments`, does not: it only reads the keys a
preset states outright, and silently fills everything else from bare
program defaults, no error, no warning.

I found this because a 135mm PLA print peeled off the plate about 6mm in
and the nozzle dragged it into a tangled mess. The preset I'd passed on
the CLI should have inherited a 220°C nozzle temperature and a textured-
PEI bed profile. Instead the slice had silently used the program default:
200°C-ish nozzle behavior and a 35°C "Cool Plate" setting meant for a
different surface entirely, nowhere near enough adhesion for a part that
size. The sliced file looked completely normal; nothing in the CLI's
output said anything was wrong.

The fix (`slicer.py`'s `resolve()`) walks a preset's `inherits` chain by
hand, merges parent into child, and writes out one flat file with no
`inherits` link left for the CLI to mishandle. Machine presets are the
one exception: the stock file resolves fine on its own, and flattening it
makes the CLI reject it outright on an unrelated key.

## Reverse-engineering the start-print command

The printer accepts a `project_file` MQTT command that starts a print
already sitting on its storage, which is the one piece of the pipeline
that still needed a touchscreen tap. There's no published spec for it.
The actual payload came from capturing OrcaSlicer's own debug log while it
sent that exact command to start a real print, then reading off the JSON
it sent.

That capture was truncated. `build_start_print_payload()` ships the
fields that were actually captured, not a guess at the rest, and the
README and `DECISIONS.md` both say plainly that it isn't yet verified
against a real printer. I'd rather ship something honestly partial than
pad it with plausible-looking fields I never actually saw a real client
send.

## Mesh checks that catch what a render looks fine

`trimesh`'s `is_watertight` check answers "is every edge shared by
exactly two faces," which two separate, touching-but-disjoint solids can
both satisfy individually. A CSG `union()` that didn't actually fuse into
one physical piece still passes that check. The real test is connected-
component count (`mesh.split(only_watertight=False)`), and even OpenSCAD's
own console "Volumes: N" stat isn't reliable here: a properly-fused
single solid can still report more than one.

Separately, a cavity that tunnels straight through a wall (two separate
cuts overlapping where they shouldn't) is still a closed, watertight
manifold. Neither check above catches it, and neither does eyeballing a
render from the outside: in the real case that found this, the before/
after renders of a buggy water-reservoir module looked nearly identical,
because the wall separating a cut from a cavity was 0.7mm thick, invisible
at normal camera distance. The actual catch was sampling real XYZ
coordinates against the mesh and checking which ones should be solid
versus void (`mesh_check.contains_points`).

## Overhangs, bridges, and the bug my own tests caught before a real user would

Overhang detection (flag a downward-facing surface whose angle from
vertical exceeds what FDM can print without support) and bridge
detection (flag a near-horizontal span likely to sag) are both simple
geometric heuristics: compute each coplanar region's angle from the
build-up axis, flag what's past a threshold. Straightforward to write.

The first version I ran against a real painting-station module flagged
its own base as a 90-degree overhang on a ~10,000mm2 region. That base is
the part of the model sitting flat on the print bed. The build plate
*is* the support there; nothing about this geometry distinguishes "the
bottom of the model, resting on the bed" from "a ledge floating in open
air with nothing under it." Every single real model would have hit this,
because every real model has a base. A check that cries wolf on every
input isn't a check anyone would keep enabled.

The fix excludes any region whose vertices all sit at the mesh's own
lowest point (within a small tolerance), on the assumption the model
will print sitting on a plate at that height. I caught this by actually
running the new check against a real STL already in a working repo, not
just against my own synthetic test fixtures, which is also why the test
suite now includes a case that constructs a shape with something
*genuinely* floating above the model's base (a shelf well above a thin
leg) and confirms that real overhang still gets flagged once the bed
exclusion is in place. A heuristic that only gets tested against shapes
designed to prove the heuristic right doesn't catch this kind of thing.

## What's intentionally not here

Automatic repair of a thin wall, camera/timelapse control over MQTT (two
reverse-engineered payloads tried, neither worked, no real capture exists
yet to try against), and a GUI. This is a CLI toolkit for a specific,
narrow job, not a slicer replacement.
