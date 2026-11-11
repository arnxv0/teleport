import React from 'react';
import {
  AbsoluteFill, Audio, Img, Loop, OffthreadVideo, Sequence, interpolate, spring, staticFile,
  useCurrentFrame, useVideoConfig, Easing,
} from 'remotion';
import {loadFont as loadInter} from '@remotion/google-fonts/Inter';
import {loadFont as loadMono} from '@remotion/google-fonts/JetBrainsMono';
import scenesRaw from '../public/scenes.json';
import assetsRaw from '../public/assets.json';

const {fontFamily: INTER} = loadInter('normal', {weights: ['400', '500', '600', '700'], subsets: ['latin']});
const {fontFamily: MONO} = loadMono('normal', {weights: ['400', '500'], subsets: ['latin']});

export const FPS = 30;
const C = {bg: '#04070D', surface: '#0A0F1A', border: '#172133', text: '#E8EEF7', muted: '#8A97AD', accent: '#5B8DEF'};

type Scene = {id: string; audio: string; seconds: number; text: string};
const scenes: Scene[] = (scenesRaw as Scene[]) ?? [];
const assets = assetsRaw as {clips: Record<string, number>; files: Record<string, string>; shots: Record<string, string>};
const frames = (s: Scene) => Math.round(((s.seconds || 7.4) + 0.6) * FPS);
export const totalFrames = scenes.reduce((a, s) => a + frames(s), 0) || 300;

// ---------- primitives ----------
const useIn = (delay = 0, damping = 200) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  return spring({frame: f - delay, fps, config: {damping}});
};

const FadeUp: React.FC<{delay?: number; children: React.ReactNode; style?: React.CSSProperties}> = ({delay = 0, children, style}) => {
  const p = useIn(delay);
  return <div style={{opacity: p, transform: `translateY(${(1 - p) * 24}px)`, ...style}}>{children}</div>;
};

// Fade whole scene in/out at edges
const SceneFade: React.FC<{dur: number; children: React.ReactNode}> = ({dur, children}) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [0, 10, dur - 10, dur], [0, 1, 1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <AbsoluteFill style={{opacity: o}}>{children}</AbsoluteFill>;
};

const Bg: React.FC = () => (
  <AbsoluteFill style={{background: C.bg}}>
    <AbsoluteFill style={{
      backgroundImage: `radial-gradient(ellipse 60% 50% at 50% 0%, rgba(91,141,239,0.10), transparent 70%)`,
    }} />
    <AbsoluteFill style={{
      backgroundImage: `linear-gradient(${C.border}55 1px, transparent 1px), linear-gradient(90deg, ${C.border}55 1px, transparent 1px)`,
      backgroundSize: '64px 64px', maskImage: 'radial-gradient(ellipse 70% 60% at 50% 40%, black, transparent 80%)',
      WebkitMaskImage: 'radial-gradient(ellipse 70% 60% at 50% 40%, black, transparent 80%)', opacity: 0.5,
    }} />
  </AbsoluteFill>
);

const Caption: React.FC<{children: React.ReactNode; delay?: number; size?: number; top?: number}> = ({children, delay = 6, size = 64, top = 70}) => (
  <div style={{position: 'absolute', top, left: 0, right: 0, display: 'flex', justifyContent: 'center'}}>
    <FadeUp delay={delay}>
      <div style={{fontFamily: INTER, fontSize: size, fontWeight: 600, color: C.text, letterSpacing: -1.5, textAlign: 'center', maxWidth: 1600, lineHeight: 1.15}}>
        {children}
      </div>
    </FadeUp>
  </div>
);

const Eyebrow: React.FC<{children: React.ReactNode}> = ({children}) => (
  <span style={{fontFamily: MONO, fontSize: 22, color: C.accent, letterSpacing: 2, textTransform: 'uppercase'}}>{children}</span>
);

const Box: React.FC<{style?: React.CSSProperties; children?: React.ReactNode}> = ({style, children}) => (
  <div style={{background: C.surface, border: `1px solid ${C.border}`, borderRadius: 16, ...style}}>{children}</div>
);

// ---------- clip in browser frame ----------
// Per-clip overrides: skip to the key moment, faster playback for long recordings
const CLIP_OPTS: Record<string, {start: number; rate: number}> = {kill_teleport: {start: 0, rate: 2}};
const fitClip = (name: string, slot: number) => {
  const d = assets.clips[name];
  if (!d) return null;
  const o = CLIP_OPTS[name];
  if (o && d > o.start + 2 && d * FPS > slot) {
    const clipFrames = Math.floor((d - o.start) * FPS);
    return {clipFrames, rate: o.rate, loop: clipFrames / o.rate < slot - 1, start: Math.floor(o.start * FPS)};
  }
  const clipFrames = Math.floor(d * FPS);
  let rate = 1;
  if (clipFrames > slot * 1.5) rate = 1.5;
  else if (clipFrames > slot) rate = clipFrames / slot;
  else if (clipFrames >= slot * 0.67) rate = clipFrames / slot; // gentle slow-down
  return {clipFrames, rate, loop: clipFrames / rate < slot - 1, start: 0};
};

