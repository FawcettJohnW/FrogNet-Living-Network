// @ds-adherence-ignore -- omelette starter scaffold (raw elements/hex/px by design)

/* BEGIN USAGE */
// animations.jsx
// Reusable animation starter: Stage, Timeline, Sprite, easing helpers.
// Exports (to window): Stage, Sprite, PlaybackBar, TextSprite, ImageSprite, RectSprite,
//   useTime, useTimeline, useSprite, Easing, interpolate, animate, clamp.
//
// Usage (in an HTML file that loads React + Babel):
//
//   <Stage width={1280} height={720} duration={10} background="#f6f4ef">
//     <MyScene />
//   </Stage>
//
// <Stage> auto-scales to the viewport and provides the scrubber, play/pause,
// ←/→ seek, space, and 0-to-reset controls, and persists the playhead.
// Inside <Stage>, any child can call useTime() to read the current
// playhead (seconds). Or wrap content in <Sprite start={1} end={4}>...</Sprite>
// to only render during that window -- children receive a `localTime` and
// `progress` via the useSprite() hook. Use Easing + interpolate()/animate()
// for tweens; TextSprite / ImageSprite / RectSprite have built-in entry/exit.
// Build YOUR scenes by composing Sprites inside a Stage.
/* END USAGE */
// ─────────────────────────────────────────────────────────────────────────────

// ── Easing functions (hand-rolled, Popmotion-style) ─────────────────────────
// All easings take t ∈ [0,1] and return eased t ∈ [0,1] (may overshoot for back/elastic).
const Easing = {
  linear: (t) => t,

  // Quad
  easeInQuad:    (t) => t * t,
  easeOutQuad:   (t) => t * (2 - t),
  easeInOutQuad: (t) => (t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t),

  // Cubic
  easeInCubic:    (t) => t * t * t,
  easeOutCubic:   (t) => (--t) * t * t + 1,
  easeInOutCubic: (t) => (t < 0.5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1),

  // Quart
  easeInQuart:    (t) => t * t * t * t,
  easeOutQuart:   (t) => 1 - (--t) * t * t * t,
  easeInOutQuart: (t) => (t < 0.5 ? 8 * t * t * t * t : 1 - 8 * (--t) * t * t * t),

  // Expo
  easeInExpo:  (t) => (t === 0 ? 0 : Math.pow(2, 10 * (t - 1))),
  easeOutExpo: (t) => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t)),
  easeInOutExpo: (t) => {
    if (t === 0) return 0;
    if (t === 1) return 1;
    if (t < 0.5) return 0.5 * Math.pow(2, 20 * t - 10);
    return 1 - 0.5 * Math.pow(2, -20 * t + 10);
  },

  // Sine
  easeInSine:    (t) => 1 - Math.cos((t * Math.PI) / 2),
  easeOutSine:   (t) => Math.sin((t * Math.PI) / 2),
  easeInOutSine: (t) => -(Math.cos(Math.PI * t) - 1) / 2,

  // Back (overshoot)
  easeOutBack: (t) => {
    const c1 = 1.70158, c3 = c1 + 1;
    return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
  },
  easeInBack: (t) => {
    const c1 = 1.70158, c3 = c1 + 1;
    return c3 * t * t * t - c1 * t * t;
  },
  easeInOutBack: (t) => {
    const c1 = 1.70158, c2 = c1 * 1.525;
    return t < 0.5
      ? (Math.pow(2 * t, 2) * ((c2 + 1) * 2 * t - c2)) / 2
      : (Math.pow(2 * t - 2, 2) * ((c2 + 1) * (t * 2 - 2) + c2) + 2) / 2;
  },

  // Elastic
  easeOutElastic: (t) => {
    const c4 = (2 * Math.PI) / 3;
    if (t === 0) return 0;
    if (t === 1) return 1;
    return Math.pow(2, -10 * t) * Math.sin((t * 10 - 0.75) * c4) + 1;
  },
};

// ── Core interpolation helpers ──────────────────────────────────────────────

// Clamp a value to [min, max]
const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

