# Herdr-backed agent bridges — design

Date: 2026-09-01
Status: approved design, awaiting implementation plan
Scope: rewrite of both inter-agent bridges on this machine

| Bridge | Direction | Repo | Installed at |
|---|---|---|---|
| `hermes-bridge` | Claude Code → Hermes Agent ("Bean") | `fabzter/hermes-bridge` | `~/.claude/skills/hermes-bridge` (the clone itself) |
| `claude-bridge` | Hermes Agent → Claude Code | `fabzter/hermes-claude-bridge` | dev clone `~/src/hermes-claude-bridge`; hub install `~/.hermes/skills/claude-bridge` |

## 1. Why rewrite

The current bridges predate herdr. `hermes-bridge` is a 1500-line bash tmux
driver that scrapes prompt glyphs pinned to Hermes v0.20.0 and captures the
Hermes session id from pane text. `claude-bridge` runs `claude -p` headless,
read-only, one conversation per name, with no way for Claude to ask a human
anything.

herdr 0.8.2 is installed with a running default server and both the Claude
and Hermes integrations (v8 and v5). It already provides what the bridges
hand-rolled:

- **Agent state detection.** herdr's Hermes manifest classifies the dangerous-
  command approval, credential prompt, and clarification prompt as `blocked`,
  and `herdr agent explain --json` names the matched rule. Verified offline
  against synthetic screens for all three. The Claude manifest covers Claude's
  permission and question dialogs the same way.
- **Native session identity.** The installed integrations report the Hermes
  and Claude session ids to herdr; `agent get` exposes them as
  `agent_session.value`. No more scraping.
- **Atomic prompt + wait.** `agent prompt --wait` honors bracketed paste
  (multiline safe), refuses to type into a `blocked` agent, and waits server-
  side for the settled state.
- **Persistence and restore.** Panes survive client detach. After a server
  restart herdr relaunches `hermes --resume <id>` / `claude --resume <id>`
  itself.
- **Human visibility.** Every bridge conversation is a real pane in the herdr
  sidebar; the human can `herdr agent attach NAME` or open `herdr`.
- **Events.** `events.subscribe` on `pane.agent_status_changed` lets a
  watcher react to `blocked`/`done` without polling.

