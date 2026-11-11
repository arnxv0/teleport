import json, os, subprocess, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "public")
SCENES = [
 ("s01", "Hi! We are students, and this is Teleport. It keeps AI agents working, even when the computer they run on shuts down."),
 ("s02", "AI agents now do long jobs. They read websites, check facts, and fix data for hours. They run on small cloud computers called sandboxes. A sandbox can stop at any moment. Ours shut down after thirty minutes."),
 ("s03", "When a sandbox stops, the agent forgets everything. The work it did is gone. The money we spent on AI calls is wasted. And if we simply start over, the same result can get saved twice."),
 ("s04", "So we asked a simple question. What if the agent kept its memory somewhere safe, outside the computer? Then another computer could pick up right where it stopped. We call this teleporting."),
 ("s05", "Archil makes teleporting possible. Archil gives us one shared disk that every sandbox can open at the same time. Each agent saves its progress to that disk after every step. When a sandbox dies, its hold on the task runs out. Another sandbox notices, reads the saved progress, and keeps going. Archil also runs the sandboxes, and saves a copy of the data before we change anything."),
 ("s06", "Keenable is how our agents check facts. For every row, an agent searches the web with Keenable and reads the real page. A fix only counts if it points to a page Keenable really found. If there is no proof, a person reviews it instead."),
 ("s07", "To show teleporting in action, we built a data cleaning tool. You paste a link to any public dataset. A planning agent decides what to check. Then Teleport starts a team of agents, each one in its own sandbox."),
 ("s08", "Now we turn off a sandbox in the middle of its work. Its task jumps to another sandbox. The new sandbox skips the steps that were already done, and finishes the job."),
 ("s09", "In one test, fifty agents checked every company in the S and P 500. Halfway through, we replaced every single sandbox. All five hundred and three companies still got checked. We lost zero work, and nothing got saved twice."),
 ("s10", "Thank you to Archil for the shared disks and sandboxes, to Keenable for web search with sources, and to Casco for hosting us tonight. This is Teleport. Agents that keep going, even when their computer stops."),
]
for _, t in SCENES:  # house rules for the narration
    assert "—" not in t and "–" not in t and "not only" not in t.lower(), t

KEY = os.environ["OPENAI_API_KEY"]
def tts(item):
    sid, text = item
    body = json.dumps({"model": "gpt-4o-mini-tts", "voice": "alloy", "input": text, "response_format": "mp3",
                       "instructions": "A friendly university student presenting their hackathon project. Warm, clear, relaxed, natural pace, smiling."}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/audio/speech", data=body,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    path = f"{ROOT}/audio/{sid}.mp3"
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        f.write(r.read())
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).decode().strip())
    return {"id": sid, "audio": f"audio/{sid}.mp3", "seconds": round(dur, 2), "text": text}

with ThreadPoolExecutor(10) as pool:
    scenes = list(pool.map(tts, SCENES))
json.dump(scenes, open(f"{ROOT}/scenes.json", "w"), indent=1)
open(os.path.join(HERE, "narration.txt"), "w").write("\n\n".join(t for _, t in SCENES) + "\n")
for s in scenes: print(s["id"], s["seconds"], "s")
print("total", round(sum(s["seconds"] + 0.6 for s in scenes), 1), "s")
