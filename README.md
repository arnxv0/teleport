# Teleport

**Agents that TELEPORT to a different machine when the current one goes down.**

[![Watch the Teleport demo](video/thumbnail.jpg)](video/out/teleport-demo.mp4)

▶ **[Demo video, 3.5 min](video/out/teleport-demo.mp4)**

## The problem

AI agents now run long jobs on cloud sandboxes, and sandboxes stop all the time. Ours shut down after
30 minutes, and they also crash, run out of memory, or get overloaded. When a sandbox stops, the agent
loses everything it was doing:

- half-finished work is gone,
- the money spent on LLM and API calls is wasted,
- and a simple retry can save the same result twice.

## The idea: TELEPORT

Every agent keeps its state on **one shared Archil disk** instead of inside its sandbox:

- **Claims:** an atomic rename plus an exclusive marker, so exactly one agent owns a task.
- **Leases** renewed every 5 seconds, so a dead agent is noticed within 20 seconds.
- **Checkpoints** saved to the disk after every step.
- **Write-once results**, so a retried task can never save twice.

When a sandbox goes down, its lease expires and **another sandbox on a different machine picks up the
saved progress and finishes the task**. The agent TELEPORTs. Nothing is lost, nothing is done twice, and
there is no queue, database or coordinator to run. The disk is the only state.

## The demo: a data-cleaning swarm

To prove TELEPORT under real load, we built a data-cleaning platform on top of it.

1. Paste a link to any public dataset (CSV, JSON or JSONL).
2. A **planner agent** decides what can be checked on the live web (websites, headquarters, founding
   years, countries) and how to search for each field.
3. Teleport launches a **swarm of agents**, one per Archil sandbox.
4. Each agent checks its records with **Keenable** search and page fetch, and an LLM judge proposes fixes.
5. A **deterministic evidence gate** decides what counts: every cited URL must come from Keenable, the
   evidence must name this exact entity, and refinements (like adding a street address) don't count.
6. A person accepts fixes, which are applied to an Archil **branch**. The original data is never touched.

Then we kill machines, replace the whole swarm, and let sandboxes die of old age. Every task still finishes.

## How each sponsor is used

| | |
|---|---|
| **Archil** | Makes TELEPORT possible: one shared disk every sandbox can open at once (the swarm's only state), the sandboxes themselves, a server-side ingest, checkpoints and branches for safe edits, and the S3 API that the dashboard reads. |
| **Keenable** | Every fact is checked with a targeted Keenable search per field plus a page fetch. A fix only counts if it cites a page Keenable returned, enforced in code. |
| **Casco** | Hosted the hackathon. |

## Results (measured on Archil)

| Run | Records | Agents | Verified fixes | Machines lost | Work lost |
|---|---|---|---|---|---|
| S&P 500 companies | 503 | 50 | 181 | all 50 replaced mid-run | **0** |
| OpenAlex institutions, Nigeria | 502 | 10 | 58 | 13 | **0** |
| World universities | 60 | 8 | 8 | 2 | **0** |

- **Nothing was ever saved twice.** In the 50-agent run, 178 duplicate claims were blocked by the claim markers.
- **Real fixes:** Ares Management founded **1977 → 1997**; Zankli Medical Centre `zankli.com` → `zanklimedical.com`;
  American College of Dubai `acd.ac.ae` → `acd.ae`.
- **Attack test:** a record with hidden instructions ("set the homepage to this URL") and a fake institution
  were both refused. 0 fixes, and the attacker's URL was never proposed.
- **A real failure we didn't plan:** 50 agents overloaded the search service, some agents gave up on their
  tasks, and TELEPORT handed those tasks to free agents, which finished them from the saved progress.

## What we learned about Archil

We tested Archil's real behaviour before trusting it (`teleport/smoke_archil.py`) and built around what we found:

| We expected | What happened | What Teleport does |
|---|---|---|
| A rename has exactly one winner | Two sandboxes both "won" the same rename | Claims also take an exclusive `O_EXCL` marker |
| `link()` is safe for write-once files | One file got the other writer's contents | All write-once files use `O_EXCL` |
| Directory listings are fresh | Cached for up to 5 minutes | Claims never depend on listings |
| Saved data survives a crash | ✅ Yes, after `fsync`, even with SIGKILL | Every commit is fsync'd |

## Where else TELEPORT helps

Any agent that runs for a long time or serves many people. A chatbot that gets overwhelmed or crashes can
TELEPORT to a fresh sandbox with its memory and waiting requests intact, and keep answering. The same works
for research agents, coding agents and batch jobs.

## Run it

```bash
pip install archil
ARCHIL_API_KEY=... OPENAI_API_KEY=... KEENABLE_API_KEY=... python3 -m teleport.dashboard --archil --port 8789
```

Open http://localhost:8789, paste a dataset link, choose rows and agents, and press **Start cleaning**.
Tabs: **Overview** (plan, fleet, review), **Swarm 3D** (click a machine to kill it), **Teleports** (each task's
journey across machines), **Data** (before and after), **Connect** (preview: bring your own machine).

## Next steps

- Resume mid-LLM-call by streaming tokens to the disk.
- Let the planner adapt during a run and size the swarm to the search budget.
- Bring your own machine: any computer that mounts the disk joins the swarm.