herdr's CLI and socket work from outside a pane (verified: `herdr workspace
list`, raw `ping` over `~/.config/herdr/herdr.sock`), so neither agent has to
live inside herdr for the bridges to use it. A live spike on 2026-09-01 in an
isolated named session confirmed the end-to-end path: `agent start --kind
hermes -- chat --cli --source tool`, `agent prompt --wait`, reply readable via
`agent read --source recent-unwrapped`, session id surfaced in
`agent_session`, and headless restore after a server restart.

## 2. Goals and non-goals

Goals

1. Replace tmux with herdr in both directions; delete the glyph scraper.
2. Keep the existing safety contract: nothing auto-approves, no `--yolo`, no
   `--dangerously-skip-permissions`, secrets are never typed by an agent.
3. Multiple concurrent Claude sessions from Hermes (today: one).
4. Rely on herdr for resilience (restore after server restart) while owning
   shutdown so sessions do not accumulate.
5. Forward Claude's blocked/done events into Hermes via Hermes's webhook
   adapter (already running on port 8644) so Hermes can relay Claude's
   questions to the user instead of polling.
6. Both SKILL.md files teach the calling agent every herdr nuance it needs.

Non-goals

- A herdr plugin (plugin v1 event hooks fire on layout lifecycle, not agent
  status; a watcher would still be needed).
- Rewriting the unrelated `bean_bridge` Cosmos DB skill in `~/.hermes/skills`.
- Windows support. macOS/Linux only.
- Enabling herdr toasts (`ui.toast.delivery` is `off` in the user's config).
  Documented as optional; not changed by the bridges.

## 3. Shared architecture

Both bridges are single-file **Python 3 (stdlib only)** scripts. Rationale:
the user asked for CLI plus socket API; JSON handling and a raw Unix-socket
client are painful in bash 3.2; python3 exists on the host and Hermes's own
herdr plugin is Python. The Hermes skill installer copies files verbatim
without the exec bit, so the Hermes side is always invoked as
`python3 <path>`; the Claude side keeps a shebang and exec bit.

### 3.1 Control surfaces

| Need | Surface |
|---|---|
| Create/find workspace and tabs, start/prompt/read/explain/close | `herdr` CLI (JSON stdout) |
| Wait for a specific pane status change once | raw socket `events.wait` |
| Long-lived status stream (watcher) | raw socket `events.subscribe` |
| Server liveness | raw socket `ping` |

**Both bridges live in the named herdr session `agents`** (decision
2026-09-01), never in the user's default session. Every `herdr` CLI call is
made with `HERDR_SESSION=agents` in its environment, and the raw socket client
connects to `~/.config/herdr/sessions/agents/herdr.sock`. Named sessions are
fully separate runtime namespaces (own server process, socket, `session.json`
and restore state), so:

- the bridges cannot see or disturb the user's own workspaces, and the user's
  everyday `herdr` sidebar does not show bridge panes;
- auto-start only ever starts the `agents` server;
- `herdr session stop agents` is a complete kill switch for everything the
  bridges created, and `herdr session delete agents` wipes their persisted
  layout.

The human watches or intervenes with `herdr session attach agents` (full UI)
or `HERDR_SESSION=agents herdr agent attach NAME` (one pane). Both SKILL.md
files state this. The session name is a constant in each script
(`HERDR_BRIDGE_SESSION` env var overrides it for tests, which use throwaway
names such as `bridge-test-<pid>`).

### 3.2 Server availability

Every subcommand runs `ensure_server()` first:

1. `ping` over the `agents` socket. On success continue.
2. Otherwise spawn `HERDR_SESSION=agents herdr server` detached
   (`start_new_session=True`, stdio to
   `~/.config/herdr/sessions/agents/herdr-server.log`), then poll `ping` up to
   10 s. Verified: `herdr server` honors `HERDR_SESSION`, and the named
   server restores its saved layout and agents on start without any client.
3. Failure exits 9 (`server_unavailable`) with the log path in the message.

The user intends to run the herdr server as a service; the auto-start is the
fallback when it is not up. The bridges never run `herdr server stop` or
`herdr session stop` on their own.

### 3.3 Topology

One workspace per bridge, found by label, created with `--no-focus` if
missing:

| Bridge | Workspace label | Tab label | Agent name |
|---|---|---|---|
| hermes-bridge | `hermes-bridge` | `NAME` | `NAME` |
| claude-bridge | `claude-bridge` | `NAME` | `NAME` |

Each named session owns one tab whose root pane hosts the agent. The tab is
created with `--cwd` (Hermes: `$HOME`; Claude: `--cwd` argument, default
`$HOME`) and `--no-focus`. Bridges never read, write, or close anything
outside their own workspace. If two live agents share a name (should not
happen; names are unique among live agents, herdr enforces this), the bridge
refuses with exit 1 rather than guessing.

Because agent names are the herdr identity, **NAME must match herdr's rule
`[a-z][a-z0-9_-]{0,31}`**. This is stricter than the old bridge (`.`,
uppercase were allowed). Old state files with non-conforming names are left
in place and ignored; the SKILL.md tells Claude to pick a new conforming name.

### 3.4 State files

`<skill dir>/state/<NAME>.json`:

```json
{"agent_session_id": "…", "pane_id": "w3:p1", "tab_id": "w3:t2",
 "cwd": "/Users/fabzter", "updated_at": "2026-09-01T16:40:00Z"}
