# herdrbridge

`herdrbridge.py` is a single, stdlib-only Python module (3.9+) that provides
the plumbing for driving coding agents through [herdr](https://herdr.dev)'s
CLI and Unix-socket API: running an agent's CLI wrapper, tracking session
state across restarts, classifying what an agent's pane is currently doing,
pulling a clean reply out of a noisy terminal transcript, and planning a
keypress sequence through herdr's approval menus. It has no third-party
dependencies and is not installed as a package — it is a single file, plus a
small test-support module and a handful of fixture transcripts, that gets
copied verbatim into the repos that use it. It is shared by two agent
bridges: `fabzter/hermes-bridge` (Claude Code driving Hermes Agent) and
`fabzter/hermes-claude-bridge` (Hermes Agent driving Claude Code).

The module is organized into a few areas, in the order they appear in the
file. The **client** (`Herdr`) wraps the `herdr` binary and its socket:
`cli`/`cli_text` run CLI subcommands and parse JSON or return raw stdout,
`request`/`subscribe` speak the socket protocol directly for one-shot calls
and event streams, and `ensure_server` starts the per-session herdr server
if it isn't already answering. The **state store** (`StateStore`) persists
one small JSON file per session name — pane/tab ids and the agent's session
id — so a session can be found again after herdr or the bridge process
restarts, and it transparently migrates the previous generation's
`<name>.session-id` files. **Classification** (`classify`, `state_exit`)
turns herdr's raw `agent_status` plus a matched rule id into one of a fixed
set of bridge states (`idle`, `busy`, `approval`, `secret`, `clarify`,
`blocked`, `dead`, `missing`, `unknown`) and maps each to a process exit
code. **Reply extraction** (`extract_reply`) diffs a pane read taken before
and after a prompt to isolate the agent's new output, handling both
Hermes's boxed REPL replies and Claude's alt-screen chrome. The **menu
planner** (`parse_menu`, `plan_menu_step`) reads a herdr approval menu off
the screen and plans one arrow-key/enter step at a time toward a target
option, refusing to guess when the screen doesn't look like a menu it
recognizes. Finally, **`Bridge`** ties all of this together into the
operations a bridge actually needs: resolving a session name to a live
agent, a restorable idle pane, or nothing (`resolve`); starting or resuming
an agent in its workspace (`start`); reading and classifying its current
state (`state`); sending a prompt and waiting for a reply (`send`);
answering a clarification or walking an approval menu (`answer`,
`navigate_menu`); and stopping a session or garbage-collecting stale tabs
(`stop`, `gc`, `list_sessions`).

Downstream repos never install this as a dependency; each bridge repo vendors
this file with a `tools/sync-lib.sh` that fetches `herdrbridge.py`,
`tests/fakes.py`, and the transcript fixtures under `tests/fixtures/` from
this repo at a specific commit, recording that commit's hash in a local
`herdrbridge.version` file. To pick up a change made here, a downstream repo
re-runs its `tools/sync-lib.sh` against the new commit and updates
`herdrbridge.version` accordingly — the library is developed and tested in
this repo first, and downstream repos update to it deliberately rather than
tracking it live.

Requires herdr >= 0.8.2.

Tests: `python3 -m unittest discover -s tests -v`

## Known Hermes issues

Hermes v0.20.0 may segfault in the LadybugDB memory provider after a turn.
When that happens the bridge reports the session as `dead`; calling `start`
again resumes it.
