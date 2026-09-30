# v0.41 RED — multi-record Map-Register partial application under site-policy change (statistical)

Base: v0.40a (3fbac778b24c4cd3efe9f0bfd800d14ffd90f6e788b88eaba11779f635a6b5bd).
tools/stress_multirecord_atomic.py (from the evaluated candidate): each round publishes a site for record 1 only,
immediately sends a two-record register (record 2 has no site), and checks that record 1 is not applied unless the
register was accepted. Against unchanged v0.40a, five runs of 600 rounds: partial applications 8, 0, 0, 0, 10.
The defect is a race, so the red is statistical. No implementation source changed.
