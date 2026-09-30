# One Map-Register through lisp-boundary, taken apart (2026-09-27, loopback, v0.54)
- Sequential, one sender: 2.7-3.5 ms steady (first 5.6 ms). lispers.net: ~2 ms. The 16-23 ms medians in
  compare_lispers.py are 16 registers in flight at once on a one-core machine.
- Frames the boundary sends per steady register (count.sh, trace register-frames-trace.log): 19.
    the register itself : 1 read of the per-length registration table (the prior -- the WHOLE table, v0.46),
                          1 read of the length manifest, 1 registration write, 1 governance write
    held-view traffic   : ~6.5 writes of resolver-applied|0| cells and ~13 held-read re-arms per register --
                          every Engine in the process that holds the registration / governance views wakes on each
                          write, re-reads, and writes its applied cell
- The boundary runs 10 Engines (request, configuration, 8 register workers); each keeps its own held views.
- The applied cells are written at ONE address per (variable, instance) -- resolver-applied|iid|group /
  registration|LEN -- by every Engine and every process that holds the view; the practice ("an applied cell that
  it alone writes") is not met: one participant's wait can be satisfied by another's cell.
