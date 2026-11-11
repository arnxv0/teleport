# Teleport: Hackathon Plan

Archil "Agents Need Solid Ground" hackathon, Nov 10 2026. Demos start 9:30pm and submissions close 10:00pm.

---

## 1. Pitch

**Teleport is a swarm of agents that cleans a large, messy public dataset. Its agents keep working when their machines die or move.**
Every fix is verified against the live web with Keenable and lands on an Archil branch, so the original data is never touched.

**One-liner for the judges:**
> "30 agents cleaned the world's research-institution records with no laptop downloads and no warehouse. We killed half of them mid-run, they teleported to new machines, and not one fix was lost or duplicated."

**What we build vs. what Archil provides:**
- **Archil provides:** durable disks, sandboxes, branches and checkpoints, and an S3 object API.
- **We build:** the swarm harness on top. It handles task leases, checkpoints, and fixes that are safe to retry, so a dead agent never loses or repeats work. That harness is what makes teleporting safe.

### Why each judge cares

| Judge | Cares about | What Teleport shows them |
|---|---|---|
| Hunter Leath (Archil) | Swarms on a shared file system; "the file system is the agent" | Agents are stateless; all their state lives on one disk; they resume anywhere; each fix is on a branch |
| René Brandel (Casco) | Failure and attack resistance | A live crash-and-recover; a poisoned record the swarm refuses to "fix" without evidence |
| George Trushevskiy (Keenable) | Real use of Keenable | Every fix is verified with search and page fetch, and the evidence URL is stored |
| Nicholas Arcolano (Jellyfish) | Measured results | Counts of errors found, fixes verified, duplicates avoided, and work lost (0) |
| Rotem Yakir (YC founder) | Is it a product? | "Git for public data": reviewable diffs with evidence |

---

## 2. Dataset

**Pick: OpenAlex institutions** (universities, labs and companies credited on research papers).
- **Location:** public S3 bucket `s3://openalex` in us-east-1, under the `data/` prefix. No AWS account is needed when you use `--no-sign-request`. The license is CC0.
- **Docs:** https://help.openalex.org/access/snapshot/ and https://registry.opendata.aws/openalex/

**Why this dataset:** it's messy in ways the web can verify, it's a manageable size, and it matters because it feeds rankings and research credit.

**What the swarm checks for each record:**

| Check | How it's detected | How Keenable verifies it |
|---|---|---|
| Dead or wrong homepage | URL fails, redirects, or points elsewhere | Search for the official site |
| Wrong country or type | Location and name disagree | Fetch the official site's about or contact page |
| Duplicate institution | Near-identical names in the same city | Both resolve to the same official site |
| Renamed or merged | The name is missing from the current web | Search for rename or merger news |

**Scope for tonight:** use one country or one slice of about 2,000 to 5,000 records. Don't use the whole entity.

**Constraint:** Archil branches and checkpoints only work on disks that are **not** synced to a data source like S3. Archil's docs also only cover connecting buckets you control, using a bucket policy or credentials.
- **Workaround:** copy the slice **server-side** into an Archil-managed disk with `aws s3 cp --no-sign-request` through Archil exec. Nothing lands on a laptop, and branches still work.
- **In the pitch,** say "no laptop downloads, no warehouse," not "zero downloads."

---

## 3. Architecture

```
                    ┌────────────────────────── Archil disk "teleport" ──────────────────────────┐
                    │ raw/institutions/*.jsonl   (checkpoint "raw", never modified)              │
  Orchestrator ───► │ tasks/todo/<task>.json     → tasks/claimed/<worker>/<task>.json → done/   │
  (laptop or        │ leases/<task>.json         {worker, expires_at}                            │
   Archil sandbox)  │ results/<fix_id>.json      proposed fix + evidence URLs (write-once)        │
                    │ log/events.log             append-only audit trail                         │
                    │ state/<worker>.json        heartbeat + current step                        │
                    └──────────────▲───────────────▲───────────────▲──────────────────────────────┘
                                   │               │               │
                     Worker pool A: Archil   Worker pool B: E2B   Worker C: laptop
                     sandboxes (fork)        sandbox + archil     (archil mount)
                                   │               │               │
                                   └──── Keenable search + fetch (verification) ────┘
   Review UI (web page) ← reads results/ + log/ via the Archil S3 API → accept fixes → apply on branch "cleaned"
```

