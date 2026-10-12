// ─────────────────────────────────────────────────────────────────────────────
//  THE 13 SHIFTS — kinetic motion piece for LinkedIn (1080×1080)
//  Depends on the animations.jsx engine prepended in shifts-video.jsx:
//  Stage, Sprite, useSprite, useTime, useTimeline, Easing, clamp.
// ─────────────────────────────────────────────────────────────────────────────

const FONTS = {
  display: "'Archivo', sans-serif",
  serif:   "'Source Serif 4', Georgia, serif",
  mono:    "'IBM Plex Mono', monospace",
};
const C = {
  paper:'#f3efe4', ink:'#1b1a16', green:'#2c5a3c',
  muted:'#6f6d64', body:'#3a3a32', hair:'#dcd7c9',
};

const SHIFTS = [
  { n:'01', t:'Offline-First', tFlat:'Offline-First',
    old:'Connection is normal. Disconnection is an error.',
    neu:'Offline is the default.',
    line:'Every node runs fully standalone. Connectivity is a bonus that comes and goes.' },
  { n:'02', t:'Transport\nAbstraction', tFlat:'Transport Abstraction',
    old:'Pick a transport. Build your protocol for it.',
    neu:'The radio is just a pipe.',
    line:'The same code runs over fiber, Wi-Fi, LoRa, or satellite — and never knows the difference.' },
  { n:'03', t:'Sovereign\nNetworking', tFlat:'Sovereign Networking',
    old:'You’re a tenant in someone else’s infrastructure.',
    neu:'You are the infrastructure.',
    line:'No ISP, no cloud, no authority in the chain. The network and the data are yours.' },
  { n:'04', t:'Emergent\nInternet', tFlat:'Emergent Internet',
    old:'The network is designed and administered.',
    neu:'The network assembles itself.',
    line:'Nodes find each other — even across the open Internet — and form one private, coherent network, with nobody in charge.' },
  { n:'05', t:'Living\nTopology', tFlat:'Living Topology',
    old:'A topology you design, document, and maintain.',
    neu:'Derived from reality, every time it changes.',
    line:'Move a node from one network to another without ever touching the node.' },
  { n:'06', t:'Ground Truth,\nNot DNS', tFlat:'Ground Truth, Not DNS',
    old:'Trust a central nameserver to say who’s who.',
    neu:'Local ground truth, always current.',
    line:'Each node knows exactly what it can reach — no zones, no TTLs, nothing stale.' },
  { n:'07', t:'Proximity,\nNot Perimeter', tFlat:'Proximity, Not Perimeter',
    old:'Build walls around a hostile perimeter.',
    neu:'Physical proximity is the perimeter.',
    line:'You can’t attack what you can’t find — the Internet-facing surface is one specific grain of sand on a very large beach.' },
  { n:'08', t:'The Network\nRemembers', tFlat:'The Network Remembers',
    old:'Every request starts over from zero.',
    neu:'The network carries the context.',
    line:'It learns the shape of your traffic, so only what actually changed crosses the wire.' },
  { n:'09', t:'Infrastructure\nCompression', tFlat:'Infrastructure Compression',
    old:'Compression is something an app opts into.',
    neu:'The network optimizes everything, automatically.',
    line:'Automatic, transparent, zero app changes — 93.8% less traffic on real workloads.' },
  { n:'10', t:'Parallel,\nNot Serial', tFlat:'Parallel, Not Serial',
    old:'Send. Wait. Send. Wait. Dead air.',
    neu:'256 in flight. The wire never idles.',
    line:'44× the throughput on the very same link — full utilization, not faster hardware.' },
  { n:'11', t:'Network as\nDatabase', tFlat:'Network as Database',
    old:'Data lives behind the network.',
    neu:'Data lives in the network.',
    line:'A network-wide shared data space that everyone can see and access simultaneously.' },
  { n:'12', t:'Networked\nIntelligence', tFlat:'Networked Intelligence',
    old:'AI in the cloud, sensors at the edge, a fragile pipe between.',
    neu:'Intelligence on the mesh, one shared world.',
    line:'Offline-capable, task-specific AI sees every sensor and every actuator in real time — and keeps working with no internet.' },
  { n:'13', t:'Memory,\nNot Messages', tFlat:'Memory, Not Messages',
    old:'Send and receive. Manage the exchange.',
    neu:'Read and write one shared memory.',
    line:'The keystone: shared memory that converges — facilitating a new, network-optimized programming model called UnREST.' },
];

