import React, { useState, useEffect, useRef } from "react";

/* =============================================================================
   FrogNet Communicator — single phone-sized tabbed surface.

   This is the LAYOUT + PRESENTATION LOGIC, driven by real state. It implements the
   model worked out with John:

   - Tabs ARE the bundle launcher. Home (Services) is the landing tab; Phone/Call and
     Text are the communication surface; Games/Calendar (and other hubs) appear when
     present.
   - Phone -> people selector AND a list of Joinable streams. Joinability lives in the
     stream's own tuples (watchable / joinable / group), set by originator/admin; the
     list is a filtered read of the space.
   - ONE uplink per participant; the join choice is what goes ON it: Watch (nothing),
     Audio Only, or Full A/V. Server mixes audio = sum-of-others, video = grid-of-others
     thumbnails (minus yourself), personalized per recipient, output as multi-rung
     channels for downlink scaling.
   - Downlink presentation is PER PLANE and composed locally from three things each:
       possible (link can carry it)  -  enabled (user wants it)  -  ladder level.
     Video region shows: the grid  /  "No Video" (enabled but impossible)  /
       "Video Off" (possible but disabled)  /  black (master display off)  /
       "Text Only" (both planes impossible).
     Audio indicator shows: Stereo / Mono (ladder) / Off (user) / None (impossible).
   - Self-view is a LOCAL display control: Full / Half / Thumbnail / Off — smaller gives
     the grid more room. Never touches the uplink or the server.
   - PTT and spoken-text-output are LOCAL controls.
   - Text is ONE convergent `conversation` tuple: a JSON array of messages. Sending
     appends; reading is a refresh that's SAME until it grows, then a DIFF of the new
     message. Rendered in the Text tab and the call's chat strip from the same tuple.

   Streams/codec/mix are wired after; here the video regions are driven placeholders so
   every control and every presentation state is real and drivable.
   ============================================================================= */

// --- design tokens: a calm, instrument-panel identity. Pond at night: deep teal-black,
// lily-pad green as the single live accent, warm bone text. Not a default cream/acid look.
const C = {
  bg: "#0c1413",          // pond-bottom near-black teal
  panel: "#12201d",       // raised panel
  panelHi: "#18302b",     // hover/active panel
  line: "#23403a",        // hairline
  ink: "#e8e6dc",         // bone text
  inkDim: "#8fa39c",      // muted
  pad: "#4ade80",         // lily-pad green — the one live accent
  padDim: "#2f5f47",
  warn: "#e8b04b",        // amber for degraded
  bad: "#d96a5b",         // muted red for impossible
};

const MONO = "'Courier New', ui-monospace, monospace";
const SANS = "'Inter', system-ui, -apple-system, sans-serif";

// ---- mock shared space: LiveStream instances (what a real get_all would return) ----
const INITIAL_STREAMS = [
  { who: "Dan", session: "ny-watch-1", kind: "watchable", participants: 1, subtitle: "Queens HaLow field" },
  { who: "Seattle Standup", session: "sea-grp-7", kind: "joinable", group: true, participants: 4, subtitle: "group call" },
  { who: "John", session: "demo-2", kind: "joinable", group: false, participants: 1, subtitle: "1:1" },
];

const PEOPLE = [
  { name: "Julie", node: "10.130.130", status: "online" },
  { name: "Dan", node: "10.102.60", status: "online" },
  { name: "John Bulldis", node: "10.28.28", status: "away" },
  { name: "Pete", node: "10.250.250", status: "offline" },
];

// other participants in the current call (the server's grid-of-others, minus you)
const OTHERS = [
  { name: "Julie", speaking: true },
  { name: "Dan", speaking: false },
  { name: "Bulldis", speaking: false },
];

const TABS_BASE = ["Home", "Call", "Text"];
const TABS_BUNDLES = ["Games", "Calendar"]; // appear "when present"