### The harness rules (our real engineering)

1. **Stateless workers.** A worker holds nothing in memory that isn't on the disk. A dead worker can be replaced by any machine that mounts the disk.
2. **Claim by atomic rename.** To claim a task, a worker renames `tasks/todo/X.json` to `tasks/claimed/<worker>/X.json`. Archil documents renames as atomic, so two workers can never claim the same task.
3. **Leases with expiry.** The worker writes `leases/X.json` with `expires_at = now + 90s` and refreshes it every 30 seconds. When a lease expires, the orchestrator moves the task back to `todo/`. That's how dead agents' work gets reassigned.
4. **Idempotent fixes.** `fix_id = hash(record_id + check_type)`, and a result is written only if `results/<fix_id>.json` doesn't exist yet. A retried task can never produce a duplicate fix.
5. **Checkpointed steps.** The worker writes its current step (`fetching`, `verifying`, `writing`) to `state/<worker>.json`. On resume, it skips the steps that already finished.
6. **Durable commits.** The FUSE mount buffers recent writes, and Archil's E2B guide says to unmount before killing the sandbox. A hard kill can therefore lose buffered writes.
   - **Fix:** write commit-critical files (results, leases, log) through the **Archil S3 object API** (`put_object`, `append_object`) instead of the mount, or `fsync` them.
   - Confirm the durability guarantees with the Archil engineers on site.
7. **Append-only audit log.** Every claim, verification, fix and reassignment is appended to `log/events.log` with `append_object`, which is limited to 1 MiB per call.
8. **Evidence or nothing.** A fix is only "verified" when it has at least one Keenable URL supporting it. Otherwise it's marked "flagged for human review."

### Teleport flow (the demo moment)

1. Workers run in Archil sandboxes. A worker is mid-task, holding a lease.
2. We **kill** it (`sandbox.stop()`, or kill the E2B sandbox).
3. Its lease expires and the orchestrator returns the task to `todo/`, logging a "reassigned" event.
4. A worker on a **different machine** (an E2B sandbox or a laptop) claims the task, reads `state/`, and resumes.
5. The dashboard shows the same task finishing on new hardware, with 0 duplicates and 0 lost fixes.
6. **Optional:** `sandbox.fork()` to clone a busy worker onto a second machine and split its queue (agent "cell division").

### Branches ("Git for public data")
- After ingest, take the checkpoint `raw`.
- Create the branch `cleaned` from `raw`.
- Accepted fixes are applied only on `cleaned`.
- The review UI shows the diff between `raw` and `cleaned`, with the evidence for every change.

---

## 4. Archil integration (copy-paste starters)

Docs index: https://docs.archil.com/llms.txt

### Python SDK (`pip install "archil[openai-agents]"`)
```python
import archil
archil.configure(api_key="key-...", region="aws-us-east-1")   # or ARCHIL_API_KEY / ARCHIL_REGION

# 1) Archil-managed disk (NOT S3-synced, so branches work)
res = archil.create_disk(name="teleport")
d = res.disk; MOUNT_TOKEN = res.token          # token shown once, save it

# 2) Server-side ingest of a public slice (runs next to the data, not on a laptop)
d.exec("pip install awscli -q && aws s3 cp --no-sign-request --recursive "
       "s3://openalex/data/jsonl/institutions/ /mnt/raw/institutions/")
# check the exact prefix first: aws s3 ls --no-sign-request s3://openalex/data/jsonl/

# 3) Durable writes through the object API
d.put_object("results/abc.json", json.dumps(fix), "application/json")
d.append_object("log/events.log", json.dumps(event) + "\n")    # max 1 MiB per call
d.object_exists("results/abc.json")                             # idempotency check
d.list_objects("tasks/todo/", recursive=True)

# 4) Sandboxes as workers
sb = archil.create_sandbox(name="worker-1", vcpu_count=2, mem_size_mib=4096)
sb.exec("python worker.py --id worker-1")
sb.stop(); sb.start()                          # the kill-and-resume demo
clone = sb.fork(name="worker-1b")              # cell division
archil.list_sandboxes(disk=d.id)

# 5) Agent file tools for OpenAI Agents SDK (read_file, write_file, delete_file, list_files, grep, run_bash)
from agents import Agent
agent = Agent(name="cleaner", tools=d.agent_tools().for_openai_agents())
```