const BrowserFrame: React.FC<{clip: string; shot?: string; slot: number; label?: string; w?: number; h?: number}> = ({clip, shot, slot, label, w = 1520, h = 855}) => {
  const p = useIn(0);
  const fit = fitClip(clip, slot);
  const shotPath = assets.shots[shot || clip] || assets.shots[clip.split('_')[0]];
  let body: React.ReactNode;
  if (fit) {
    const v = <OffthreadVideo src={staticFile(assets.files[clip])} muted startFrom={fit.start} playbackRate={fit.rate} style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top'}} />;
    body = fit.loop ? <Loop durationInFrames={Math.max(1, Math.floor(fit.clipFrames / fit.rate) - 1)}>{v}</Loop> : v;
  } else if (shotPath) {
    body = <KenBurns src={shotPath} slot={slot} />;
  } else {
    body = <Placeholder label={label || clip} />;
  }
  return (
    <div style={{
      width: w, height: h + 44, borderRadius: 14, overflow: 'hidden', border: `1px solid ${C.border}`, background: C.surface,
      boxShadow: '0 40px 120px rgba(0,0,0,0.6), 0 0 0 1px rgba(91,141,239,0.06)',
      opacity: p, transform: `translateY(${(1 - p) * 30}px) scale(${0.97 + 0.03 * p})`,
    }}>
      <div style={{height: 44, display: 'flex', alignItems: 'center', gap: 8, padding: '0 18px', borderBottom: `1px solid ${C.border}`}}>
        {[0, 1, 2].map((i) => <div key={i} style={{width: 12, height: 12, borderRadius: 6, background: C.border}} />)}
        <div style={{marginLeft: 18, flex: 1, maxWidth: 520, height: 26, borderRadius: 7, background: C.bg, border: `1px solid ${C.border}`,
          fontFamily: MONO, fontSize: 14, color: C.muted, display: 'flex', alignItems: 'center', padding: '0 12px'}}>teleport · localhost</div>
      </div>
      <div style={{width: w, height: h, position: 'relative', overflow: 'hidden', background: C.bg}}>{body}</div>
    </div>
  );
};

const KenBurns: React.FC<{src: string; slot: number}> = ({src, slot}) => {
  const f = useCurrentFrame();
  const s = interpolate(f, [0, slot], [1, 1.06]);
  return <Img src={staticFile(src)} style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top', transform: `scale(${s})`, transformOrigin: '50% 0%'}} />;
};

const Placeholder: React.FC<{label: string}> = ({label}) => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{padding: 40, gap: 18, display: 'flex', flexDirection: 'column'}}>
      <div style={{display: 'flex', gap: 18}}>
        {[0, 1, 2, 3].map((i) => (
          <Box key={i} style={{flex: 1, height: 120, padding: 22}}>
            <div style={{fontFamily: MONO, fontSize: 16, color: C.muted}}>{['agents', 'rows done', 'teleports', 'lost'][i]}</div>
            <div style={{fontFamily: INTER, fontSize: 44, fontWeight: 600, color: i === 2 ? C.accent : C.text}}>
              {[50, Math.min(503, Math.floor(f * 2.2)), Math.floor(f / 40), 0][i]}
            </div>
          </Box>
        ))}
      </div>
      <div style={{display: 'grid', gridTemplateColumns: 'repeat(10, 1fr)', gap: 10, flex: 1}}>
        {Array.from({length: 50}).map((_, i) => {
          const prog = ((f * (0.6 + (i % 7) * 0.13) + i * 9) % 100) / 100;
          return (
            <Box key={i} style={{borderRadius: 10, padding: 10, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end'}}>
              <div style={{height: 6, borderRadius: 3, background: C.border}}>
                <div style={{height: 6, width: `${prog * 100}%`, borderRadius: 3, background: C.accent}} />
              </div>
            </Box>
          );
        })}
      </div>
      <div style={{position: 'absolute', bottom: 16, right: 24, fontFamily: MONO, fontSize: 14, color: C.border}}>{label}</div>
    </AbsoluteFill>
  );
};

// Clip scene with caption on top
const ClipScene: React.FC<{clip: string; caption: string; slot: number}> = ({clip, caption, slot}) => (
  <AbsoluteFill>
    <Caption size={52} top={46}>{caption}</Caption>
    <div style={{position: 'absolute', top: 150, left: 0, right: 0, display: 'flex', justifyContent: 'center'}}>
      <BrowserFrame clip={clip} slot={slot} w={1440} h={810} />
    </div>
  </AbsoluteFill>
);

const TwoClips: React.FC<{dur: number; a: [string, string]; b: [string, string]}> = ({dur, a, b}) => {
  const h = Math.floor(dur / 2);
  return (
    <>
      <Sequence durationInFrames={h}><SceneFade dur={h}><ClipScene clip={a[0]} caption={a[1]} slot={h} /></SceneFade></Sequence>
      <Sequence from={h} durationInFrames={dur - h}><SceneFade dur={dur - h}><ClipScene clip={b[0]} caption={b[1]} slot={dur - h} /></SceneFade></Sequence>
    </>
  );
};

// ---------- scenes ----------
const S01: React.FC<{dur: number}> = () => {
  const p = useIn(4, 100);
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', gap: 36}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 28, opacity: p, transform: `scale(${0.92 + 0.08 * p})`}}>
        <Logo size={110} />
        <div style={{fontFamily: INTER, fontSize: 168, fontWeight: 700, color: C.text, letterSpacing: -6}}>Teleport</div>
      </div>
      <FadeUp delay={24}>
        <div style={{fontFamily: INTER, fontSize: 46, color: C.muted, fontWeight: 500, letterSpacing: -1, textAlign: 'center', maxWidth: 1300, lineHeight: 1.25}}>
          Agents that TELEPORT to a different machine when the current one goes down
        </div>
      </FadeUp>
    </AbsoluteFill>
  );
};

const Logo: React.FC<{size: number}> = ({size}) => (
  <svg width={size} height={size} viewBox="0 0 100 100">
    <circle cx="30" cy="50" r="16" fill="none" stroke={C.muted} strokeWidth="6" />
    <circle cx="70" cy="50" r="16" fill={C.accent} />
    <path d="M 30 26 Q 50 4 70 26" fill="none" stroke={C.accent} strokeWidth="6" strokeLinecap="round" />
  </svg>
);

