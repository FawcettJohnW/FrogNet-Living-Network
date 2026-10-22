1200 instances (4-11 nodes, 1-6 members); the two optimum methods agree on 1200; 0 invalid trees or reference failures

| links | topology | n | tree | optimal | cost/opt p50 | p90 | max | worst member stretch | ties other way: p90 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| symmetric | chain | 150 | shortest-path (requester) | 150 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| symmetric | chain | 150 | reverse-path (responses) | 150 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| symmetric | star | 150 | shortest-path (requester) | 150 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| symmetric | star | 150 | reverse-path (responses) | 150 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| symmetric | ring | 150 | shortest-path (requester) | 95 | 1.000 | 1.320 | 1.889 | 1.000 | 1.320 | 1.889 |
| symmetric | ring | 150 | reverse-path (responses) | 95 | 1.000 | 1.320 | 1.889 | 1.000 | 1.320 | 1.889 |
| symmetric | branch | 150 | shortest-path (requester) | 103 | 1.000 | 1.250 | 1.647 | 1.000 | 1.217 | 1.800 |
| symmetric | branch | 150 | reverse-path (responses) | 103 | 1.000 | 1.250 | 1.647 | 1.000 | 1.217 | 1.800 |
| asymmetric | chain | 150 | shortest-path (requester) | 150 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| asymmetric | chain | 150 | reverse-path (responses) | 150 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| asymmetric | star | 150 | shortest-path (requester) | 150 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| asymmetric | star | 150 | reverse-path (responses) | 150 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| asymmetric | ring | 150 | shortest-path (requester) | 83 | 1.000 | 1.476 | 1.875 | 1.000 | 1.500 | 1.875 |
| asymmetric | ring | 150 | reverse-path (responses) | 68 | 1.050 | 1.750 | 3.182 | 3.667 | 1.800 | 3.182 |
| asymmetric | branch | 150 | shortest-path (requester) | 96 | 1.000 | 1.258 | 1.833 | 1.000 | 1.278 | 1.833 |
| asymmetric | branch | 150 | reverse-path (responses) | 48 | 1.143 | 1.833 | 6.000 | 14.000 | 1.800 | 3.750 |

Symmetric graphs: reverse-path cost equals shortest-path cost in 600 of 600.
Asymmetric graphs: reverse-path costs more than shortest-path in 103 of 600, less in 26.
Tie rule changes the reverse-path cost in 55 of 1200 instances.
