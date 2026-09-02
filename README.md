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
if it isn't already answering, logging its stdio to its own
`herdr-server.spawn.log` (rotated via `rotate_log` before each spawn, kept
separate from herdr's own `herdr-server.log` in the same directory) —
`rotate_log(path, max_bytes=5 MiB, keep=2) -> bool` rotates `path` to `.1`
through `.keep` and returns whether it rotated, a no-op (`False`) when the
file is missing, under `max_bytes`, or `keep < 1`. The
**state store** (`StateStore`) persists
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
operations a bridge actually needs: resolving the bridge's herdr workspace,
creating it on first use and caching it thereafter (`workspace`), with
`refresh=True` or a prior call to `invalidate_workspace` forcing a fresh
`workspace list`/`create` round-trip — every topology call (`tabs`, `panes`,
`agents`, and anything that creates a tab) goes through this cache and
retries itself once, re-resolving the workspace, if herdr reports the
cached id gone (`workspace_not_found`/`not_found`); resolving a session name
to a live agent, a restorable idle pane, or nothing (`resolve`); starting or
resuming an agent in its workspace (`start`); reading and classifying its
current state (`state`); sending a prompt and waiting for a reply (`send`);
waiting for an agent to reach one of a set of target statuses, preferring
herdr's own server-side `agent wait` but falling back to polling `state`
if that call itself fails for a reason unrelated to the wait outcome, such
as the socket dropping mid-call (`wait_status`); answering a clarification
by sending the text and an enter keypress, then polling `state` every
`poll_s` (default 0.25s) until it leaves `clarify` or `settle_s` (default
5s) runs out (`answer`); walking an approval menu (`navigate_menu`); and
stopping a session or garbage-collecting stale tabs (`stop`, `gc`,
`list_sessions`). Every public `Bridge` entry point validates `name` via
`validate_name` before it does anything else, so an invalid name never
reaches herdr. Before launching an agent in a freshly created pane, `start`
waits up to `BridgeConfig.shell_settle_s` (70s by default, polling every
`poll_s`) for the pane's shell to settle, since a just-created pane can
briefly have something other than a plain shell in the foreground; lower
`shell_settle_s` in your own `BridgeConfig` if your setup never hits that
worst case and you'd rather fail fast. All of these polling loops read the
clock and sleep through the module-level `_sleep`/`_now` hooks (aliases for
`time.sleep`/`time.time` by default), so tests can patch `herdrbridge._sleep`
and `herdrbridge._now` to drive deadline loops without waiting in real time.

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