const S02: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const start = 30, end = dur - 50;
  const t = interpolate(f, [start, end], [1800, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.in(Easing.quad)});
  const mm = String(Math.floor(t / 60)).padStart(2, '0');
  const ss = String(Math.floor(t % 60)).padStart(2, '0');
  return (
    <AbsoluteFill>
      <Caption>Agents run on computers that shut down</Caption>
      <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', gap: 70, paddingTop: 120}}>
        <div style={{fontFamily: MONO, fontSize: 150, fontWeight: 500, color: t < 300 ? '#E5675B' : C.text, letterSpacing: -4}}>{mm}:{ss}</div>
        <div style={{display: 'flex', gap: 28}}>
          {Array.from({length: 6}).map((_, i) => {
            const dieAt = 1800 - (i + 1) * 290;
            const alive = t > dieAt;
            const blink = !alive ? 0 : t < dieAt + 120 ? (Math.floor(f / 4) % 2 ? 0.35 : 1) : 1;
            const appear = spring({frame: f - 8 - i * 4, fps: FPS, config: {damping: 200}});
            return (
              <Box key={i} style={{width: 200, height: 150, padding: 18, opacity: appear * (alive ? blink : 0.18), display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
                borderColor: alive ? C.border : C.border}}>
                <div style={{fontFamily: MONO, fontSize: 18, color: C.muted}}>sandbox {i + 1}</div>
                <div style={{display: 'flex', alignItems: 'center', gap: 10}}>
                  <div style={{width: 12, height: 12, borderRadius: 6, background: alive ? C.accent : C.border}} />
                  <div style={{fontFamily: MONO, fontSize: 18, color: alive ? C.text : C.muted}}>{alive ? 'running' : 'stopped'}</div>
                </div>
              </Box>
            );
          })}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const S03: React.FC<{dur: number}> = ({dur}) => {
  const items = [
    {t: 'Work is lost', d: 'The agent forgets every step', icon: 'M20 50 L80 50 M50 20 L50 80', rot: 45},
    {t: 'Money is wasted', d: 'Paid AI calls are thrown away', icon: 'M50 15 L50 85 M68 30 Q50 18 35 30 Q25 45 50 50 Q75 55 65 70 Q50 82 32 70', rot: 0},
    {t: 'Results get saved twice', d: 'Starting over writes again', icon: 'M25 30 h40 v40 h-40 z M38 18 h40 v40', rot: 0},
  ];
  const step = Math.floor((dur - 60) / 3);
  return (
    <AbsoluteFill>
      <Caption>When a sandbox stops</Caption>
      <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 40, paddingTop: 80}}>
        {items.map((it, i) => (
          <FadeUp key={i} delay={20 + i * step}>
            <Box style={{width: 480, height: 400, padding: 44, display: 'flex', flexDirection: 'column', justifyContent: 'space-between'}}>
              <div style={{width: 84, height: 84, borderRadius: 20, border: `1px solid ${C.border}`, display: 'flex', alignItems: 'center', justifyContent: 'center', background: C.bg}}>
                <svg width="48" height="48" viewBox="0 0 100 100" style={{transform: `rotate(${it.rot}deg)`}}>
                  <path d={it.icon} fill="none" stroke={C.accent} strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <div>
                <div style={{fontFamily: INTER, fontSize: 48, fontWeight: 600, color: C.text, letterSpacing: -1, lineHeight: 1.1}}>{it.t}</div>
                <div style={{fontFamily: INTER, fontSize: 26, color: C.muted, marginTop: 14}}>{it.d}</div>
              </div>
            </Box>
          </FadeUp>
        ))}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const ProgressBar: React.FC<{v: number; w?: number}> = ({v, w = 300}) => (
  <div style={{width: w, height: 10, borderRadius: 5, background: C.border}}>
    <div style={{width: `${Math.max(0, Math.min(1, v)) * 100}%`, height: 10, borderRadius: 5, background: C.accent}} />
  </div>
);

const Machine: React.FC<{name: string; prog: number; x: number; y: number; opacity?: number; children?: React.ReactNode}> = ({name, prog, x, y, opacity = 1, children}) => (
  <Box style={{position: 'absolute', left: x, top: y, width: 380, height: 210, padding: 28, opacity, display: 'flex', flexDirection: 'column', justifyContent: 'space-between'}}>
    <div style={{fontFamily: MONO, fontSize: 22, color: C.muted}}>{name}</div>
    {children}
    <div>
      <div style={{fontFamily: MONO, fontSize: 18, color: C.text, marginBottom: 10}}>step {Math.round(prog * 40)} / 40</div>
      <ProgressBar v={prog} w={322} />
    </div>
  </Box>
);

const Disk: React.FC<{x: number; y: number; w?: number; label?: string; glow?: number}> = ({x, y, w = 300, label = 'Shared disk', glow = 0}) => {
  const h = w * 0.62;
  return (
    <div style={{position: 'absolute', left: x - w / 2, top: y - h / 2, width: w, height: h}}>
      <svg width={w} height={h} viewBox="0 0 300 186" style={{filter: `drop-shadow(0 0 ${glow * 40}px rgba(91,141,239,0.5))`}}>
        {[0, 1, 2].map((i) => {
          const cy = 140 - i * 46;
          return (
            <g key={i}>
              <path d={`M20 ${cy} v-30 a130 26 0 0 1 260 0 v30 a130 26 0 0 1 -260 0z`} fill={C.surface} stroke={i === 2 ? C.accent : C.border} strokeWidth="2" />
              <ellipse cx="150" cy={cy - 30} rx="130" ry="26" fill={C.surface} stroke={i === 2 ? C.accent : C.border} strokeWidth="2" />
            </g>
          );
        })}
      </svg>
      <div style={{position: 'absolute', top: h + 14, left: -100, right: -100, textAlign: 'center', fontFamily: INTER, fontWeight: 600, fontSize: 30, color: C.text}}>{label}</div>
    </div>
  );
};

const S04: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const k = (a: number, b: number) => interpolate(f, [a * dur, b * dur], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic)});
  const progA = 0.55 * k(0.03, 0.3);
  const memDown = k(0.3, 0.45);
  const aFade = 1 - k(0.45, 0.55);
  const arc = k(0.55, 0.72);
  const progB = 0.55 + 0.45 * k(0.72, 0.97);
  const ax = 260, bx = 1280, my = 290;
  const diskX = 960, diskY = 760;
  // memory card path: from A to disk
  const cardX = interpolate(memDown, [0, 1], [ax + 190, diskX]) - 70;
  const cardY = interpolate(memDown, [0, 1], [my + 105, diskY - 40]) - 26;
  return (
    <AbsoluteFill>
      <Caption size={56} top={50}>Keep the memory on a shared disk. We call this TELEPORT.</Caption>
      <Machine name="machine A" prog={progA} x={ax} y={my} opacity={0.15 + 0.85 * aFade} />
      {aFade < 0.6 && <div style={{position: 'absolute', left: ax, top: my - 46, width: 380, textAlign: 'center', fontFamily: MONO, fontSize: 22, color: '#E5675B', opacity: 1 - aFade}}>shut down</div>}
      <Machine name="machine B" prog={arc >= 1 ? progB : 0} x={bx} y={my} opacity={0.35 + 0.65 * arc} />
      <Disk x={diskX} y={diskY} glow={memDown * (1 - arc * 0.5)} />
      {/* arc from disk to B */}
      <svg width={1920} height={1080} style={{position: 'absolute', left: 0, top: 0}}>
        <path d={`M ${diskX} ${diskY - 100} Q ${diskX + 300} ${diskY - 160} ${bx + 190} ${my + 215}`} fill="none" stroke={C.accent} strokeWidth="4" strokeLinecap="round"
          strokeDasharray={900} strokeDashoffset={900 * (1 - arc)} opacity={arc > 0 ? 1 : 0} />
      </svg>
      <div style={{position: 'absolute', left: cardX, top: cardY, width: 140, height: 52, borderRadius: 10, background: C.accent, opacity: memDown < 1 ? (progA > 0.05 ? 1 : 0) : 1 - arc,
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 20, color: C.bg, fontWeight: 500}}>memory</div>
    </AbsoluteFill>
  );
};

