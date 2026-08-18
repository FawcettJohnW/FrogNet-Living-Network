// figures.js — makes Magnum Croakus self-generating.
//
// Drop this file beside build_magnum.js and add ONE line near the top of
// build_magnum.js, before the first img() call is evaluated:
//
//     require("./figures").ensure();
//
// That is the whole integration. On every build it checks magnum-figures/ for
// the PNGs the document references, and shells out to tools/make_figures.py for
// any that are missing. Nothing is regenerated unless it is absent, so a build
// stays fast; pass FIGURES_FORCE=1 to redraw everything after editing the
// drawing code.
//
//     node build_magnum.js                  # draws whatever is missing
//     FIGURES_FORCE=1 node build_magnum.js  # redraw all figures first
//     FIGURES_SKIP=1 node build_magnum.js   # trust what is on disk
//
// Requires python3 with Pillow. If either is absent the build stops with the
// exact command to run, rather than emitting a docx full of placeholder boxes.

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const DIR = process.env.FIGURES_DIR || "magnum-figures";
const SCRIPT = process.env.FIGURES_SCRIPT || path.join("tools", "make_figures.py");

// The figures the document places. Keep this list in step with img() calls —
// verify() below will tell you when it drifts.
const REQUIRED = [
  "multi_transport",
  "two_planes",
  "fanout",
  "quality_ladder",
  "backpressure",
  "keyframe_anchor",
];

// Drawn by the same script and available to the book and the site. Generated
// alongside the required set so nothing has to be produced by hand later.
const EXTRA = [
  "resolv_chain",
  "broker_rendezvous",
  "handler_object",
  "shotgun",
  "derivation",
  "hello_world",
  "wire_states",
  "dead_air",
  "discovery_walk",
  "election",
  "tuple_address",
  "address_scaling",
  "airgap_broker",
  "scopes",
  "monitor_read",
  "three_arms",
  "lamp_path",
];

function python() {
  for (const bin of [process.env.PYTHON, "python3", "python"].filter(Boolean)) {
    const r = spawnSync(bin, ["-c", "import PIL, sys; print(sys.version_info[0])"],
                        { encoding: "utf8" });
    if (r.status === 0) return bin;
  }
  return null;
}

function missing(names) {
  return names.filter((n) => !fs.existsSync(path.join(DIR, n + ".png")));
}

/**
 * Draw any figure that is not on disk. Called once per build.
 * @param {{all?: boolean}} opts  all:false limits generation to REQUIRED.
 */
function ensure(opts = {}) {
  if (process.env.FIGURES_SKIP) {
    console.log("[figures] FIGURES_SKIP set — using whatever is in " + DIR);
    return;
  }
  const wanted = opts.all === false ? REQUIRED : REQUIRED.concat(EXTRA);
  const force = !!process.env.FIGURES_FORCE;
  const todo = force ? wanted : missing(wanted);

  if (!todo.length) {
    console.log("[figures] all " + wanted.length + " figures present in " + DIR);
    return;
  }

  const py = python();
  if (!py) {
    throw new Error(
      "[figures] python3 with Pillow is required to draw " + todo.length +
      " missing figure(s).\n" +
      "          pip install pillow\n" +
      "          then re-run, or run it by hand:\n" +
      "          python3 " + SCRIPT + " --out " + DIR
    );
  }
  if (!fs.existsSync(SCRIPT)) {
    throw new Error("[figures] " + SCRIPT + " not found — cannot draw figures.");
  }

  const args = [SCRIPT, "--out", DIR, "--only", todo.join(",")];
  if (force) args.push("--force");
  console.log("[figures] drawing " + todo.length + ": " + todo.join(", "));
  const r = spawnSync(py, args, { encoding: "utf8", stdio: "inherit" });
  if (r.status !== 0) {
    throw new Error("[figures] " + SCRIPT + " exited " + r.status);
  }

  const still = missing(wanted);
  if (still.length) {
    throw new Error("[figures] still missing after generation: " + still.join(", "));
  }
}

/**
 * Cross-check the img() calls in a builder source against REQUIRED, so the two
 * lists cannot drift apart silently. Call with __filename from build_magnum.js.
 */
function verify(builderPath) {
  const src = fs.readFileSync(builderPath, "utf8");
  const used = [...new Set([...src.matchAll(/img\("([\w.-]+)\.png"/g)].map((m) => m[1]))];
  const undeclared = used.filter((n) => !REQUIRED.includes(n));
  const unused = REQUIRED.filter((n) => !used.includes(n));
  if (undeclared.length) {
    console.warn("[figures] placed but not in REQUIRED: " + undeclared.join(", "));
  }
  if (unused.length) {
    console.warn("[figures] in REQUIRED but never placed: " + unused.join(", "));
  }
  return { used, undeclared, unused };
}

module.exports = { ensure, verify, REQUIRED, EXTRA, DIR };
