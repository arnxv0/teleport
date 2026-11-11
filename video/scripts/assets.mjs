// Writes public/assets.json: which clips/shots exist and clip durations (seconds).
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
const pub = path.resolve('public');
const out = {clips: {}, files: {}, shots: {}};
const probe = (f) => {
  for (const bin of ['ffprobe', 'node_modules/@remotion/compositor-darwin-arm64/ffprobe']) {
    try {
      const s = execFileSync(bin, ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]).toString().trim();
      const d = parseFloat(s);
      if (d > 0) return d;
    } catch {}
  }
  return null;
};
for (const f of fs.existsSync(path.join(pub, 'clips')) ? fs.readdirSync(path.join(pub, 'clips')) : []) {
  if (!/\.(mp4|webm|mov)$/.test(f)) continue;
  const name = f.replace(/\.\w+$/, '');
  if (out.files[name] && out.files[name].endsWith('.mp4')) continue; // prefer mp4
  const p = path.join(pub, 'clips', f);
  if (fs.statSync(p).size < 1000) continue;
  const d = probe(p);
  if (d) { out.clips[name] = d; out.files[name] = 'clips/' + f; }
}
for (const f of fs.existsSync(path.join(pub, 'shots')) ? fs.readdirSync(path.join(pub, 'shots')) : []) {
  if (/\.(png|jpg|jpeg)$/i.test(f)) out.shots[f.replace(/\.\w+$/, '')] = 'shots/' + f;
}
fs.writeFileSync(path.join(pub, 'assets.json'), JSON.stringify(out, null, 1));
console.log(JSON.stringify(out));