const S05Diagram: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const cx = 960, cy = 600;
  const n = 6;
  const pos = Array.from({length: n}).map((_, i) => {
    const a = (Math.PI * 2 * i) / n - Math.PI / 2;
    return {x: cx + Math.cos(a) * 560, y: cy + Math.sin(a) * 300};
  });
  const lbl = (d: number) => spring({frame: f - d, fps: FPS, config: {damping: 200}});
  return (
    <AbsoluteFill>
      <Caption size={56} top={50}>Archil: one shared disk for every sandbox</Caption>
      <svg width={1920} height={1080} style={{position: 'absolute'}}>
        {pos.map((p, i) => {
          const s = lbl(30 + i * 6);
          const dash = (f * 2) % 24;
          return <line key={i} x1={cx} y1={cy} x2={cx + (p.x - cx) * s} y2={cy + (p.y - cy) * s} stroke={C.accent} strokeOpacity={0.5} strokeWidth="2" strokeDasharray="8 16" strokeDashoffset={-dash} />;
        })}
      </svg>
      {pos.map((p, i) => {
        const s = lbl(30 + i * 6);
        const prog = ((f * (0.4 + i * 0.07) + i * 20) % 100) / 100;
        return (
          <Box key={i} style={{position: 'absolute', left: p.x - 110, top: p.y - 50, width: 220, height: 100, padding: 16, opacity: s, transform: `scale(${0.9 + 0.1 * s})`,
            display: 'flex', flexDirection: 'column', justifyContent: 'space-between'}}>
            <div style={{fontFamily: MONO, fontSize: 17, color: C.muted}}>sandbox {i + 1}</div>
            <ProgressBar v={prog} w={186} />
          </Box>
        );
      })}
      <div style={{opacity: lbl(8)}}><Disk x={cx} y={cy - 20} w={260} label="Archil disk" glow={0.5 + 0.3 * Math.sin(f / 15)} /></div>
      {[
        {t: 'Shared disk every sandbox can open', d: 70, x: 960, y: 1000},
        {t: 'Sandboxes', d: 110, x: 230, y: 330},
        {t: 'Saved copy before changes', d: 150, x: 1690, y: 330},
      ].map((l, i) => (
        <div key={i} style={{position: 'absolute', left: l.x - 300, top: l.y, width: 600, textAlign: 'center', opacity: lbl(l.d), fontFamily: INTER, fontSize: 28, color: i === 0 ? C.accent : C.text, fontWeight: 500}}>
          {l.t}
        </div>
      ))}
    </AbsoluteFill>
  );
};

const S05: React.FC<{dur: number}> = ({dur}) => {
  const a = Math.floor(dur * 0.55);
  return (
    <>
      <Sequence durationInFrames={a}><SceneFade dur={a}><S05Diagram dur={a} /></SceneFade></Sequence>
      <Sequence from={a} durationInFrames={dur - a}><SceneFade dur={dur - a}><ClipScene clip="swarm3d" caption="Every agent saves its progress after each step" slot={dur - a} /></SceneFade></Sequence>
    </>
  );
};

