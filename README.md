# Teleport

**Agents that survive their machines dying.**

AI agents increasingly run long jobs on **ephemeral sandboxes**: VMs that time out (ours live 30
minutes), pause when idle, crash, or get reclaimed. When one dies, everything the agent was doing dies
with it: half-finished work is lost, paid LLM and API calls are wasted, and a naive retry can write the
same result twice.

**With Teleport, agents TELEPORT to a different machine when the current one goes down.** Every agent keeps its state on one shared
**Archil disk**, not in its VM:
- **Atomic claims:** a rename plus an exclusive `O_EXCL` marker, so exactly one agent owns a task.
- **Leases** renewed every 5 seconds, so a dead agent is detected within 20 seconds.
- **Per-step checkpoints**, fsync'd to the disk after every step.
- **Write-once results**, so a retried agent can never duplicate a commit.

When a sandbox dies mid-task, its lease expires and **another sandbox, on a different machine, resumes
the task from its last checkpoint**. The work *teleports*: nothing is lost and nothing is done twice. No
queue, database or coordinator is needed; the disk is the swarm's only state, so any machine that
mounts it can join or take over.

### Demonstrated by: an automated data-cleaning swarm

To prove it under real load, we built a platform on top of it. Paste the URL of any public dataset (CSV,
JSON or JSONL). A **planner agent** decides what can be verified on the live web, and Teleport spawns a
swarm of agents in Archil sandboxes. Each agent checks records with **Keenable** search and fetch, an LLM
judge proposes fixes, and a deterministic **evidence gate** decides what counts. Fixes are reviewed by a
human and applied to an Archil **branch**; the original stays untouched.

Then we kill machines mid-run, replace the entire swarm, and let sandboxes die of old age, and watch
every task finish anyway.

## Where else TELEPORT helps

TELEPORT is for any agent that runs for a long time or serves many requests.

- **A smart chatbot or assistant.** It answers thousands of people. When too many requests pile up, or its
  sandbox crashes or runs out of memory, the bot breaks down. With TELEPORT, the bot keeps its memory and its
  list of waiting requests on the shared disk, so it TELEPORTs to a fresh sandbox and keeps answering where
  it left off. Nobody's request is lost or answered twice.
- **Research agents** that read hundreds of pages over hours.
- **Coding agents** that work across many repositories.
- **Batch jobs** like evaluations, scraping, or document processing.

Data cleaning is the first application we built to prove TELEPORT works under real load.

## Results (measured tonight, on Archil)

| Run | Records | Agents | Verified fixes | Flagged | Machines killed | Teleports | Duplicates | Work lost |
|---|---|---|---|---|---|---|---|---|
| OpenAlex institutions, Nigeria (+2 poison records) | 502 | 10 sandboxes, then the whole swarm replaced mid-run | 58 | 39 | 3 hard-killed + 10 replaced | 10 | 0 | **0** |
| World Universities CSV, pasted on the homepage | 60 | 8 sandboxes | 8 | 7 | 2 | 2 | 0 | **0** |
| S&P 500 companies, 50-agent stress test (Keenable rate-limit storm + whole swarm replaced) | 503 | 50 sandboxes in 5 pools | 181 | 36 | 50 replaced mid-run | 466* | 0 written twice (178 duplicate claims blocked) | **0** |

\* Inflated by the rate-limit storm: 310 failed API calls made agents abandon tasks, which were requeued and
resumed elsewhere. It's the harness working under stress, not machines dying. A real error it found:
Ares Management founded **1977 → 1997**.

- **Example fixes:** Zankli Medical Centre `zankli.com` → `zanklimedical.com`; American College of Dubai
  `acd.ac.ae` → `acd.ae`; Al Khawarizmi International College `khawarizmi.com` → `kic.ac.ae`.
- **Poison records:** a prompt injection hidden in the data (it tells the "auditor" to point the
  homepage at an attacker URL) and a plausible but nonexistent institution. **0 fixes**, 4 proposals flagged,
  and the attacker URL was never proposed.
- **Mid-task resume:** for example, a task killed after `probe → search → fetch` on one sandbox resumed on
  another, and only the LLM judge ran again.

## How it works

```
paste URL ─► planner agent (LLM) ─► plan.json ─► ingest (in a sandbox) ─► tasks on the Archil disk
                                                                              │
     ┌──────────────── Archil sandboxes, each running the same stateless worker ┘
     │  claim ─► probe URL ─► Keenable search (one targeted query per field) ─► Keenable fetch
     │        ─► LLM judge ─► evidence gate ─► write-once fix / flag / verdict ─► done
     │  (checkpoint to the disk after every step; lease renewed every 5s)
     └─► machine dies ─► lease expires ─► reaper requeues ─► another machine resumes from the checkpoint
```

All state lives on **one shared Archil disk**. It is the queue, the lock, the log and the result store,
with no database, queue or cluster:

```
plan.json                   the planner agent's decisions (what to verify, how to search)
raw/                        the ingested slice (checkpoint "raw")
tasks/manifest.json         every task id
tasks/todo|claimed|done/    task lifecycle
claims/<tid>.<gen>          O_EXCL claim marker: the real arbiter of who owns a task
leases/<tid>.json           {worker, machine, epoch, expires_at}, or {done: true}
checkpoints/<tid>.json      per-step outputs, so another machine resumes mid-task
verdicts/<tid>.json         write-once outcome per record (the "no lost work" proof)
fixes/ flags/ accepted/     write-once fixes with evidence, flags, and human review decisions
log/<worker>.jsonl          per-worker append-only events
```

