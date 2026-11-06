# Observer-visibility experiment: pre-registered hypotheses

Recorded 2026-11-06, before any held-out seed (7-10) ran. This file is committed to the repository before the matrix
runs; its commit time is the record.

## The question

How well can an observer recover who follows whom (the 24 true "j follows i" edges among 56 ordered pairs of 8
participants) under five builds of one messaging workload:

- A: direct TLS 1.3, each producer to each of its followers
- B: stock Mosquitto, topic `chat/<recipient>`, TLS 1.3 on 8883
- C: chat-ram, the chat example's own schema (`ChatServer.<recipient>.<producer>`), over WireGuard (wireguard-go)
- D: chat-ram, own truth (`Chat.<producer>.out`, one held read per producer followed), over WireGuard
- D-all: chat-ram, read-all (`Chat.Out.<producer>`, every consumer one held read, filtered locally), over WireGuard

## Observers

- O1: p1's link, from a headers-only capture. It is scored on the 14 pairs that involve p1.
- O2: the server's link, from a headers-only capture. It is scored on all 56 pairs. A has no server, so O2 sees nothing
  for A.
- O3: the server operator. For C, D and D-all this is the operations the host decodes, logged at the client library
  boundary. For B it is Mosquitto's own log. It is scored on all 56 pairs.
- O4: p1 as an authorized insider, reading the whole service. It is scored on the 42 pairs without p1, because the
  insider already knows its own edges.

## Metric and decision rule

The primary metric is the held-out AUC (seeds 7-10) of a logistic regression trained on seeds 1-6, with a 95%
bootstrap CI over 1000 draws that resample seeds and then pairs within each seed.

A condition reduces linkability for an observer only if its AUC is below B's for that observer and the two CIs do not
overlap.

## Hypotheses

- **H1:** at O1, C, D and D-all show lower AUC than A.
- **H2:** at O2, C, D and D-all show no lower AUC than B. Tested as: the condition's CI is not entirely below B's.
- **H3:** at O3, D-all < D < C. Each step requires non-overlapping CIs.
- **H4:** the semantic wire's size pattern raises AUC at O1 and O2 relative to the same condition with padded sizes.
  **Not testable in this build:** the Ribbit wire has no padding flag (only RTT pings carry padding). H4 is reported as
  not run.

Results against FrogNet are reported exactly as results for it.