const typed = (s: string, f: number, start: number, cps = 1.4) => s.slice(0, Math.max(0, Math.floor((f - start) * cps)));

const S06Search: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const q = 'Ares Management founding year';
  const doneTyping = 10 + q.length / 1.4;
  const results = [
    {u: 'aresmgmt.com/about', t: 'About Ares: founded in 1997 in Los Angeles'},
    {u: 'en.wikipedia.org/wiki/Ares_Management', t: 'Ares Management Corporation, founded 1997'},
    {u: 'sec.gov/cgi-bin/browse-edgar', t: 'Form 10-K: Ares Management, organized 1997'},
  ];
  const fixAt = doneTyping + 20 + results.length * 12 + 10;
  const fx = spring({frame: f - fixAt, fps: FPS, config: {damping: 14, mass: 0.6}});
  return (
    <AbsoluteFill>
      <Caption size={56} top={50}>Keenable: every fix needs a real source</Caption>
      <div style={{position: 'absolute', top: 190, left: 260, width: 1400}}>
        <Box style={{height: 88, display: 'flex', alignItems: 'center', padding: '0 30px', gap: 20, borderRadius: 44}}>
          <svg width="30" height="30" viewBox="0 0 24 24"><circle cx="10" cy="10" r="7" fill="none" stroke={C.muted} strokeWidth="2" /><path d="M15 15 L21 21" stroke={C.muted} strokeWidth="2" strokeLinecap="round" /></svg>
          <div style={{fontFamily: INTER, fontSize: 36, color: C.text}}>{typed(q, f, 10)}<span style={{opacity: Math.floor(f / 15) % 2 ? 0 : 1, color: C.accent}}>|</span></div>
        </Box>
        <div style={{display: 'flex', flexDirection: 'column', gap: 16, marginTop: 28}}>
          {results.map((r, i) => {
            const s = spring({frame: f - doneTyping - 20 - i * 12, fps: FPS, config: {damping: 200}});
            return (
              <Box key={i} style={{padding: '20px 28px', opacity: s, transform: `translateY(${(1 - s) * 20}px)`}}>
                <div style={{fontFamily: MONO, fontSize: 20, color: C.accent}}>{r.u}</div>
                <div style={{fontFamily: INTER, fontSize: 30, color: C.text, marginTop: 6}}>{r.t}</div>
              </Box>
            );
          })}
        </div>
        <div style={{marginTop: 40, display: 'flex', justifyContent: 'center', opacity: Math.min(1, fx), transform: `scale(${0.8 + 0.2 * fx})`}}>
          <Box style={{padding: '22px 40px', display: 'flex', alignItems: 'center', gap: 28, borderColor: C.accent}}>
            <span style={{fontFamily: MONO, fontSize: 26, color: C.muted}}>founded</span>
            <span style={{fontFamily: INTER, fontSize: 48, color: C.muted, textDecoration: 'line-through'}}>1977</span>
            <span style={{fontFamily: INTER, fontSize: 40, color: C.muted}}>→</span>
            <span style={{fontFamily: INTER, fontSize: 48, color: C.text, fontWeight: 700}}>1997</span>
            <div style={{width: 48, height: 48, borderRadius: 24, background: C.accent, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>
              <svg width="28" height="28" viewBox="0 0 24 24"><path d="M5 12 L10 17 L19 7" fill="none" stroke={C.bg} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </div>
          </Box>
        </div>
      </div>
    </AbsoluteFill>
  );
};

const S06: React.FC<{dur: number}> = ({dur}) => {
  const a = Math.floor(dur * 0.55);
  return (
    <>
      <Sequence durationInFrames={a}><SceneFade dur={a}><S06Search dur={a} /></SceneFade></Sequence>
      <Sequence from={a} durationInFrames={dur - a}><SceneFade dur={dur - a}><ClipScene clip="data" caption="No proof? A person reviews it." slot={dur - a} /></SceneFade></Sequence>
    </>
  );
};

const S07: React.FC<{dur: number}> = ({dur}) => (
  <TwoClips dur={dur} a={['overview_paste', 'Paste a link to any dataset']} b={['overview_live', 'A team of agents, each in its own sandbox']} />
);
const S08: React.FC<{dur: number}> = ({dur}) => {
  const a = Math.floor(dur * 0.7);
  return (
    <>
      <Sequence durationInFrames={a}><SceneFade dur={a}><ClipScene clip="kill_teleport" caption="Turn off a sandbox mid-task. The task TELEPORTs." slot={a} /></SceneFade></Sequence>
      <Sequence from={a} durationInFrames={dur - a}><SceneFade dur={dur - a}><ClipScene clip="teleports" caption="Done steps are skipped. The job finishes." slot={dur - a} /></SceneFade></Sequence>
    </>
  );
};

const Counter: React.FC<{to: number; delay: number}> = ({to, delay}) => {
  const f = useCurrentFrame();
  const v = interpolate(f, [delay, delay + 40], [0, to], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.cubic)});
  return <>{Math.round(v)}</>;
};