### TypeScript SDK (`npm install disk`, Node 22.19 or later; the package name is `disk`)
```ts
import { Archil } from "disk";
const client = new Archil({ apiKey: process.env.ARCHIL_API_KEY, region: "aws-us-east-1" });
const worker = await client.sandboxes.create({
  name: "worker-1", baseImage: "node:24-bookworm",
  mounts: [{ disk: "dsk-...", path: "/mnt/data" }],
});
await worker.exec("node worker.js");
await worker.pause(); await worker.resume(); await worker.stop(); await worker.start();
const fork = await worker.fork({ name: "worker-1b" });
// fast search over the disk for duplicate detection:
const { matches } = await (await archilDisk).grep({ directory: "raw", pattern: "University of", recursive: true });
```

### Branches and checkpoints (CLI, disk must be mounted)
```bash
sudo archil mount myorg/teleport /mnt/archil --region aws-us-east-1
archil checkpoints create /mnt/archil raw
archil branches --region aws-us-east-1 create dsk-XXXX cleaned --from-checkpoint raw
sudo archil mount myorg/teleport /mnt/cleaned --region aws-us-east-1 --branch cleaned
```

### Mounting the same disk somewhere else (teleport targets)
- **E2B:** build an E2B template that runs the Archil install script as root. Then mount as root with the disk ID, region and mount token. Add **`--conditional`** so several sandboxes can mount at once; a plain mount allows only one. Unmount to flush writes. If egress is restricted, allow TCP ports 8100 and 32050. Guide: https://docs.archil.com/guides/platforms/e2b.md
- **Daytona:** https://docs.archil.com/guides/platforms/daytona.md
- **Laptop or any Linux box:** `sudo archil mount ...` as above.

---

## 5. Keenable integration

- **REST API base:** `https://api.keenable.ai`, with the auth header `X-API-Key`. Credits: code `N1WEB`, $200, about 50,000 calls.
- **MCP endpoint:** `https://api.keenable.ai/mcp`, with the tools `search_web_pages` and `fetch_page_content`. Use it if your agent framework speaks MCP.

```python
import requests, os
H = {"X-API-Key": os.environ["KEENABLE_API_KEY"]}

def search(q):            # ranked results: url, title, description
    return requests.post("https://api.keenable.ai/v1/search", headers=H,
                         json={"query": q}).json()

def fetch(url):           # clean markdown; only indexed URLs are supported
    return requests.get("https://api.keenable.ai/v1/fetch", headers=H,
                        params={"url": url}).json()["content"]
```

**Verification loop for each record:**
1. `search(f"{name} {city} official website")` returns candidate official sites.
2. Compare them against the record's homepage. If they differ or the homepage is dead, propose a fix.
3. `fetch(official_url)` pulls the about or contact page. The LLM checks that the country and type match.
4. Save the result as `{fix_id, record_id, field, old, new, evidence_urls[], confidence, worker, ts}`.

**Notes:**
- The MCP fetch has a `live=true` option for pages Keenable hasn't indexed. Use it when a fetch fails.
- **Optional wow:** use Time Machine (a search parameter that returns results as of a past date) to show when a homepage went dead.

---

## 6. Build timeline (it's ~7:00pm now)

| Time | Milestone | Owner |
|---|---|---|
| 7:00 to 7:20 | Accounts and keys (Archil code `ArchilHackathon26`, Keenable `N1WEB`, LLM key); create the disk; ingest the slice; checkpoint `raw` | A |
| 7:00 to 7:45 | `verify.py`: Keenable search and fetch plus an LLM judge on 20 records, end to end | B |
| 7:00 to 8:00 | Harness: task files, atomic-rename claims, leases, idempotent results, event log | C |
| 7:20 to 8:15 | Orchestrator: split records into tasks, launch N sandbox workers, reassign expired leases | A |
| 7:45 to 8:30 | Dashboard: live counters (tasks done, fixes verified, reassigned, duplicates = 0), worker map by machine, review table with evidence links | D |
| 8:15 to 8:45 | Teleport: second pool (E2B or laptop with `--conditional` mount); kill and resume tested 3 times | C |
| 8:45 to 9:05 | Branch `cleaned` plus the accept-fix flow; the poisoned-record test | A, B |
| 9:05 to 9:25 | Full dry run; record a backup video; README with a one-line-per-track table | All |
| 9:30 | Demos. **Submit before 10:00.** | |