// interpolate([0, 0.5, 1], [0, 100, 50], ease?) -> fn(t)
// Popmotion-style: linearly maps t across input keyframes to output values,
// with optional easing per segment (single fn or array of fns).
function interpolate(input, output, ease = Easing.linear) {
  return (t) => {
    if (t <= input[0]) return output[0];
    if (t >= input[input.length - 1]) return output[output.length - 1];
    for (let i = 0; i < input.length - 1; i++) {
      if (t >= input[i] && t <= input[i + 1]) {
        const span = input[i + 1] - input[i];
        const local = span === 0 ? 0 : (t - input[i]) / span;
        const easeFn = Array.isArray(ease) ? (ease[i] || Easing.linear) : ease;
        const eased = easeFn(local);
        return output[i] + (output[i + 1] - output[i]) * eased;
      }
    }
    return output[output.length - 1];
  };
}

// animate({from, to, start, end, ease})(t) — simpler single-segment tween.
// Returns `from` before `start`, `to` after `end`.
function animate({ from = 0, to = 1, start = 0, end = 1, ease = Easing.easeInOutCubic }) {
  return (t) => {
    if (t <= start) return from;
    if (t >= end) return to;
    const local = (t - start) / (end - start);
    return from + (to - from) * ease(local);
  };
}

// ── Timeline context ────────────────────────────────────────────────────────

const TimelineContext = React.createContext({ time: 0, duration: 10, playing: false });

const useTime = () => React.useContext(TimelineContext).time;
const useTimeline = () => React.useContext(TimelineContext);

// ── Sprite ──────────────────────────────────────────────────────────────────
// Renders children only when the playhead is inside [start, end]. Provides
// a sub-context with `localTime` (seconds since start) and `progress` (0..1).
//
//   <Sprite start={2} end={5}>
//     {({ localTime, progress }) => <Thing x={progress * 100} />}
//   </Sprite>
//
// Or as a plain wrapper — children can call useSprite() themselves.

const SpriteContext = React.createContext({ localTime: 0, progress: 0, duration: 0 });
const useSprite = () => React.useContext(SpriteContext);

function Sprite({ start = 0, end = Infinity, children, keepMounted = false }) {
  const { time } = useTimeline();
  const visible = time >= start && time <= end;
  if (!visible && !keepMounted) return null;

  const duration = end - start;
  const localTime = Math.max(0, time - start);
  const progress = duration > 0 && isFinite(duration)
    ? clamp(localTime / duration, 0, 1)
    : 0;

  const value = { localTime, progress, duration, visible };

  return (
    <SpriteContext.Provider value={value}>
      {typeof children === 'function' ? children(value) : children}
    </SpriteContext.Provider>
  );
}

// ── Sample sprite components ────────────────────────────────────────────────

// TextSprite: fades/slides text in on entry, holds, then fades out on exit.
// Props: text, x, y, size, color, font, entryDur, exitDur, align
function TextSprite({
  text,
  x = 0, y = 0,
  size = 48,
  color = '#111',
  font = 'Inter, system-ui, sans-serif',
  weight = 600,
  entryDur = 0.45,
  exitDur = 0.35,
  entryEase = Easing.easeOutBack,
  exitEase = Easing.easeInCubic,
  align = 'left',
  letterSpacing = '-0.01em',
}) {
  const { localTime, duration } = useSprite();
  const exitStart = Math.max(0, duration - exitDur);

  let opacity = 1;
  let ty = 0;

  if (localTime < entryDur) {
    const t = entryEase(clamp(localTime / entryDur, 0, 1));
    opacity = t;
    ty = (1 - t) * 16;
  } else if (localTime > exitStart) {
    const t = exitEase(clamp((localTime - exitStart) / exitDur, 0, 1));
    opacity = 1 - t;
    ty = -t * 8;
  }

  const translateX = align === 'center' ? '-50%' : align === 'right' ? '-100%' : '0';

  return (
    <div style={{
      position: 'absolute',
      left: x, top: y,
      transform: `translate(${translateX}, ${ty}px)`,
      opacity,
      fontFamily: font,
      fontSize: size,
      fontWeight: weight,
      color,
      letterSpacing,
      whiteSpace: 'pre',
      lineHeight: 1.1,
      willChange: 'transform, opacity',
    }}>
      {text}
    </div>
  );
}