const S09: React.FC<{dur: number}> = ({dur}) => {
  const step = Math.floor((dur - 90) / 5);
  const stats: {n?: number; unit: string; big?: string}[] = [
    {n: 503, unit: 'companies checked'},
    {n: 50, unit: 'agents'},
    {big: 'Every', unit: 'sandbox replaced mid-run'},
    {n: 0, unit: 'work lost'},
    {n: 0, unit: 'saved twice'},
  ];
  return (
    <AbsoluteFill>
      <Caption size={56} top={50}>The S&amp;P 500 test</Caption>
      <div style={{position: 'absolute', top: 210, left: 140, width: 1640, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 28}}>
        {stats.map((s, i) => (
          <FadeUp key={i} delay={15 + i * step}>
            <Box style={{height: 300, padding: 40, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', gridColumn: undefined}}>
              <div style={{fontFamily: INTER, fontSize: 130, fontWeight: 700, color: i >= 3 ? C.accent : C.text, letterSpacing: -5, lineHeight: 1}}>
                {s.big ?? <Counter to={s.n!} delay={15 + i * step} />}
              </div>
              <div style={{fontFamily: INTER, fontSize: 32, color: C.muted, marginTop: 16}}>{s.unit}</div>
            </Box>
          </FadeUp>
        ))}
        <FadeUp delay={15 + 2 * step}>
          <div style={{height: 302, borderRadius: 16, overflow: 'hidden', border: `1px solid ${C.border}`, background: C.surface, position: 'relative'}}>
            <ThumbClip clip="review" slot={dur} />
          </div>
        </FadeUp>
      </div>
    </AbsoluteFill>
  );
};

const ThumbClip: React.FC<{clip: string; slot: number}> = ({clip, slot}) => {
  const fit = fitClip(clip, slot);
  const shot = assets.shots[clip];
  if (fit) {
    const v = <OffthreadVideo src={staticFile(assets.files[clip])} muted startFrom={fit.start} playbackRate={fit.rate} style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top left'}} />;
    return fit.loop ? <Loop durationInFrames={Math.max(1, Math.floor(fit.clipFrames / fit.rate) - 1)}>{v}</Loop> : v;
  }
  if (shot) return <Img src={staticFile(shot)} style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top left'}} />;
  return (
    <AbsoluteFill style={{padding: 30, gap: 14, display: 'flex', flexDirection: 'column'}}>
      <div style={{fontFamily: MONO, fontSize: 18, color: C.muted}}>review queue</div>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} style={{display: 'flex', alignItems: 'center', gap: 12}}>
          <div style={{width: 14, height: 14, borderRadius: 7, background: i === 2 ? C.muted : C.accent}} />
          <div style={{height: 12, flex: 1, borderRadius: 6, background: C.border}} />
        </div>
      ))}
    </AbsoluteFill>
  );
};

const S10: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const split = Math.floor(dur * 0.62);
  const out = interpolate(f, [split - 12, split], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const sponsors = [
    {n: 'Archil', d: 'Shared disks and sandboxes'},
    {n: 'Keenable', d: 'Web search with sources'},
    {n: 'Casco', d: 'Hosting us tonight'},
  ];
  return (
    <AbsoluteFill>
      {f < split && (
        <AbsoluteFill style={{opacity: out, alignItems: 'center', justifyContent: 'center', gap: 70}}>
          <FadeUp delay={4}><div style={{fontFamily: INTER, fontSize: 90, fontWeight: 700, color: C.text, letterSpacing: -3}}>Thank you</div></FadeUp>
          <div style={{display: 'flex', gap: 36}}>
            {sponsors.map((s, i) => (
              <FadeUp key={i} delay={18 + i * 18}>
                <Box style={{width: 460, padding: 40}}>
                  <div style={{fontFamily: INTER, fontSize: 56, fontWeight: 600, color: C.text, letterSpacing: -1.5}}>{s.n}</div>
                  <div style={{fontFamily: INTER, fontSize: 28, color: C.muted, marginTop: 10}}>{s.d}</div>
                </Box>
              </FadeUp>
            ))}
          </div>
        </AbsoluteFill>
      )}
      {f >= split && (
        <Sequence from={split}>
          <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', gap: 34}}>
            <FadeUp delay={2}>
              <div style={{display: 'flex', alignItems: 'center', gap: 26}}>
                <Logo size={100} />
                <div style={{fontFamily: INTER, fontSize: 150, fontWeight: 700, color: C.text, letterSpacing: -5}}>Teleport</div>
              </div>
            </FadeUp>
            <FadeUp delay={14}><div style={{fontFamily: INTER, fontSize: 42, color: C.muted, textAlign: 'center', maxWidth: 1300, lineHeight: 1.25}}>Agents that TELEPORT to a different machine when the current one goes down</div></FadeUp>
            <FadeUp delay={26}><div style={{fontFamily: MONO, fontSize: 34, color: C.accent, marginTop: 20}}>github.com/arnxv0/teleport</div></FadeUp>
          </AbsoluteFill>
        </Sequence>
      )}
    </AbsoluteFill>
  );
};

const Bubble: React.FC<{w: number; me?: boolean; o?: number}> = ({w, me, o = 1}) => (
  <div style={{alignSelf: me ? 'flex-end' : 'flex-start', width: w, height: 30, borderRadius: 15, opacity: o,
    background: me ? C.accent : C.border}} />
);

const ChatBox: React.FC<{x: number; y: number; name: string; answered: number; opacity?: number; glitch?: number}> = ({x, y, name, answered, opacity = 1, glitch = 0}) => {
  const f = useCurrentFrame();
  const jitter = glitch > 0 ? Math.sin(f * 3.1) * 8 * glitch : 0;
  const shown = Math.max(0, Math.floor(answered));
  return (
    <Box style={{position: 'absolute', left: x + jitter, top: y, width: 420, height: 380, padding: 24, opacity, display: 'flex', flexDirection: 'column', gap: 12, overflow: 'hidden',
      filter: glitch > 0 ? `hue-rotate(${glitch * 90}deg) contrast(${1 + glitch})` : undefined}}>
      <div style={{fontFamily: MONO, fontSize: 20, color: C.muted, marginBottom: 6}}>{name} · chatbot</div>
      {Array.from({length: Math.min(4, shown)}).map((_, i) => (
        <React.Fragment key={i}>
          <Bubble w={150 + ((i + shown) % 3) * 40} />
          <Bubble w={200 + ((i * 7 + shown) % 4) * 25} me />
        </React.Fragment>
      ))}
    </Box>
  );
};