// entrance helper: eased 0→1 starting at `at`, over `dur`
const appear = (lt, at, dur = 0.5, ease = Easing.easeOutCubic) =>
  ease(clamp((lt - at) / dur, 0, 1));

// scene-level fade in/out wrapper alpha
function sceneAlpha(lt, dur) {
  const inA  = clamp(lt / 0.5, 0, 1);
  const outA = 1 - clamp((lt - (dur - 0.7)) / 0.7, 0, 1);
  return Math.min(inA, outA);
}

// ── Persistent chrome: wordmark + global progress bar ───────────────────────
function Chrome() {
  const time = useTime();
  const { duration } = useTimeline();
  const p = clamp(time / duration, 0, 1);
  // wordmark only during the shift scenes + recap (hidden on intro & CTA)
  const wm = (time > 8.6 && time < 179) ? 1 : 0;
  return (
    <div style={{ position:'absolute', inset:0, pointerEvents:'none' }}>
      <div style={{ position:'absolute', right:110, top:150, opacity:wm, transition:'opacity 0.3s',
        fontFamily:FONTS.mono, fontSize:22, letterSpacing:'0.16em', color:C.muted }}>FROGNET</div>
      <div style={{ position:'absolute', left:110, right:110, bottom:46, height:4,
        background:C.hair, borderRadius:2 }}>
        <div style={{ position:'absolute', left:0, top:0, bottom:0, width:`${p*100}%`,
          background:C.green, borderRadius:2 }} />
      </div>
    </div>
  );
}

// ── A single shift scene ─────────────────────────────────────────────────────
function ShiftScene({ d }) {
  const { localTime: lt, duration: dur } = useSprite();
  const a = sceneAlpha(lt, dur);

  const labelA = appear(lt, 0.15, 0.4);
  const titleA = appear(lt, 0.30, 0.5);
  const oldA   = appear(lt, 1.1, 0.5);
  const strike = clamp((lt - 2.7) / 0.8, 0, 1);
  const oldDim = 1 - 0.5 * clamp((lt - 3.5) / 0.6, 0, 1);
  const arrowA = appear(lt, 3.2, 0.5);
  const newA   = appear(lt, 3.7, 0.55, Easing.easeOutBack);
  const sentA  = appear(lt, 4.7, 0.6);
  const drift  = Math.sin(lt * 0.5) * 7;

  return (
    <div style={{ position:'absolute', inset:0, opacity:a }}>
      {/* giant watermark number */}
      <div style={{ position:'absolute', right:-24, top:118 + drift, fontFamily:FONTS.display,
        fontWeight:800, fontSize:470, lineHeight:0.8, letterSpacing:'-0.04em',
        color:'rgba(44,90,60,0.06)' }}>{d.n}</div>

      {/* label */}
      <div style={{ position:'absolute', left:110, top:150, opacity:labelA, fontFamily:FONTS.mono,
        fontSize:30, letterSpacing:'0.18em', color:C.green }}>SHIFT {d.n}</div>

      {/* title */}
      <div style={{ position:'absolute', left:110, top:200, width:860, opacity:titleA,
        transform:`translateY(${(1-titleA)*16}px)`, fontFamily:FONTS.display, fontWeight:800,
        fontSize:78, lineHeight:0.98, letterSpacing:'-0.03em', color:C.ink,
        whiteSpace:'pre-line' }}>{d.t}</div>

      {/* OLD WAY */}
      <div style={{ position:'absolute', left:110, top:452, opacity:oldA, fontFamily:FONTS.mono,
        fontSize:22, letterSpacing:'0.14em', color:C.muted }}>THE OLD WAY</div>
      <div style={{ position:'absolute', left:110, top:486, width:905, opacity:oldA }}>
        <span style={{ position:'relative', display:'inline-block', fontFamily:FONTS.serif,
          fontStyle:'italic', fontSize:34, lineHeight:1.2, color:C.muted, opacity:oldDim }}>
          {d.old}
          <span style={{ position:'absolute', left:0, top:'54%', height:3,
            width:`${strike*100}%`, background:C.green }} />
        </span>
      </div>

      {/* arrow */}
      <div style={{ position:'absolute', left:112, top:566, opacity:arrowA, fontFamily:FONTS.display,
        fontWeight:700, fontSize:30, color:C.green }}>↓</div>

      {/* FROGNET / new */}
      <div style={{ position:'absolute', left:110, top:614, opacity:newA, fontFamily:FONTS.mono,
        fontSize:22, letterSpacing:'0.14em', color:C.green }}>FROGNET</div>
      <div style={{ position:'absolute', left:110, top:648, width:880, opacity:newA,
        transform:`translateY(${(1-newA)*18}px)`, fontFamily:FONTS.display, fontWeight:700,
        fontSize:56, lineHeight:1.02, letterSpacing:'-0.02em', color:C.ink }}>{d.neu}</div>

      {/* supporting sentence */}
      <div style={{ position:'absolute', left:110, top:838, width:885, opacity:sentA,
        transform:`translateY(${(1-sentA)*12}px)`, fontFamily:FONTS.serif, fontSize:31,
        lineHeight:1.34, color:C.body }}>{d.line}</div>
    </div>
  );
}