**Rule:** by 8:00 there must be one ugly end-to-end run (claim → verify → result → dashboard). Only after that do we add polish.

---

## 7. 90-second demo script

1. **(0 to 10s) Problem:** "Public datasets are full of silent errors. Research-institution records drive rankings and funding, and thousands are wrong."
2. **(10 to 30s) Launch:** hit Run. 30 agents in Archil sandboxes claim tasks from one shared disk. Counters climb and evidence links appear.
3. **(30 to 50s) Teleport:** "Let's kill half the swarm." Stop 15 sandboxes. Leases expire, tasks move to E2B and laptop workers, and the dashboard shows "reassigned: 15, lost: 0, duplicates: 0."
4. **(50 to 70s) Trust:** click one fix to show the before and after, two Keenable source URLs, and the worker and machine that made it. Then show the poisoned record: "No evidence, so it's flagged, not fixed."
5. **(70 to 90s) Result:** "N records verified and M errors fixed on a branch. The original is untouched, and every change is reviewable like a pull request. Git for public data, on solid ground."

### Likely judge questions

- **"Isn't crash recovery just Archil?"**
  > "Archil keeps the bytes. We make the work safe: leases, atomic claims and idempotent fixes mean a dead agent never loses or duplicates work."
- **"How do you know the fixes are right?"**
  > "Every fix needs web evidence. Anything without it is only flagged. A human accepts fixes onto the branch."
- **"Why not a warehouse?"**
  > "Agents work on files directly. There's no ETL or schema, and the swarm scales by adding sandboxes."
- **"Business?"**
  > "Continuous cleaning of public and private datasets, with an evidence trail. A data-quality team made of agents."

---

## 8. Risks, fallbacks and cut list

| Risk | Fallback |
|---|---|
| The server-side ingest from the public bucket fails (egress or tooling) | Copy a small slice via the laptop into the disk with `put_object`. The demo is unchanged |
| The E2B mount takes too long | Teleport between Archil sandboxes plus a laptop mount only |
| Branch CLI issues | Write fixes to `cleaned/` and diff the files; mention branches as next |
| Keenable fetch fails for unindexed pages | Use MCP fetch with `live=true`, or fall back to search descriptions as evidence |
| Too few real errors found | Pick the slice with the most dead homepages; pre-check 200 records |
| LLM cost or latency | Use a small model for the judge; cap at 2 Keenable calls per record |
| Live demo flakiness | Pre-run once and keep a recorded video plus the finished results on the dashboard |

**Cut order if behind:** cell-division fork → Time Machine → duplicate detection → E2B pool. **Never cut:** kill-and-resume, evidence links, and the counters.

### Submission README: one line per track
- **Most Creative:** a swarm that teleports between machines and treats public data like a Git repo.
- **Best Use of Archil:** a shared disk as the swarm's only state, sandbox fork and stop/start, branches and checkpoints, append-only logs, and the S3 API.
- **Technical Execution:** atomic claims, leases, idempotent fixes, and a measured result of 0 lost and 0 duplicated.
- **Keenable:** search plus fetch evidence for every fix.

---

## Sources

- Archil docs: https://docs.archil.com/llms.txt
- Archil branches and checkpoints: https://docs.archil.com/concepts/branches-and-checkpoints.md
- Archil S3 data sources: https://docs.archil.com/data-sources/s3-object-storage.md
- Archil TypeScript SDK: https://docs.archil.com/sdks/typescript.md
- Archil Python SDK: https://pypi.org/project/archil/
- Archil on E2B: https://docs.archil.com/guides/platforms/e2b.md
- Keenable REST API: https://keenable.ai/docs/api
- Keenable MCP server: https://keenable.ai/docs/mcp
- OpenAlex snapshot: https://help.openalex.org/access/snapshot/
- Hackathon page: https://archil-hackathon.pages.dev/
