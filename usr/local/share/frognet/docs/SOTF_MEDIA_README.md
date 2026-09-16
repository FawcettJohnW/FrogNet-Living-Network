# SotF media stack — install overlay

This overlay drops in from / and lands files at their real system paths:

  /etc/frognet_bundles/communicator/   runtime modules + the phone app
      sotf_media_codex.py      codex (AV raw on the established socket; control SAME/DIFF)
      sotf_metrics.py          throughput + control-plane convergence counters
      sotf_media_backing.py    real backings: tuple-on-:80, established socket, election regen
      sotf_autoscale.py        backlog -> ladder rung up/down (scale to fit)
      frognet_mediahost.py     reconciliation-loop media host: minus-self mix, on-demand rungs
      wire_medium.py           sim WireMedium (shaped links) used by the topology test
      communicator_app.jsx     phone-sized tabbed app (Home/Call/Text + bundle tabs)
      substrate.py / working_memory.py   convergence primitives (bundle already ships these)

  /opt/frognet_semantic/simulation/    sims + harnesses + the runner
      run_all_tests.py         runs all seven suites
      sim_sotf_media_codex.py  codex: AV byte-exact, control SAME/DIFF, scale, hostReset
      sim_mediahost.py         on-demand rungs, minus-self, float-safe rebuild
      run_movies.py            real VP8 + PCM through the codex
      compare_udp_vs_sotf.py   UDP dies ~3% loss; SotF holds through 50% (loss = time not frames)
      compare_bandwidth.py     scaler drops the ladder to fit a throttled pipe
      degrade_sweep.py         architectural degradation gap (latency/loss)
      tests_topology.py        real shaped links (LAN..Jammed) with auto-downgrade
      media_assets/            real VP8 clips + PCM + per-rung encodings

Run:  cd /opt/frognet_semantic/simulation && python3 run_all_tests.py   (needs ffmpeg)

Box steps (named, not faked): control on :80 via TupleTransient; send_frame on the real
mediahost socket; the mediahost ffmpeg muscle (amix/xstack) against live uplinks; codec.py
native TYPE_RAW + sotf_handler.py patches on every media node; the proxy _sotf graft.
License: ffmpeg subprocessed (never linked), libvpx/Opus (BSD), no bundled binary; Apache-2.0.