// ── Intro ────────────────────────────────────────────────────────────────────
function Intro() {
  const { localTime: lt, duration: dur } = useSprite();
  const a = sceneAlpha(lt, dur);
  const ebA  = appear(lt, 0.2, 0.5);
  const l1A  = appear(lt, 0.7, 0.6);
  const l2A  = appear(lt, 1.2, 0.6);
  const l3A  = appear(lt, 1.7, 0.5);
  const yrA  = appear(lt, 2.3, 0.6, Easing.easeOutBack);
  const subA = appear(lt, 3.4, 0.6);
  return (
    <div style={{ position:'absolute', inset:0, opacity:a, display:'flex', flexDirection:'column',
      justifyContent:'center', padding:'0 96px' }}>
      <div style={{ opacity:ebA, fontFamily:FONTS.mono, fontSize:25, letterSpacing:'0.14em',
        color:C.green, marginBottom:42 }}>FAWCETT INNOVATIONS · THE FROGNET LIVING NETWORK</div>
      <div style={{ fontFamily:FONTS.display, fontWeight:800, fontSize:80, lineHeight:1.04,
        letterSpacing:'-0.03em', color:C.ink }}>
        <div style={{ opacity:l1A, transform:`translateY(${(1-l1A)*18}px)` }}>Everything you know</div>
        <div style={{ opacity:l2A, transform:`translateY(${(1-l2A)*18}px)` }}>about networks</div>
        <div style={{ opacity:l3A }}>assumes it’s&nbsp;
          <span style={{ display:'inline-block', opacity:yrA, transform:`scale(${0.7+0.3*yrA})`,
            transformOrigin:'left center', color:C.green }}>1969.</span>
        </div>
      </div>
      <div style={{ opacity:subA, marginTop:50, fontFamily:FONTS.serif, fontSize:36, lineHeight:1.3,
        color:C.body, maxWidth:840 }}>Thirteen shifts that redefine networking for modern computing.</div>
    </div>
  );
}

// ── Recap grid ───────────────────────────────────────────────────────────────
function Recap() {
  const { localTime: lt, duration: dur } = useSprite();
  const a = sceneAlpha(lt, dur);
  const hA = appear(lt, 0.2, 0.5);
  return (
    <div style={{ position:'absolute', inset:0, opacity:a, padding:'120px 110px 0' }}>
      <div style={{ opacity:hA, fontFamily:FONTS.display, fontWeight:800, fontSize:62,
        letterSpacing:'-0.03em', color:C.ink, marginBottom:44 }}>Thirteen shifts.</div>
      <div style={{ display:'flex', flexDirection:'column', gap:13 }}>
        {SHIFTS.map((d, i) => {
          const itA = appear(lt, 0.6 + i * 0.07, 0.45);
          return (
            <div key={i} style={{ opacity:itA, transform:`translateX(${(1-itA)*-16}px)`,
              display:'flex', gap:22, alignItems:'baseline' }}>
              <span style={{ fontFamily:FONTS.mono, fontSize:23, color:C.green, flex:'none',
                width:34 }}>{d.n}</span>
              <span style={{ fontFamily:FONTS.display, fontWeight:600, fontSize:33, color:C.ink,
                lineHeight:1.0, letterSpacing:'-0.01em', whiteSpace:'nowrap' }}>{d.tFlat}</span>
            </div>
          );
        })}
      </div>
      <div style={{ opacity:appear(lt, 1.7, 0.7), marginTop:42, paddingTop:30,
        borderTop:`1.5px solid ${C.hair}`, fontFamily:FONTS.display, fontWeight:700, fontSize:34,
        letterSpacing:'-0.01em', color:C.ink }}>FrogNet: &nbsp;<span style={{ color:C.green }}>Networking for the 21st century.</span></div>
      <div style={{ opacity:appear(lt, 2.1, 0.7), marginTop:16, fontFamily:FONTS.serif,
        fontSize:29, lineHeight:1.3, color:C.body }}>Contact us for access to the code today &mdash; <span style={{ color:C.ink, fontWeight:600 }}>before we open-source it.</span></div>
    </div>
  );
}