// ImageSprite: scales + fades in; optional Ken Burns drift during hold.
function ImageSprite({
  src,
  x = 0, y = 0,
  width = 400, height = 300,
  entryDur = 0.6,
  exitDur = 0.4,
  kenBurns = false,
  kenBurnsScale = 1.08,
  radius = 12,
  fit = 'cover',
  placeholder = null, // {label: string} for striped placeholder
}) {
  const { localTime, duration } = useSprite();
  const exitStart = Math.max(0, duration - exitDur);

  let opacity = 1;
  let scale = 1;

  if (localTime < entryDur) {
    const t = Easing.easeOutCubic(clamp(localTime / entryDur, 0, 1));
    opacity = t;
    scale = 0.96 + 0.04 * t;
  } else if (localTime > exitStart) {
    const t = Easing.easeInCubic(clamp((localTime - exitStart) / exitDur, 0, 1));
    opacity = 1 - t;
    scale = (kenBurns ? kenBurnsScale : 1) + 0.02 * t;
  } else if (kenBurns) {
    const holdSpan = exitStart - entryDur;
    const holdT = holdSpan > 0 ? (localTime - entryDur) / holdSpan : 0;
    scale = 1 + (kenBurnsScale - 1) * holdT;
  }

  const content = placeholder ? (
    <div style={{
      width: '100%', height: '100%',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'repeating-linear-gradient(135deg, #e9e6df 0 10px, #dcd8cf 10px 20px)',
      color: '#6b6458',
      fontFamily: 'JetBrains Mono, ui-monospace, monospace',
      fontSize: 13,
      letterSpacing: '0.04em',
      textTransform: 'uppercase',
    }}>
      {placeholder.label || 'image'}
    </div>
  ) : (
    <img src={src} alt="" style={{ width: '100%', height: '100%', objectFit: fit, display: 'block' }} />
  );

  return (
    <div style={{
      position: 'absolute',
      left: x, top: y,
      width, height,
      opacity,
      transform: `scale(${scale})`,
      transformOrigin: 'center',
      borderRadius: radius,
      overflow: 'hidden',
      willChange: 'transform, opacity',
    }}>
      {content}
    </div>
  );
}

// RectSprite: simple rectangle that animates position/size/color via props.
// Useful demo primitive — takes a `render` fn for per-frame customization.
function RectSprite({
  x = 0, y = 0,
  width = 100, height = 100,
  color = '#111',
  radius = 8,
  entryDur = 0.4,
  exitDur = 0.3,
  render, // optional: (ctx) => style overrides
}) {
  const spriteCtx = useSprite();
  const { localTime, duration } = spriteCtx;
  const exitStart = Math.max(0, duration - exitDur);

  let opacity = 1;
  let scale = 1;

  if (localTime < entryDur) {
    const t = Easing.easeOutBack(clamp(localTime / entryDur, 0, 1));
    opacity = clamp(localTime / entryDur, 0, 1);
    scale = 0.4 + 0.6 * t;
  } else if (localTime > exitStart) {
    const t = Easing.easeInQuad(clamp((localTime - exitStart) / exitDur, 0, 1));
    opacity = 1 - t;
    scale = 1 - 0.15 * t;
  }

  const overrides = render ? render(spriteCtx) : {};

  return (
    <div style={{
      position: 'absolute',
      left: x, top: y,
      width, height,
      background: color,
      borderRadius: radius,
      opacity,
      transform: `scale(${scale})`,
      transformOrigin: 'center',
      willChange: 'transform, opacity',
      ...overrides,
    }} />
  );
}