```

`agent_session_id` is refreshed from herdr's `agent_session.value` after every
successful `start`/`open` and `send`/`ask`. The Hermes plugin reports it on
`on_session_start`; the Claude hook on `SessionStart`. If herdr has no
reference yet, the bridge keeps whatever it had. Existing `*.session-id`
files from the old bridge are read once as a migration source for
`agent_session_id` (so current conversations resume) and then ignored.

### 3.5 Resolving a session (shared algorithm)

`resolve(NAME)` returns one of `live`, `restorable`, `missing`:

1. `herdr agent list` → an agent named `NAME` in the bridge workspace →
   `live`. This also covers herdr's own restore: verified 2026-09-01 in an
   isolated named session that after `session stop` + server restart herdr
   relaunches the agent **headless (no client attached)** and **keeps the
   agent name** (`agent_name` is persisted in `session.json`). No adoption or
   rename step is needed.
2. Else if the stored `pane_id` still exists and hosts an idle shell (`pane
   get` has no agent, `process-info` foreground is the shell) → `restorable`:
   `agent start` in that pane with `--resume <agent_session_id>`. This is the
   path after the agent process dies (crash, `/exit` without `stop`).
3. Else `missing`: create the tab, then `agent start` (with `--resume` if an
   id is stored and `--fresh` was not given).

**Session id timing (verified):** herdr's `agent_session` is `null` right
after `agent start`; the Hermes plugin reports the id on the first LLM call,
so it appears after the first `send`. `session NAME` therefore returns the
stored id (possibly empty) until a message has been sent, and `start` alone
cannot persist a new id. The Claude hook reports on `SessionStart`, so the
Claude id is available immediately after `open`.

### 3.6 Launch commands

| Bridge | `agent start` args |
|---|---|
| hermes-bridge | `--kind hermes -- chat --cli --source tool [--resume ID]` |
| claude-bridge | `--kind claude -- [--resume ID] [--permission-mode default] [--allowedTools …]` |

Startup timeout 60 s (herdr default is 30 s; Hermes loads plugins and memory
at startup and can exceed that). `agent_not_ready` (blocked during startup) is surfaced as the
corresponding blocked state, not as failure.

**herdr restore nuance (verified 2026-09-01, documented in both SKILL.md):**
herdr's own restore runs exactly the command in the session-state table,
`hermes --resume <id>` / `claude --resume <id>`, and drops every argument the
bridge passed to `agent start` (`pane process-info` showed argv
`hermes --resume 20260901_181533_8626d1`). Consequences:

- Hermes: `--cli` is dropped but the user's `display.interface` is `cli`, so
  the classic REPL still comes up. `--source tool` is dropped too, but the
  session record keeps its original `tool` source, so the resumed
  conversation stays hidden from `hermes sessions list` (verified). Net
  effect for Hermes: none the bridge needs to act on.
- Claude: `--permission-mode`, `--allowedTools`, and `--model` are dropped.
  A session opened `--read-only` comes back in Claude's default permission
  mode after a herdr restart. The bridge records the requested flags in the
  state file and, on `resolve`, compares them with the live argv from
  `pane process-info`; on mismatch it logs a warning on stderr and `ask`
  refuses with exit 1 until the caller runs `close` + `open`. The SKILL.md
  spells this out.

**Known issue on this host (not a bridge bug):** in the same test, Hermes
segfaulted right after completing its first turn in a *resumed* session, both
under herdr's restore and under an explicit `hermes chat --cli --source tool
--resume <id>`; a fresh session survived the same turn. The crash report
faults in `_lbug.cpython-311-darwin.so` (`NodeTableScanState::scanNext`),
i.e. the LadybugDB memory provider. Until that is fixed, resume is
unreliable here; the bridge handles it as `dead` → `restorable` and the
SKILL.md tells the agent to prefer `--fresh` when a resumed session dies
twice in a row.

### 3.7 State model

herdr status → bridge state, refined by `agent explain --json`'s
`matched_rule.id` when status is `blocked`:

| herdr status | matched rule | bridge state | exit |
|---|---|---|---|
| `idle` / `done` | — | `idle` | 0 |
| `working` | — | `busy` | 8 (on send) |
| `blocked` | Hermes `dangerous_command_approval`, `confirmation_prompt` | `approval` | 3 |
| `blocked` | Hermes `credential_prompt` | `secret` | 4 |
| `blocked` | Hermes `clarification_prompt` | `clarify` | 5 |
| `blocked` | Claude `bash_permission_prompt`, `generic_permission_prompt`, `legacy_no_prompt_blocker` | `approval` | 3 |
| `blocked` | Claude `live_blocked_form`, `mcp_elicitation_prompt`, `dynamic_workflow_prompt` | `clarify` | 5 |
| `blocked` | anything else | `blocked` | 3 |
| `unknown` | — | `unknown` | 7 |
| pane exists, no agent | — | `dead` | 7 |
| no pane | — | `missing` | 2 |

`done` is folded into `idle`: it only means the human has not looked at the
tab. Rule ids are read from the live manifest; unknown ids degrade to the
generic `blocked` rather than erroring, so a manifest update cannot break the
bridge, only coarsen it.

### 3.8 Prompt, wait, and reply extraction

`send`/`ask`:

1. `state` must be `idle`; `busy` → exit 8 (refuse, do not interrupt);
   blocked states → their exit code.
2. Snapshot `agent read --source recent-unwrapped --lines 400` (the
   "before" text).
3. `herdr agent prompt NAME TEXT --wait --timeout MS` (default 600 000 ms).
   herdr sends TEXT via bracketed paste plus Enter, so multiline is safe;
   `send`/`ask` accept `-f FILE` and `-` for stdin.
4. On `agent_prompt_stalled`, fall back to `agent wait NAME --timeout MS`
   (Hermes sometimes takes >5 s to show the working indicator).
5. Read the "after" text with the same read call. **Hermes** (classic REPL,
   normal scrollback): reply = lines after the last echoed prompt line
   containing the first line of TEXT, minus the trailing prompt line.
   **Claude** (alternate screen): herdr's idle-agent history read pages via
   mouse scroll when `--lines` exceeds the viewport; reply = text after the
   last occurrence of the sent prompt, minus Claude's input box and status
   lines. If the sent prompt is not found in the after-text (very long
   replies), fall back to "everything new relative to the before-snapshot",
   then to the raw last 120 lines, and set a `truncated` flag on stderr. The
   SKILL.md documents the herdr-recommended fallback: ask the agent to write
   the answer to a file under `$TMPDIR` and reply with the path.
6. Print the reply on stdout; exit with the code of the final state (0 idle,
   3/4/5 blocked variants). A blocked final state also prints the dialog text
   (`agent read --source visible`) so the caller can relay it.

`wait NAME [--timeout MS]` = `agent wait` then `state`. `peek`/`read` =
`agent read --source recent-unwrapped --lines N` (default 80) verbatim.

### 3.9 Answering dialogs

- `answer NAME TEXT`: allowed only in `clarify`. Because `agent prompt`
  refuses blocked agents by design, it uses `pane send-text` (bracketed-paste
  aware) followed by `pane send-keys enter`, then verifies the state left
  `clarify`.
- `approve NAME` (hermes-bridge only): allowed only in `approval`. Reads the
  visible menu, locates the "Allow once" row and the cursor row, moves with
  `up`/`down` re-reading after each key until the cursor sits on "Allow once",
  then `enter`. If the menu shape is not recognized (no numbered rows, no
  "Allow once", cursor not found) it refuses with exit 1. Never used unless
  the human said yes in chat — SKILL.md rule, unchanged.
- `deny NAME [REASON]`: same navigation to "Deny"; REASON goes to stderr.
- `keys NAME KEY…` (claude-bridge only): raw `agent send-keys`, for the human-
  authorized case where Hermes is told "approve Claude's prompt". The SKILL.md
  restricts it to that case and requires reading the dialog first.

### 3.10 Shutdown and hygiene

- `stop`/`close NAME`: if live, `agent prompt NAME /exit` (Hermes) or
  `agent send-keys NAME ctrl+d` twice (Claude) → `events.wait pane_exited`
  10 s → `tab close`. If already missing, exit 0. State file keeps the
  session id for a later resume.
- `gc`: closes tabs in the bridge workspace whose pane no longer hosts an
  agent (agent exited) and whose name has no live agent. Never closes tabs
  with a live agent.
- `forget NAME`: deletes the state file (explicit way to drop a conversation).
- The bridges never close the workspace itself and never touch other
  workspaces.

### 3.11 Exit codes (both bridges)

`0` ok · `1` generic/refused · `2` missing session · `3` approval/blocked ·
`4` secret · `5` clarify · `6` timeout · `7` dead/unknown · `8` busy ·
`9` herdr server unavailable · `2` also for bad usage (matches old
claude-bridge). `state` always prints a word and exits 0.

## 4. hermes-bridge (Claude → Hermes) specifics

CLI (positional NAME replaces the old mandatory `--session NAME`; `--session
NAME` is still accepted as an alias for one release):

```
hermes-bridge start   NAME [--fresh] [--timeout S]
hermes-bridge send    NAME (TEXT | -f FILE | -) [--timeout S]
hermes-bridge state   NAME
hermes-bridge wait    NAME [--timeout S]
hermes-bridge peek    NAME [-n LINES]
hermes-bridge approve NAME
hermes-bridge deny    NAME [REASON]
hermes-bridge answer  NAME TEXT
hermes-bridge session NAME
hermes-bridge stop    NAME
hermes-bridge forget  NAME
hermes-bridge list
hermes-bridge gc
hermes-bridge log [-n N]            # tails ~/.hermes/logs/agent.log (unchanged)
```

`send-file` is removed (`send -f`). `session` prints herdr's live
`agent_session.value` when available, else the stored one. `list` prints
NAME, pane id, state, and session id for every tab in the workspace.

SKILL.md rewrite covers: the NAME rule change; herdr as the transport and
what that means (`herdr agent attach NAME` for the human, sidebar state);
the state table above; lifecycle authority text carried over from the
current SKILL.md (Claude owns Hermes session lifecycle, gateway restart
rule, the mid-approval exception); herdr restore/adoption nuance; the
`done` vs `idle` note; knowledge-exchange recipes (unchanged in substance);
the removal of every tmux reference.

## 5. claude-bridge (Hermes → Claude) specifics

```
claude-bridge open    NAME [--cwd DIR] [--read-only] [--fresh] [--model M]
claude-bridge ask     NAME (TEXT | -f FILE | -) [--timeout S]
claude-bridge state   NAME
claude-bridge read    NAME [-n LINES]
claude-bridge answer  NAME TEXT
claude-bridge keys    NAME KEY...
claude-bridge session NAME
claude-bridge close   NAME
claude-bridge forget  NAME
claude-bridge list
claude-bridge gc
claude-bridge watch   start|stop|status
claude-bridge setup-webhook [--deliver telegram] [--route claude-bridge]
```

- `ask` auto-`open`s a missing session (default cwd `$HOME`) so the simple
  path stays one command, as today.
- `--read-only` maps to `--allowedTools Read Grep Glob WebSearch WebFetch`
  plus `--permission-mode default`; without it Claude runs with its normal
  permission prompts, which surface as `approval` and must be relayed to the
  human (Hermes never answers them on its own).
- Multiple sessions: one tab each; `list` shows them all. The SKILL.md gives
  naming guidance (one name per topic/repo, e.g. `cv`, `luca-backend`).

### 5.1 Event forwarding into Hermes

`watch start` forks a daemon (pidfile in the state dir) that:

1. Lists bridge panes, opens one socket connection, and sends one
   `events.subscribe` with a `pane.agent_status_changed` subscription per
   pane plus `pane.created`/`pane.closed` on the workspace to keep the set
   current (re-subscribing when panes change).
2. On `blocked` or `done` for a pane whose previous status was `working`,
   reads the pane (`agent read --source visible`) and POSTs JSON to
   `http://127.0.0.1:8644/webhooks/<route>` with the HMAC-SHA256 signature
   header Hermes's webhook adapter expects:
   `{"event_type":"claude_blocked"|"claude_done","session":NAME,
   "pane_id":…, "state":…, "excerpt":…}`.