const S09b: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const part = Math.floor(dur * 0.72);
  const k = (a: number, b: number) => interpolate(f, [a * part, b * part], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic)});
  const queue = Math.min(9, Math.floor(f / 10));
  const glitch = k(0.32, 0.42) * (1 - k(0.42, 0.48));
  const offA = 1 - k(0.42, 0.5);
  const drop = k(0.48, 0.62);
  const arc = k(0.62, 0.78);
  const answeredA = Math.min(4, f / 25);
  const answeredB = 4 + Math.max(0, (f - 0.78 * part) / 20);
  const ax = 220, bx = 1280, by = 230;
  const dx = 960, dy = 820;
  const cards = ['Chatbots', 'Research agents', 'Coding agents'];
  const cardsIn = f >= part;
  const fadeDiagram = interpolate(f, [part - 12, part], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill>
      {!cardsIn && (
        <AbsoluteFill style={{opacity: fadeDiagram}}>
          <Caption size={56} top={50}>TELEPORT works for more than data</Caption>
          <ChatBox x={ax} y={by} name="sandbox A" answered={answeredA} opacity={0.15 + 0.85 * offA} glitch={glitch} />
          {offA < 0.7 && <div style={{position: 'absolute', left: ax, top: by - 44, width: 420, textAlign: 'center', fontFamily: MONO, fontSize: 22, color: '#E5675B', opacity: 1 - offA}}>crashed</div>}
          {/* incoming request queue */}
          <div style={{position: 'absolute', left: 700, top: by + 10, width: 180, display: 'flex', flexDirection: 'column', gap: 10, opacity: 1 - drop * (1 - arc)}}>
            <div style={{fontFamily: MONO, fontSize: 18, color: C.muted}}>waiting: {queue * 120}</div>
            {Array.from({length: queue}).map((_, i) => <div key={i} style={{height: 22, width: 120 + (i % 3) * 25, borderRadius: 11, border: `1px solid ${C.accent}`, opacity: 0.4 + 0.06 * i}} />)}
          </div>
          <Disk x={dx} y={dy} w={240} label="Archil disk" glow={drop * (1 - arc * 0.5)} />
          {[{t: 'memory', o: 0}, {t: 'requests', o: 1}].map((c, i) => {
            const cx = interpolate(drop, [0, 1], [ax + 210, dx]) - 65 + (i ? 70 : -70) * (1 - drop);
            const cy = interpolate(drop, [0, 1], [by + 300, dy - 60]) - 22 + i * 50;
            return <div key={i} style={{position: 'absolute', left: cx, top: cy, width: 130, height: 42, borderRadius: 10, background: i ? C.surface : C.accent, border: `1px solid ${C.accent}`,
              opacity: drop > 0 ? 1 - arc : 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 18, color: i ? C.text : C.bg}}>{c.t}</div>;
          })}
          <svg width={1920} height={1080} style={{position: 'absolute', left: 0, top: 0}}>
            <path d={`M ${dx} ${dy - 90} Q ${dx + 300} ${dy - 120} ${bx + 210} ${by + 385}`} fill="none" stroke={C.accent} strokeWidth="4" strokeLinecap="round"
              strokeDasharray={900} strokeDashoffset={900 * (1 - arc)} opacity={arc > 0 ? 1 : 0} />
          </svg>
          <ChatBox x={bx} y={by} name="sandbox B" answered={arc >= 1 ? answeredB : 0} opacity={0.3 + 0.7 * arc} />
          {arc >= 1 && <div style={{position: 'absolute', left: bx, top: by - 44, width: 420, textAlign: 'center', fontFamily: MONO, fontSize: 22, color: C.accent}}>keeps answering</div>}
        </AbsoluteFill>
      )}
      {cardsIn && (
        <Sequence from={part}>
          <Caption size={60} top={200}>Any agent that runs for a long time</Caption>
          <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 36, paddingTop: 120}}>
            {cards.map((c, i) => (
              <FadeUp key={i} delay={10 + i * 10}>
                <Box style={{width: 420, padding: 44}}>
                  <div style={{width: 14, height: 14, borderRadius: 7, background: C.accent, marginBottom: 26}} />
                  <div style={{fontFamily: INTER, fontSize: 48, fontWeight: 600, color: C.text, letterSpacing: -1}}>{c}</div>
                </Box>
              </FadeUp>
            ))}
          </AbsoluteFill>
        </Sequence>
      )}
    </AbsoluteFill>
  );
};