export default function Communicator() {
  const [tab, setTab] = useState("Home");

  // ---- per-plane downlink state (composed at render) ----
  const [videoPossible, setVideoPossible] = useState(true);
  const [audioPossible, setAudioPossible] = useState(true);
  const [videoEnabled, setVideoEnabled] = useState(true);
  const [audioEnabled, setAudioEnabled] = useState(true);
  const [videoRung, setVideoRung] = useState(2);      // 0..3  (150/300/600/1200)
  const [audioRung, setAudioRung] = useState(1);      // 0=mono 1=stereo
  const [displayOn, setDisplayOn] = useState(true);   // master display gate

  // ---- self-view local control: Full / Half / Thumb / Off ----
  const [selfView, setSelfView] = useState("Half");

  // ---- uplink payload (the one uplink): "watch" | "audio" | "av" ----
  const [uplink, setUplink] = useState("av");
  const [ptt, setPtt] = useState(false);              // local: gates uplink audio
  const [speakText, setSpeakText] = useState(false);  // local: TTS incoming

  // ---- conversation tuple: JSON array of messages (SAME/DIFF on refresh) ----
  const [conversation, setConversation] = useState([
    { from: "Julie", t: "can donna see the wedding stream?", at: "9:02" },
    { from: "you", t: "patching her in now", at: "9:02" },
    { from: "Dan", t: "starlink node converged, survived a reboot", at: "9:05" },
  ]);
  const [draft, setDraft] = useState("");

  // ---- joinable list read from the space ----
  const [streams] = useState(INITIAL_STREAMS);
  const [joinTarget, setJoinTarget] = useState(null); // {who, kind} being joined

  const RUNGS = ["150k 160×120", "300k 320×240", "600k 320×240", "1200k 640×480"];

  function sendMessage() {
    if (!draft.trim()) return;
    // append to the conversation tuple (the DIFF is just this element)
    setConversation((c) => [...c, { from: "you", t: draft.trim(), at: "now" }]);
    setDraft("");
  }

  const activeTabs = [...TABS_BASE, ...TABS_BUNDLES];

  return (
    <div style={{ minHeight: "100vh", background: "#05090a", display: "flex",
      justifyContent: "center", fontFamily: SANS }}>
      {/* phone frame */}
      <div style={{ width: 390, minHeight: 780, maxHeight: 844, background: C.bg,
        display: "flex", flexDirection: "column", boxShadow: "0 0 0 8px #000, 0 20px 60px rgba(0,0,0,.6)",
        position: "relative", overflow: "hidden" }}>

        {/* header */}
        <div style={{ padding: "14px 16px 10px", borderBottom: `1px solid ${C.line}`,
          display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 10, height: 10, borderRadius: 6, background: C.pad,
            boxShadow: `0 0 10px ${C.pad}` }} />
          <div style={{ fontFamily: MONO, fontSize: 13, letterSpacing: 1, color: C.ink }}>
            FrogNet <span style={{ color: C.inkDim }}>Communicator</span>
          </div>
          <div style={{ marginLeft: "auto", fontFamily: MONO, fontSize: 11, color: C.inkDim }}>
            {tab === "Call" ? "demo-2" : "pond"}
          </div>
        </div>

        {/* body */}
        <div style={{ flex: 1, overflow: "auto" }}>
          {tab === "Home" && <Home onPick={setTab} />}
          {tab === "Call" && (
            <Call
              {...{ videoPossible, audioPossible, videoEnabled, audioEnabled,
                videoRung, audioRung, displayOn, selfView, uplink, ptt,
                conversation, RUNGS,
                setVideoEnabled, setAudioEnabled, setDisplayOn, setSelfView,
                setUplink, setPtt, setVideoRung, setAudioRung,
                setVideoPossible, setAudioPossible }}
            />
          )}
          {tab === "Text" && (
            <Text {...{ conversation, draft, setDraft, sendMessage, ptt, setPtt,
              speakText, setSpeakText }} />
          )}
          {tab === "Games" && <Bundle name="Games" note="UnREST game hub — Hearts, chess, backgammon. Tab present because the bundle is installed." />}
          {tab === "Calendar" && <Bundle name="Calendar" note="Shared calendar bundle. Convergent records; edits are lossless-eventual." />}
        </div>

        {/* tab bar = the bundle launcher */}
        <div style={{ display: "flex", borderTop: `1px solid ${C.line}`, background: C.panel }}>
          {activeTabs.map((t) => (
            <button key={t} onClick={() => setTab(t)}
              style={{ flex: 1, padding: "10px 4px 12px", background: "none", border: "none",
                cursor: "pointer", borderTop: tab === t ? `2px solid ${C.pad}` : "2px solid transparent",
                color: tab === t ? C.pad : C.inkDim, fontFamily: MONO, fontSize: 11,
                letterSpacing: 0.5 }}>
              {t}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ----------------------------- Home / Services ----------------------------- */
function Home({ onPick }) {
  const services = [
    { name: "Phone", to: "Call", desc: "Call someone, or join a stream", live: true },
    { name: "Text", to: "Text", desc: "Conversation + push-to-talk", live: true },
    { name: "Games", to: "Games", desc: "Hearts · chess · backgammon", live: false },
    { name: "Calendar", to: "Calendar", desc: "Shared schedule", live: false },
  ];
  return (
    <div style={{ padding: 16 }}>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.inkDim, letterSpacing: 1,
        textTransform: "uppercase", marginBottom: 12 }}>Services available</div>
      <div style={{ display: "grid", gap: 10 }}>
        {services.map((s) => (
          <button key={s.name} onClick={() => onPick(s.to)}
            style={{ textAlign: "left", padding: "16px 16px", background: C.panel,
              border: `1px solid ${C.line}`, borderRadius: 14, cursor: "pointer",
              display: "flex", alignItems: "center", gap: 14 }}>
            <div style={{ width: 42, height: 42, borderRadius: 11, background: C.panelHi,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontFamily: MONO, color: s.live ? C.pad : C.inkDim, fontSize: 18 }}>
              {s.name[0]}
            </div>
            <div>
              <div style={{ color: C.ink, fontSize: 16, fontWeight: 600 }}>{s.name}</div>
              <div style={{ color: C.inkDim, fontSize: 12, marginTop: 2 }}>{s.desc}</div>
            </div>
            {s.live && <div style={{ marginLeft: "auto", width: 7, height: 7, borderRadius: 4,
              background: C.pad, boxShadow: `0 0 8px ${C.pad}` }} />}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------- Phone / Call ------------------------------ */
function Call(p) {
  const [mode, setMode] = useState("incall"); // "people" | "incall"

  // presentation composition — the heart of the model
  const showTextOnly = !p.videoPossible && !p.audioPossible;
  let videoState; // grid | novideo | videooff | black
  if (!p.displayOn) videoState = "black";
  else if (showTextOnly) videoState = "textonly";
  else if (!p.videoEnabled) videoState = "videooff";
  else if (!p.videoPossible) videoState = "novideo";
  else videoState = "grid";

  let audioLabel; // Stereo | Mono | Off | None
  if (!p.audioEnabled) audioLabel = "Off";
  else if (!p.audioPossible) audioLabel = "None";
  else audioLabel = p.audioRung === 1 ? "Stereo" : "Mono";

  // self-view height by local control
  const selfH = { Full: 200, Half: 120, Thumbnail: 64, Off: 0 }[p.selfView];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* the "their video on top" region — grows as self-view shrinks */}
      <div style={{ flex: 1, minHeight: 220, background: "#070d0c", position: "relative",
        display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
        <VideoRegion state={videoState} others={OTHERS} rung={p.RUNGS[p.videoRung]} />
        {/* audio indicator badge */}
        <div style={{ position: "absolute", top: 10, left: 10, fontFamily: MONO,
          fontSize: 11, padding: "3px 8px", borderRadius: 6,
          background: "rgba(0,0,0,.5)",
          color: audioLabel === "Stereo" ? C.pad : audioLabel === "Mono" ? C.warn
            : audioLabel === "None" ? C.bad : C.inkDim,
          border: `1px solid ${C.line}` }}>
          ♪ {audioLabel}
        </div>
        {videoState === "grid" && (
          <div style={{ position: "absolute", top: 10, right: 10, fontFamily: MONO,
            fontSize: 10, color: C.inkDim, background: "rgba(0,0,0,.5)", padding: "3px 7px",
            borderRadius: 6, border: `1px solid ${C.line}` }}>{p.RUNGS[p.videoRung].split(" ")[0]}</div>
        )}
      </div>

      {/* self-view (local capture) — Full/Half/Thumb/Off */}
      {p.selfView !== "Off" && (
        <div style={{ height: selfH, background: "#0a1211", borderTop: `1px solid ${C.line}`,
          position: "relative", display: "flex", alignItems: "center",
          justifyContent: "center", transition: "height .2s" }}>
          <div style={{ fontFamily: MONO, fontSize: 12, color: C.inkDim }}>
            {p.uplink === "watch" ? "you · watching (no uplink)"
              : p.uplink === "audio" ? "you · audio only on uplink"
              : "you · self-view"}
          </div>
          <div style={{ position: "absolute", bottom: 6, right: 8, fontFamily: MONO,
            fontSize: 10, color: C.padDim }}>{p.selfView}</div>
        </div>
      )}

      {/* chat strip — a few lines from the conversation tuple */}
      <div style={{ height: 78, overflow: "auto", padding: "8px 12px", background: C.bg,
        borderTop: `1px solid ${C.line}` }}>
        {p.conversation.slice(-3).map((m, i) => (
          <div key={i} style={{ fontSize: 12, marginBottom: 3 }}>
            <span style={{ color: m.from === "you" ? C.pad : C.inkDim, fontFamily: MONO }}>{m.from}:</span>{" "}
            <span style={{ color: C.ink }}>{m.t}</span>
          </div>
        ))}
      </div>

      {/* controls */}
      <Controls {...p} videoState={videoState} setMode={setMode} />
    </div>
  );
}

function VideoRegion({ state, others, rung }) {
  if (state === "black") return null; // black frame: render nothing
  if (state === "textonly")
    return <Placeholder big="Text Only" small="audio and video can't be carried — call continues as text" tone={C.warn} />;
  if (state === "novideo")
    return <Placeholder big="No Video" small="video can't be carried on this link" tone={C.bad} />;
  if (state === "videooff")
    return <Placeholder big="Video Off" small="you turned video off" tone={C.inkDim} />;
  // grid of others (minus you)
  const cols = others.length <= 1 ? 1 : 2;
  return (
    <div style={{ position: "absolute", inset: 8, display: "grid",
      gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 6 }}>
      {others.map((o) => (
        <div key={o.name} style={{ background: "#0e1a18", borderRadius: 10,
          border: o.speaking ? `1.5px solid ${C.pad}` : `1px solid ${C.line}`,
          display: "flex", alignItems: "center", justifyContent: "center",
          position: "relative", boxShadow: o.speaking ? `0 0 12px ${C.padDim}` : "none" }}>
          <div style={{ width: 44, height: 44, borderRadius: 22, background: C.panelHi,
            display: "flex", alignItems: "center", justifyContent: "center",
            color: C.pad, fontFamily: MONO, fontSize: 18 }}>{o.name[0]}</div>
          <div style={{ position: "absolute", bottom: 5, left: 7, fontFamily: MONO,
            fontSize: 10, color: C.inkDim }}>{o.name}</div>
        </div>
      ))}
    </div>
  );
}

function Placeholder({ big, small, tone }) {
  return (
    <div style={{ textAlign: "center", padding: 24 }}>
      <div style={{ fontFamily: MONO, fontSize: 22, color: tone, letterSpacing: 1 }}>{big}</div>
      <div style={{ fontSize: 12, color: C.inkDim, marginTop: 8, maxWidth: 240 }}>{small}</div>
    </div>
  );
}

function Controls(p) {
  const Btn = ({ on, onClick, children, accent }) => (
    <button onClick={onClick} style={{ flex: 1, padding: "9px 4px", borderRadius: 9,
      border: `1px solid ${on ? (accent || C.pad) : C.line}`, cursor: "pointer",
      background: on ? (accent ? "rgba(232,176,75,.12)" : "rgba(74,222,128,.12)") : C.panel,
      color: on ? (accent || C.pad) : C.inkDim, fontFamily: MONO, fontSize: 11 }}>
      {children}
    </button>
  );
  const cycleSelf = () => {
    const order = ["Full", "Half", "Thumbnail", "Off"];
    p.setSelfView(order[(order.indexOf(p.selfView) + 1) % order.length]);
  };
  const cycleUplink = () => {
    const order = ["av", "audio", "watch"];
    p.setUplink(order[(order.indexOf(p.uplink) + 1) % order.length]);
  };
  return (
    <div style={{ padding: 10, background: C.panel, borderTop: `1px solid ${C.line}`,
      display: "grid", gap: 8 }}>
      <div style={{ display: "flex", gap: 6 }}>
        <Btn on={p.videoEnabled} onClick={() => p.setVideoEnabled(!p.videoEnabled)}>video {p.videoEnabled ? "on" : "off"}</Btn>
        <Btn on={p.audioEnabled} onClick={() => p.setAudioEnabled(!p.audioEnabled)}>audio {p.audioEnabled ? "on" : "off"}</Btn>
        <Btn on={p.displayOn} onClick={() => p.setDisplayOn(!p.displayOn)}>display {p.displayOn ? "on" : "off"}</Btn>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <Btn on={false} onClick={cycleSelf}>self · {p.selfView}</Btn>
        <Btn on={p.uplink !== "watch"} onClick={cycleUplink}>
          uplink · {p.uplink === "av" ? "A/V" : p.uplink === "audio" ? "audio" : "watch"}
        </Btn>
        <Btn on={p.ptt} accent={C.warn} onClick={() => p.setPtt(!p.ptt)}>PTT</Btn>
      </div>
      {/* link-condition simulators so every presentation state is drivable */}
      <div style={{ display: "flex", gap: 6, marginTop: 2 }}>
        <Btn on={!p.videoPossible} accent={C.bad} onClick={() => p.setVideoPossible(!p.videoPossible)}>
          {p.videoPossible ? "drop video link" : "video impossible"}
        </Btn>
        <Btn on={!p.audioPossible} accent={C.bad} onClick={() => p.setAudioPossible(!p.audioPossible)}>
          {p.audioPossible ? "drop audio link" : "audio impossible"}
        </Btn>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <Btn on={false} onClick={() => p.setVideoRung(Math.max(0, p.videoRung - 1))}>rung −</Btn>
        <div style={{ flex: 2, textAlign: "center", fontFamily: MONO, fontSize: 11,
          color: C.inkDim, alignSelf: "center" }}>{p.RUNGS[p.videoRung]}</div>
        <Btn on={false} onClick={() => p.setVideoRung(Math.min(3, p.videoRung + 1))}>rung +</Btn>
      </div>
    </div>
  );
}

/* --------------------------------- Text tab -------------------------------- */
function Text({ conversation, draft, setDraft, sendMessage, ptt, setPtt, speakText, setSpeakText }) {
  const endRef = useRef(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [conversation]);
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ flex: 1, overflow: "auto", padding: "12px 14px" }}>
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.inkDim, marginBottom: 10 }}>
          one conversation tuple · refresh is SAME until it grows
        </div>
        {conversation.map((m, i) => (
          <div key={i} style={{ display: "flex", justifyContent: m.from === "you" ? "flex-end" : "flex-start",
            marginBottom: 8 }}>
            <div style={{ maxWidth: "76%", padding: "8px 11px", borderRadius: 13,
              background: m.from === "you" ? C.padDim : C.panel,
              border: `1px solid ${C.line}`, color: C.ink, fontSize: 14 }}>
              {m.from !== "you" && <div style={{ fontFamily: MONO, fontSize: 10, color: C.pad,
                marginBottom: 2 }}>{m.from}</div>}
              {m.t}
              <div style={{ fontFamily: MONO, fontSize: 9, color: C.inkDim, marginTop: 3,
                textAlign: "right" }}>{m.at}</div>
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      {/* spoken-text-output toggle (local control) */}
      <div style={{ padding: "6px 14px", borderTop: `1px solid ${C.line}`, display: "flex",
        alignItems: "center", gap: 8 }}>
        <button onClick={() => setSpeakText(!speakText)}
          style={{ fontFamily: MONO, fontSize: 11, padding: "4px 9px", borderRadius: 7,
            border: `1px solid ${speakText ? C.pad : C.line}`, cursor: "pointer",
            background: speakText ? "rgba(74,222,128,.12)" : C.panel,
            color: speakText ? C.pad : C.inkDim }}>
          speak incoming {speakText ? "on" : "off"}
        </button>
        <span style={{ fontFamily: MONO, fontSize: 10, color: C.inkDim }}>local · TTS</span>
      </div>

      {/* input row + PTT */}
      <div style={{ padding: 10, background: C.panel, borderTop: `1px solid ${C.line}`,
        display: "flex", gap: 8, alignItems: "center" }}>
        <button onMouseDown={() => setPtt(true)} onMouseUp={() => setPtt(false)}
          onMouseLeave={() => setPtt(false)}
          style={{ width: 58, height: 44, borderRadius: 10, cursor: "pointer",
            border: `1px solid ${ptt ? C.warn : C.line}`,
            background: ptt ? "rgba(232,176,75,.18)" : C.panelHi,
            color: ptt ? C.warn : C.inkDim, fontFamily: MONO, fontSize: 11 }}>
          PTT
        </button>
        <input value={draft} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && sendMessage()}
          placeholder="message"
          style={{ flex: 1, padding: "11px 12px", borderRadius: 10, background: C.bg,
            border: `1px solid ${C.line}`, color: C.ink, fontSize: 14, outline: "none" }} />
        <button onClick={sendMessage}
          style={{ padding: "0 16px", height: 44, borderRadius: 10, cursor: "pointer",
            border: "none", background: C.pad, color: "#06140c", fontWeight: 700,
            fontFamily: MONO, fontSize: 13 }}>send</button>
      </div>
    </div>
  );
}

/* ------------------------------- bundle tabs ------------------------------- */
function Bundle({ name, note }) {
  return (
    <div style={{ padding: 24, textAlign: "center" }}>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.inkDim, letterSpacing: 1,
        textTransform: "uppercase", marginBottom: 14 }}>{name} hub</div>
      <div style={{ width: 64, height: 64, borderRadius: 16, background: C.panel,
        border: `1px solid ${C.line}`, margin: "0 auto 16px", display: "flex",
        alignItems: "center", justifyContent: "center", color: C.padDim, fontFamily: MONO,
        fontSize: 26 }}>{name[0]}</div>
      <div style={{ color: C.inkDim, fontSize: 13, maxWidth: 260, margin: "0 auto",
        lineHeight: 1.5 }}>{note}</div>
      <div style={{ fontFamily: MONO, fontSize: 10, color: C.padDim, marginTop: 18 }}>
        tab present because the bundle is installed
      </div>
    </div>
  );
}