3. Debounces per pane (one POST per state transition) and reconnects with
   backoff if the socket drops (e.g. herdr live handoff).

`setup-webhook` runs `hermes webhook subscribe <route> --events
claude_blocked,claude_done --skills claude-bridge --deliver <target>
--prompt "Claude session {session} is {state}. Excerpt:\n{excerpt}\n\nUse the
claude-bridge skill to read the full state and relay the question or result
to the user."`, captures the generated secret from its output, and stores it
0600 in the state dir for the watcher. The webhook adapter is already
enabled on this host (`/health` returns ok); the route is created
dynamically, no config.yaml edit. If Hermes prints the secret differently
than expected, `setup-webhook --secret S` lets the user pass one explicitly.

The watcher is optional. Without it the SKILL.md tells Hermes to use
`ask` (which blocks until settled) or `state`.

### 5.2 Hermes SKILL.md rewrite

Routing rules carried over (coding-expert / stronger-model intent, not the
generic "second opinion"). New content: multiple named sessions and how to
choose names; the `blocked` taxonomy and the relay rule ("Claude's approval
prompts are for the user, not for you; report them, and only press keys when
the user explicitly told you to"); `--read-only` vs default; herdr restore
nuance; `watch`/webhook behavior and what an incoming `claude_blocked`
webhook prompt looks like; the alt-screen read fallback; `herdr agent attach
NAME` for the user; requirements (python3, herdr ≥ 0.8, claude on PATH);
invocation via `python3 ~/.hermes/skills/claude-bridge/scripts/claude-bridge`.

## 6. Error handling

- All herdr CLI errors arrive as JSON on stderr with exit 1; the bridge
  parses `error.code` and maps: `agent_blocked` → re-run `state` and exit
  with its code; `timeout` → 6; `pane_not_found`/`not_found` → 2;
  `agent_not_running` → 7; anything else → 1 with the herdr message.
- herdr CLI exit 2 (usage) is a bridge bug → exit 1 with the command line
  printed, so it is visible in logs.
- Socket disconnects mid-`events.wait` fall back to polling `agent get`
  every 2 s until the caller's timeout.
- The bridge never retries `agent prompt` on its own (a retry re-sends the
  message).

## 7. Testing

TDD, Python `unittest`, fixtures under `tests/fixtures/`:

1. Name validation (herdr rule), state-file round trip, old `.session-id`
   migration.
2. State mapping from `(agent_status, matched_rule.id)` pairs, including
   unknown rule ids and `done` → `idle`.
3. Reply extraction for Hermes REPL transcripts and Claude alt-screen reads,
   including the not-found and truncated fallbacks.
4. Approval-menu navigation planner: given a menu snapshot, compute the key
   sequence to reach "Allow once"/"Deny" or refuse.
5. Webhook payload + HMAC signature construction.
6. herdr error JSON → exit code mapping.

The herdr CLI and socket are wrapped behind one small `Herdr` class so tests
inject a fake. Live end-to-end on this machine after the unit suite passes:
`start`, `send`, reply capture, a real dangerous-command approval handled via
`deny`, `stop`; `open`, `ask`, a real Claude permission prompt reported as
`approval`, `close`; `watch` delivering one webhook to a `--deliver log`
route.

## 8. Rollout

1. Implement and test `hermes-bridge` in `~/.claude/skills/hermes-bridge`
   (it is the clone; the skill is live as soon as it is committed). Remove
   `scripts/hermes-bridge` (bash) only after the Python replacement passes
   the live run; keep the same script name so existing references work.
2. Implement and test `claude-bridge` in `~/src/hermes-claude-bridge`, push,
   then `hermes skills update` (or reinstall from
   `fabzter/hermes-claude-bridge/claude-bridge --yes`) to refresh
   `~/.hermes/skills/claude-bridge`.
3. Update both READMEs; cross-link; note herdr ≥ 0.8.2 as a requirement.
4. Commit and push both repos.