const S08b: React.FC<{dur: number}> = ({dur}) => {
  const f = useCurrentFrame();
  const k = (a: number, b: number) => interpolate(f, [a * dur, b * dur], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic)});
  const n = 12;
  const sx = 1340, sy = 470; // service box center
  const agents = Array.from({length: n}).map((_, i) => ({x: 260 + (i % 3) * 120, y: 250 + Math.floor(i / 3) * 120}));
  const hero = 4, rescuer = 9;
  const tries = Math.min(8, Math.max(0, Math.floor(interpolate(f, [0.12 * dur, 0.45 * dur], [1, 8.99], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}))));
  const drop = k(0.47, 0.55);
  const countT = interpolate(f, [0.55 * dur, 0.75 * dur], [20, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const arc = k(0.75, 0.84);
  const prog = 0.4 + 0.6 * k(0.84, 0.97);
  const dx = 760, dy = 860;
  const h = agents[hero], r = agents[rescuer];
  const cardX = arc > 0 ? interpolate(arc, [0, 1], [dx, r.x]) : interpolate(drop, [0, 1], [h.x, dx]);
  const cardY = arc > 0 ? interpolate(arc, [0, 1], [dy - 70, r.y + 56]) - Math.sin(arc * Math.PI) * 220 : interpolate(drop, [0, 1], [h.y + 56, dy - 70]);
  const busy = f > 0.08 * dur && f < 0.5 * dur;
  return (
    <AbsoluteFill>
      <Caption size={56} top={50}>Overloaded? The task TELEPORTs to a free agent.</Caption>
      <svg width={1920} height={1080} style={{position: 'absolute'}}>
        {agents.map((a, i) => {
          const live = busy && (i !== hero || drop === 0);
          const off = ((f * 3 + i * 17) % 60) / 60;
          return live ? <line key={i} x1={a.x + 30} y1={a.y} x2={sx - 220} y2={sy} stroke={C.accent} strokeOpacity={0.35} strokeWidth="2" strokeDasharray="6 10" strokeDashoffset={-off * 32} /> : null;
        })}
      </svg>
      {agents.map((a, i) => {
        const gone = i === hero && drop > 0;
        const isR = i === rescuer && arc > 0;
        return (
          <div key={i} style={{position: 'absolute', left: a.x - 24, top: a.y - 24, width: 48, height: 48, borderRadius: 24,
            background: isR ? C.accent : C.surface, border: `2px solid ${i === hero ? (gone ? '#E5675B' : C.text) : isR ? C.accent : C.border}`, opacity: gone ? 0.5 : 1}} />
        );
      })}
      {/* retry counter */}
      {tries > 0 && <div style={{position: 'absolute', left: h.x - 120, top: h.y - 70, width: 240, textAlign: 'center', fontFamily: MONO, fontSize: 22, color: tries >= 8 ? '#E5675B' : C.text,
        opacity: 1 - k(0.55, 0.6)}}>Try {tries} of 8</div>}
      {/* service box */}
      <Box style={{position: 'absolute', left: sx - 220, top: sy - 110, width: 440, height: 220, padding: 30, display: 'flex', flexDirection: 'column', justifyContent: 'space-between'}}>
        <div style={{fontFamily: INTER, fontSize: 34, fontWeight: 600, color: C.text}}>Search service</div>
        <div style={{fontFamily: MONO, fontSize: 20, color: C.muted}}>50 agents asking at once</div>
      </Box>
      {busy && [0, 1, 2].map((i) => {
        const t = ((f + i * 20) % 60) / 60;
        return <div key={i} style={{position: 'absolute', left: sx - 260 - t * 300, top: sy + 140 + i * 56, padding: '8px 18px', borderRadius: 18, background: C.surface, border: `1px solid #E5675B`,
          fontFamily: INTER, fontSize: 22, color: C.text, opacity: Math.sin(t * Math.PI)}}>Slow down</div>;
      })}
      <Disk x={dx} y={dy} w={200} label="Archil disk" glow={drop * (1 - arc)} />
      {drop > 0 && f < 0.75 * dur && (
        <div style={{position: 'absolute', left: dx + 150, top: dy - 60, fontFamily: MONO, fontSize: 26, color: C.muted}}>
          task free in <span style={{color: C.text}}>{Math.ceil(countT)}s</span>
        </div>
      )}
      {(drop > 0 || f < 0.47 * dur) && arc < 1 && (
        <div style={{position: 'absolute', left: cardX - 50, top: cardY - 16, width: 100, height: 32, borderRadius: 8, background: C.accent, fontFamily: MONO, fontSize: 16, color: C.bg,
          display: 'flex', alignItems: 'center', justifyContent: 'center'}}>task</div>
      )}
      {arc >= 1 && (
        <div style={{position: 'absolute', left: r.x + 40, top: r.y - 14, display: 'flex', alignItems: 'center', gap: 14}}>
          <ProgressBar v={prog} w={220} />
          <span style={{fontFamily: MONO, fontSize: 20, color: prog >= 1 ? C.accent : C.text}}>{prog >= 1 ? 'done' : `${Math.round(prog * 100)}%`}</span>
        </div>
      )}
      <div style={{position: 'absolute', bottom: 34, right: 60, fontFamily: MONO, fontSize: 20, color: C.muted}}>Real event from tonight's run</div>
    </AbsoluteFill>
  );
};

const SCENES: Record<string, React.FC<{dur: number}>> = {s01: S01, s02: S02, s03: S03, s04: S04, s05: S05, s06: S06, s07: S07, s08: S08, s08b: S08b, s09: S09, s09b: S09b, s10: S10};
const SELF_FADING = new Set(['s05', 's06', 's07', 's08']);

export const Video: React.FC = () => {
  let from = 0;
  return (
    <AbsoluteFill style={{background: C.bg, fontFamily: INTER}}>
      <Bg />
      {scenes.map((s) => {
        const dur = frames(s);
        const Comp = SCENES[s.id];
        const el = (
          <Sequence key={s.id} from={from} durationInFrames={dur} name={s.id}>
            <Audio src={staticFile(s.audio)} />
            {Comp ? (SELF_FADING.has(s.id) ? <Comp dur={dur} /> : <SceneFade dur={dur}><Comp dur={dur} /></SceneFade>) : null}
          </Sequence>
        );
        from += dur;
        return el;
      })}
    </AbsoluteFill>
  );
};