function Stage({
  width = 1280,
  height = 720,
  duration = 10,
  background = '#f6f4ef',
  fps = 60,
  loop = true,
  autoplay = true,
  persistKey = 'animstage',
  children,
}) {
  const [time, setTime] = React.useState(() => {
    try {
      const v = parseFloat(localStorage.getItem(persistKey + ':t') || '0');
      return isFinite(v) ? clamp(v, 0, duration) : 0;
    } catch { return 0; }
  });
  const [playing, setPlaying] = React.useState(autoplay);
  const [hoverTime, setHoverTime] = React.useState(null);
  const [scale, setScale] = React.useState(1);

  const stageRef = React.useRef(null);
  const canvasRef = React.useRef(null);
  const rafRef = React.useRef(null);
  const lastTsRef = React.useRef(null);

  // Persist playhead
  React.useEffect(() => {
    try { localStorage.setItem(persistKey + ':t', String(time)); } catch {}
  }, [time, persistKey]);

  // Auto-scale to fit viewport
  React.useEffect(() => {
    if (!stageRef.current) return;
    const el = stageRef.current;
    const measure = () => {
      const barH = 44; // playback bar height
      const s = Math.min(
        el.clientWidth / width,
        (el.clientHeight - barH) / height
      );
      setScale(Math.max(0.05, s));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener('resize', measure);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [width, height]);

  // Animation loop
  React.useEffect(() => {
    if (!playing) {
      lastTsRef.current = null;
      return;
    }
    const step = (ts) => {
      if (lastTsRef.current == null) lastTsRef.current = ts;
      const dt = (ts - lastTsRef.current) / 1000;
      lastTsRef.current = ts;
      setTime((t) => {
        let next = t + dt;
        if (next >= duration) {
          if (loop) next = next % duration;
          else { next = duration; setPlaying(false); }
        }
        return next;
      });
      rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      lastTsRef.current = null;
    };
  }, [playing, duration, loop]);

  // Keyboard: space = play/pause, ← → = seek
  React.useEffect(() => {
    const onKey = (e) => {
      if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
      if (e.code === 'Space') {
        e.preventDefault();
        setPlaying(p => !p);
      } else if (e.code === 'ArrowLeft') {
        setTime(t => clamp(t - (e.shiftKey ? 1 : 0.1), 0, duration));
      } else if (e.code === 'ArrowRight') {
        setTime(t => clamp(t + (e.shiftKey ? 1 : 0.1), 0, duration));
      } else if (e.key === '0' || e.code === 'Home') {
        setTime(0);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [duration]);

  const displayTime = hoverTime != null ? hoverTime : time;

  const ctxValue = React.useMemo(
    () => ({ time: displayTime, duration, playing, setTime, setPlaying }),
    [displayTime, duration, playing]
  );

  return (
    <div
      ref={stageRef}
      style={{
        position: 'absolute', inset: 0,
        display: 'flex', flexDirection: 'column',
        alignItems: 'center',
        background: '#0a0a0a',
        fontFamily: 'Inter, system-ui, sans-serif',
      }}
    >
      {/* Canvas area — vertically centered in remaining space */}
      <div style={{
        flex: 1,
        width: '100%',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        overflow: 'hidden',
        minHeight: 0,
      }}>
        <div
          ref={canvasRef}
          style={{
            width, height,
            background,
            position: 'relative',
            transform: `scale(${scale})`,
            transformOrigin: 'center',
            flexShrink: 0,
            boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
            overflow: 'hidden',
          }}
        >
          <TimelineContext.Provider value={ctxValue}>
            {children}
          </TimelineContext.Provider>
        </div>
      </div>

      {/* Playback bar — stacked below canvas, never overlapping */}
      <PlaybackBar
        time={displayTime}
        actualTime={time}
        duration={duration}
        playing={playing}
        onPlayPause={() => setPlaying(p => !p)}
        onReset={() => { setTime(0); }}
        onSeek={(t) => setTime(t)}
        onHover={(t) => setHoverTime(t)}
      />
    </div>
  );
}

// ── Playback bar ────────────────────────────────────────────────────────────
// Play/pause, return-to-begin, scrub track, time display.
// Uses fixed-width time fields so layout doesn't thrash.

function PlaybackBar({ time, duration, playing, onPlayPause, onReset, onSeek, onHover }) {
  const trackRef = React.useRef(null);
  const [dragging, setDragging] = React.useState(false);

  const timeFromEvent = React.useCallback((e) => {
    const rect = trackRef.current.getBoundingClientRect();
    const x = clamp((e.clientX - rect.left) / rect.width, 0, 1);
    return x * duration;
  }, [duration]);

  const onTrackMove = (e) => {
    if (!trackRef.current) return;
    const t = timeFromEvent(e);
    if (dragging) {
      onSeek(t);
    } else {
      onHover(t);
    }
  };

  const onTrackLeave = () => {
    if (!dragging) onHover(null);
  };

  const onTrackDown = (e) => {
    setDragging(true);
    const t = timeFromEvent(e);
    onSeek(t);
    onHover(null);
  };

  React.useEffect(() => {
    if (!dragging) return;
    const onUp = () => setDragging(false);
    const onMove = (e) => {
      if (!trackRef.current) return;
      const t = timeFromEvent(e);
      onSeek(t);
    };
    window.addEventListener('mouseup', onUp);
    window.addEventListener('mousemove', onMove);
    return () => {
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('mousemove', onMove);
    };
  }, [dragging, timeFromEvent, onSeek]);

  const pct = duration > 0 ? (time / duration) * 100 : 0;
  const fmt = (t) => {
    const total = Math.max(0, t);
    const m = Math.floor(total / 60);
    const s = Math.floor(total % 60);
    const cs = Math.floor((total * 100) % 100);
    return `${String(m).padStart(1, '0')}:${String(s).padStart(2, '0')}.${String(cs).padStart(2, '0')}`;
  };

  const mono = 'JetBrains Mono, ui-monospace, SFMono-Regular, monospace';

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '8px 16px',
      background: 'rgba(20,20,20,0.92)',
      borderTop: '1px solid rgba(255,255,255,0.08)',
      width: '100%',
      maxWidth: 680,
      alignSelf: 'center',

      borderRadius: 8,
      color: '#f6f4ef',
      fontFamily: 'Inter, system-ui, sans-serif',
      userSelect: 'none',
      flexShrink: 0,
    }}>
      <IconButton onClick={onReset} title="Return to start (0)">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M3 2v10M12 2L5 7l7 5V2z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round"/>
        </svg>
      </IconButton>
      <IconButton onClick={onPlayPause} title="Play/pause (space)">
        {playing ? (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <rect x="3" y="2" width="3" height="10" fill="currentColor"/>
            <rect x="8" y="2" width="3" height="10" fill="currentColor"/>
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M3 2l9 5-9 5V2z" fill="currentColor"/>
          </svg>
        )}
      </IconButton>

      {/* Current time: fixed width so it doesn't thrash */}
      <div style={{
        fontFamily: mono,
        fontSize: 12,
        fontVariantNumeric: 'tabular-nums',
        width: 64, textAlign: 'right',
        color: '#f6f4ef',
      }}>
        {fmt(time)}
      </div>

      {/* Scrub track */}
      <div
        ref={trackRef}
        onMouseMove={onTrackMove}
        onMouseLeave={onTrackLeave}
        onMouseDown={onTrackDown}
        style={{
          flex: 1,
          height: 22,
          position: 'relative',
          cursor: 'pointer',
          display: 'flex', alignItems: 'center',
        }}
      >
        <div style={{
          position: 'absolute',
          left: 0, right: 0, height: 4,
          background: 'rgba(255,255,255,0.12)',
          borderRadius: 2,
        }}/>
        <div style={{
          position: 'absolute',
          left: 0, width: `${pct}%`, height: 4,
          background: 'oklch(72% 0.12 250)',
          borderRadius: 2,
        }}/>
        <div style={{
          position: 'absolute',
          left: `${pct}%`, top: '50%',
          width: 12, height: 12,
          marginLeft: -6, marginTop: -6,
          background: '#fff',
          borderRadius: 6,
          boxShadow: '0 2px 4px rgba(0,0,0,0.4)',
        }}/>
      </div>

      {/* Duration: fixed width */}
      <div style={{
        fontFamily: mono,
        fontSize: 12,
        fontVariantNumeric: 'tabular-nums',
        width: 64, textAlign: 'left',
        color: 'rgba(246,244,239,0.55)',
      }}>
        {fmt(duration)}
      </div>
    </div>
  );
}

function IconButton({ children, onClick, title }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      onClick={onClick}
      title={title}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 28, height: 28,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: hover ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: 6,
        color: '#f6f4ef',
        cursor: 'pointer',
        padding: 0,
        transition: 'background 120ms',
      }}
    >
      {children}
    </button>
  );
}


Object.assign(window, {
  Easing, interpolate, animate, clamp,
  TimelineContext, useTime, useTimeline,
  Sprite, SpriteContext, useSprite,
  TextSprite, ImageSprite, RectSprite,
  Stage, PlaybackBar,
});



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