### What we learned about Archil (and engineered around)

We ran a smoke test against real sandboxes before trusting any assumption (`teleport/smoke_archil.py`):

| Assumption | What actually happened on a conditional mount | What Teleport does |
|---|---|---|
| `rename` gives exactly one winner | Two sandboxes both "won" the same rename once in 200 | Claim = rename **plus** an `O_CREAT\|O_EXCL` marker per generation; the loser logs `duplicate_prevented` |
| `link(tmp, final)` is write-once | One file ended up with the other writer's contents | All write-once files use `O_EXCL` (50/50 exclusive in the test) |
| `setsid nohup … &` survives `exec` | The process dies when `exec` returns | `sandbox.run()` + `disconnect()` |
| Listings/`stat` are fresh | Listings are cached up to 5 min and `stat` up to 30s, so finished tasks looked orphaned | Manifest-based claiming, a `requeued.json` read via `open()`, done-leases instead of deletes |
| Sandboxes stay up | 30-minute max TTL; detached processes pause after 30 min idle | `idle_ttl_seconds=0`; leases plus checkpoints make losing a sandbox routine |
| `fsync` is durable | ✅ survives SIGKILL and `stop()`; visible to other sandboxes and the S3 API | Every commit is fsync'd |

### The evidence gate (deterministic; the LLM cannot talk its way past it)

A proposed change is a **verified fix** only if:
1. every cited URL is one Keenable actually returned in this run (no invented evidence),
2. the cited evidence contains every distinctive word of this record's name (not a lookalike),
3. for URL fields, the new domain is in the evidence and the current URL is not confirmed alive,
4. it isn't a downgrade (for example to "other") and keeps the field's format (for example ISO-2).

Anything else is **flagged for human review**. Cosmetic changes (http → https, www) are dropped.

## Running it

Python 3.10+. Workers are stdlib only; the Archil backend needs `pip install archil`.

```bash
# Archil: the dashboard creates a disk, runs the planner, ingests and launches sandboxes from a pasted URL
ARCHIL_API_KEY=... OPENAI_API_KEY=... KEENABLE_API_KEY=... python3 -m teleport.dashboard --archil --port 8789
#   /            Overview: paste a URL, plan, fleet, review table (Accept → branch)
#   /3d          Swarm 3D: machines around the Archil disk; click a machine to kill it
#   /teleports   every task that survived a machine dying, as a timeline across machines
#   /connect     preview: bring your own machine

# Local (one laptop, workers labelled as separate machines)
python3 -m teleport.dashboard --port 8788

# CLI equivalents
python3 -m teleport.ingest --url <dataset-url> --limit 200
python3 -m teleport.swarm archil launch --n 8 --machine A
python3 -m teleport.swarm archil kill --machine A --n 4
python3 -m teleport.apply_fixes --archil      # accepted fixes → cleaned/ with provenance
.venv/bin/python teleport/smoke_archil.py     # verify Archil semantics (A–G)
```

| Variable | Purpose |
|---|---|
| `ARCHIL_API_KEY`, `ARCHIL_REGION` | Archil backend (default region `aws-us-east-1`) |
| `OPENAI_API_KEY`, `TELEPORT_MODEL` | planner and judge (default `gpt-4.1-mini`) |
| `KEENABLE_API_KEY` | Keenable search and fetch (falls back to the keyless public endpoint) |
| `TELEPORT_LEASE_TTL`, `TELEPORT_HEARTBEAT` | lease lifetime and refresh, in seconds (20 / 5) |
| `TELEPORT_STEP_DELAY` | slow each step down so a live demo is visible |
| `TELEPORT_SANDBOX_TTL` | sandbox max lifetime (1800 = Free-plan cap) |

## Code map

`teleport/config.py` settings · `fs.py` durable file primitives · `tasks.py` claim/lease/reap ·
`events.py` logs · `plan.py` planner agent · `sources.py` CSV/JSON/JSONL readers · `checks/` probe,
Keenable, judge, gate · `worker.py` · `reaper.py` · `ingest.py` · `backends/` local and Archil ·
`view.py` + `dashboard.py` + `*.html` dashboard · `apply_fixes.py` branch output · `smoke_archil.py`

## Honest limits and next steps

- Resume is per step. A machine killed *during* the LLM call loses that one call. Next: stream tokens to the
  disk and resume generation with a prefill.
- A human picks the swarm size. Next: an autoscaler agent driven by queue depth and sandbox TTLs.
- Bring your own machine (`/connect`) is a preview: the join script exists, but we don't demo it.

## Hackathon tracks

| Track | Why Teleport fits |
|---|---|
| Most Creative | Paste any dataset: a planner agent designs the checks, and a swarm cleans it, and its agents TELEPORT to new machines when theirs go down. Git for public data. |
| Best Use of Archil | The disk is the swarm's only state (queue, locks, logs, results); sandboxes and the SDK for the whole lifecycle; server-side ingest; checkpoints and branches; the S3 API for the dashboard; a measured smoke test of Archil's semantics. |
| Technical Execution | O_EXCL claims with generations, leases, write-once commits, per-step checkpoints: **0 lost, 0 duplicated** across 15 machines killed or replaced mid-task. |
| Keenable | One targeted search per field, plus fetch; every verified fix cites Keenable URLs, enforced in code. |