// ── Closing CTA ──────────────────────────────────────────────────────────────
function CTA() {
  const { localTime: lt, duration: dur } = useSprite();
  const a = sceneAlpha(lt, dur);
  const l1A  = appear(lt, 0.3, 0.5);
  const repA = appear(lt, 0.9, 0.6, Easing.easeOutBack);
  const logoA = appear(lt, 2.0, 0.7);
  const subA = appear(lt, 3.0, 0.6);
  const cA   = appear(lt, 4.0, 0.6);
  return (
    <div style={{ position:'absolute', inset:0, opacity:a, display:'flex', flexDirection:'column',
      justifyContent:'center', padding:'0 110px' }}>
      <div style={{ fontFamily:FONTS.display, fontWeight:800, fontSize:82, lineHeight:1.0,
        letterSpacing:'-0.03em', color:C.ink }}>
        <span style={{ opacity:l1A }}>Thirteen assumptions. </span>
        <span style={{ display:'inline-block', opacity:repA, transform:`scale(${0.7+0.3*repA})`,
          transformOrigin:'left center', color:C.green }}>Replaced.</span>
      </div>
      <div style={{ opacity:logoA, marginTop:64, display:'flex', alignItems:'center', gap:34 }}>
        <img src="assets/frognet-logo.png" alt="FrogNet" style={{ width:150, height:150,
          objectFit:'contain' }} />
        <div>
          <div style={{ fontFamily:FONTS.display, fontWeight:800, fontSize:92,
            letterSpacing:'-0.04em', color:C.green, lineHeight:0.9 }}>FrogNet</div>
          <div style={{ opacity:subA, marginTop:14, fontFamily:FONTS.serif, fontSize:36,
            color:C.body }}>Networking for the 21st century.</div>
        </div>
      </div>
      <div style={{ opacity:cA, marginTop:64, paddingTop:34, borderTop:`1.5px solid ${C.ink}`,
        display:'flex', flexDirection:'column', gap:12 }}>
        <div style={{ fontFamily:FONTS.display, fontWeight:600, fontSize:30, color:C.ink }}>Fawcett Innovations LLC</div>
        <div style={{ fontFamily:FONTS.mono, fontSize:25, letterSpacing:'0.04em', color:C.muted }}>john@fawcettinnovations.com &nbsp;·&nbsp; (206) 335-9639</div>
      </div>
    </div>
  );
}

// ── Schedule + root ──────────────────────────────────────────────────────────
const INTRO_S = 0,  INTRO_E = 9.5;
const FIRST = 9.0, STEP = 12.0, SDUR = 12.8;
const shiftStart = (i) => FIRST + i * STEP;
const RECAP_S = 165.0, RECAP_E = 180.0;
const CTA_S = 179.5, CTA_E = 191.0;
const DURATION = 190.5;

function ShiftsVideo() {
  return (
    <Stage width={1080} height={1080} duration={DURATION} background={C.paper}
      persistKey="shifts13" loop={true} autoplay={true}>
      <Chrome />
      <Sprite start={INTRO_S} end={INTRO_E}><Intro /></Sprite>
      {SHIFTS.map((d, i) => (
        <Sprite key={i} start={shiftStart(i)} end={shiftStart(i) + SDUR}>
          <ShiftScene d={d} />
        </Sprite>
      ))}
      <Sprite start={RECAP_S} end={RECAP_E}><Recap /></Sprite>
      <Sprite start={CTA_S} end={CTA_E}><CTA /></Sprite>
    </Stage>
  );
}

Object.assign(window, { ShiftsVideo });
