## Tier 4 (MEASURED, real FRR)

| timers | topo | N | event | live loops | live longest ms | live loops past >=1 SPF | most SPFs survived | gone loops | gone longest ms | gone past >=1 SPF | most SPFs survived | reconverged ms | load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tuned | grid | 16 | silent | 8 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 3127 | 0.35 |
| tuned | grid | 16 | nodefail | 4 | 264 | 0 | 0 | 4 | 269 | 0 | 0 | 2637 | 0.19 |
| tuned | grid | 16 | partition | 0 | 0 | 0 | 0 | 38 | 352 | 0 | 0 | 8049 | 0.21 |
| tuned | grid | 32 | silent | 22 | 287 | 0 | 0 | 0 | 0 | 0 | 0 | 3196 | 1.38 |
| tuned | grid | 32 | nodefail | 27 | 38 | 0 | 0 | 4 | 34 | 0 | 0 | 8387 | 0.72 |
| tuned | grid | 32 | partition | 0 | 0 | 0 | 0 | 78 | 259 | 0 | 0 | 9048 | 0.30 |
| tuned | grid | 64 | silent | 24 | 14 | 0 | 0 | 0 | 0 | 0 | 0 | 3329 | 1.10 |
| tuned | grid | 64 | nodefail | 20 | 10 | 0 | 0 | 10 | 44 | 0 | 0 | 3112 | 1.34 |
| tuned | grid | 64 | partition | 0 | 0 | 0 | 0 | 551 | 509 | 4 | 1 | 8142 | 1.68 |
| tuned | grid | 96 | silent | 51 | 17 | 0 | 0 | 0 | 0 | 0 | 0 | 3306 | 1.26 |
| tuned | grid | 96 | nodefail | 59 | 44 | 0 | 0 | 7 | 32 | 0 | 0 | 8647 | 1.02 |
| tuned | grid | 96 | partition | 0 | 0 | 0 | 0 | 932 | 5325 | 21 | 1 | 8107 | 1.56 |
| tuned | mesh | 16 | silent | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2995 | 0.23 |
| tuned | mesh | 16 | nodefail | 0 | 0 | 0 | 0 | 2 | 36 | 0 | 0 | 2695 | 0.27 |
| tuned | mesh | 16 | partition | 1 | 228 | 0 | 0 | 49 | 5956 | 17 | 2 | 8536 | 0.39 |
| tuned | mesh | 32 | silent | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2877 | 1.60 |
| tuned | mesh | 32 | nodefail | 0 | 0 | 0 | 0 | 1 | 241 | 0 | 0 | 8732 | 1.07 |
| tuned | mesh | 32 | partition | 0 | 0 | 0 | 0 | 135 | 5345 | 17 | 1 | 8716 | 0.98 |
| tuned | mesh | 64 | silent | 54 | 42 | 0 | 0 | 0 | 0 | 0 | 0 | 3250 | 1.21 |
| tuned | mesh | 64 | nodefail | 0 | 0 | 0 | 0 | 1 | 1 | 0 | 0 | 2726 | 0.97 |
| tuned | mesh | 64 | partition | 82 | 5154 | 3 | 1 | 305 | 5351 | 9 | 1 | 8941 | 0.78 |
| tuned | mesh | 96 | silent | 17 | 150 | 0 | 0 | 0 | 0 | 0 | 0 | 2913 | 1.18 |
| tuned | mesh | 96 | nodefail | 29 | 89 | 0 | 0 | 8 | 55 | 0 | 0 | 8208 | 1.36 |
| tuned | mesh | 96 | partition | 346 | 5365 | 24 | 1 | 925 | 5365 | 74 | 1 | 8107 | 2.83 |
| tuned | ring | 16 | silent | 31 | 223 | 0 | 0 | 0 | 0 | 0 | 0 | 2683 | 0.13 |
| tuned | ring | 16 | nodefail | 36 | 36 | 0 | 0 | 6 | 36 | 0 | 0 | 8580 | 0.17 |
| tuned | ring | 16 | partition | 0 | 0 | 0 | 0 | 56 | 37 | 0 | 0 | 2852 | 0.27 |
| tuned | ring | 32 | silent | 128 | 229 | 0 | 0 | 0 | 0 | 0 | 0 | 2684 | 0.41 |
| tuned | ring | 32 | nodefail | 174 | 198 | 0 | 0 | 15 | 36 | 0 | 0 | 3343 | 0.47 |
| tuned | ring | 32 | partition | 0 | 0 | 0 | 0 | 240 | 37 | 0 | 0 | 3216 | 0.15 |
| tuned | ring | 64 | silent | 760 | 39 | 0 | 0 | 0 | 0 | 0 | 0 | 2917 | 1.55 |
| tuned | ring | 64 | nodefail | 657 | 32 | 0 | 0 | 32 | 32 | 0 | 0 | 2938 | 1.70 |
| tuned | ring | 64 | partition | 0 | 0 | 0 | 0 | 757 | 36 | 0 | 0 | 2942 | 1.83 |
| tuned | ring | 96 | silent | 1874 | 210 | 0 | 0 | 0 | 0 | 0 | 0 | 3257 | 2.45 |
| tuned | ring | 96 | nodefail | 1912 | 41 | 0 | 0 | 41 | 37 | 0 | 0 | 8065 | 4.91 |
| tuned | ring | 96 | partition | 0 | 0 | 0 | 0 | 1771 | 34 | 0 | 0 | 3317 | 0.91 |
| default | grid | 16 | silent | 6 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 29599 | 0.04 |
| default | grid | 16 | nodefail | 2 | 2 | 0 | 0 | 4 | 660 | 0 | 0 | 57286 | 0.05 |
| default | grid | 16 | partition | 0 | 0 | 0 | 0 | 28 | 1087 | 0 | 0 | 29682 | 0.18 |
| default | grid | 32 | silent | 22 | 37 | 0 | 0 | 0 | 0 | 0 | 0 | 28733 | 0.25 |
| default | grid | 32 | nodefail | 12 | 39 | 0 | 0 | 8 | 39 | 0 | 0 | 57789 | 0.17 |
| default | grid | 32 | partition | 0 | 0 | 0 | 0 | 144 | 1078 | 4 | 1 | 58174 | 0.54 |
| default | grid | 64 | silent | 28 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 29470 | 1.02 |
| default | grid | 64 | nodefail | 20 | 1052 | 0 | 0 | 19 | 40 | 0 | 0 | 58736 | 0.89 |
| default | grid | 64 | partition | 0 | 0 | 0 | 0 | 195 | 1050 | 0 | 0 | 30540 | 0.28 |
| default | grid | 96 | silent | 52 | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 30835 | 0.57 |
| default | grid | 96 | nodefail | 75 | 1089 | 0 | 0 | 14 | 1069 | 0 | 0 | 58833 | 0.35 |
| default | grid | 96 | partition | 0 | 0 | 0 | 0 | 1250 | 1080 | 0 | 0 | 59762 | 0.35 |
| default | mesh | 16 | silent | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 29634 | 0.20 |
| default | mesh | 16 | nodefail | 0 | 0 | 0 | 0 | 3 | 37 | 0 | 0 | 57913 | 0.04 |
| default | mesh | 16 | partition | 1 | 998 | 0 | 0 | 58 | 30352 | 38 | 1 | 59820 | 0.10 |
| default | mesh | 32 | silent | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 28331 | 0.12 |
| default | mesh | 32 | nodefail | 0 | 0 | 0 | 0 | 1 | 529 | 0 | 0 | 57493 | 0.11 |
| default | mesh | 32 | partition | 0 | 0 | 0 | 0 | 130 | 29984 | 15 | 2 | 59421 | 0.10 |
| default | mesh | 64 | silent | 33 | 972 | 0 | 0 | 0 | 0 | 0 | 0 | 29792 | 0.85 |
| default | mesh | 64 | nodefail | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 58023 | 0.46 |
| default | mesh | 64 | partition | 85 | 30048 | 30 | 4 | 801 | 30469 | 274 | 4 | 60734 | 1.14 |
| default | mesh | 96 | silent | 14 | 1033 | 2 | 1 | 0 | 0 | 0 | 0 | 31060 | 1.38 |
| default | mesh | 96 | nodefail | 25 | 1021 | 0 | 0 | 20 | 1021 | 0 | 0 | 58246 | 0.69 |
| default | mesh | 96 | partition | 392 | 30314 | 168 | 4 | 2188 | 30299 | 603 | 4 | 59962 | 0.45 |
| default | ring | 16 | silent | 56 | 36 | 0 | 0 | 0 | 0 | 0 | 0 | 29103 | 0.11 |
| default | ring | 16 | nodefail | 42 | 36 | 0 | 0 | 7 | 36 | 0 | 0 | 58264 | 0.01 |
| default | ring | 16 | partition | 0 | 0 | 0 | 0 | 55 | 37 | 0 | 0 | 29793 | 0.04 |
| default | ring | 32 | silent | 240 | 1077 | 0 | 0 | 0 | 0 | 0 | 0 | 29552 | 0.18 |
| default | ring | 32 | nodefail | 210 | 1038 | 0 | 0 | 20 | 1014 | 0 | 0 | 29225 | 0.02 |
| default | ring | 32 | partition | 0 | 0 | 0 | 0 | 240 | 36 | 0 | 0 | 29064 | 0.05 |
| default | ring | 64 | silent | 846 | 36 | 0 | 0 | 0 | 0 | 0 | 0 | 30919 | 0.37 |
| default | ring | 64 | nodefail | 847 | 1065 | 0 | 0 | 30 | 37 | 0 | 0 | 58737 | 0.13 |
| default | ring | 64 | partition | 0 | 0 | 0 | 0 | 963 | 39 | 0 | 0 | 29416 | 0.26 |
| default | ring | 96 | silent | 2210 | 36 | 0 | 0 | 0 | 0 | 0 | 0 | 28577 | 0.47 |
| default | ring | 96 | nodefail | 1898 | 37 | 0 | 0 | 81 | 1011 | 0 | 0 | 30303 | 0.40 |
| default | ring | 96 | partition | 0 | 0 | 0 | 0 | 2095 | 36 | 0 | 0 | 29520 | 0.26 |

## Model beside the bed (median of 5 model seeds, every destination)

| run | live loops bed / model | live longest ms bed / model | gone loops bed / model | gone longest ms bed / model | reconverged ms bed / model | SPF runs per node bed / model |
|---|---|---|---|---|---|---|
| grid16-nodefail-default-s1 | 2 / 4 | 2 / 2 | 4 / 3 | 660 / 2 | 57286 / 58504 | 2.44 / 2.94 |
| grid16-nodefail-tuned-s1 | 4 / 4 | 264 / 2 | 4 / 2 | 269 / 2 | 2637 / 8510 | 1.94 / 2.0 |
| grid16-partition-default-s1 | 0 / 0 | 0 / 0 | 28 / 36 | 1087 / 1022 | 29682 / 30979 | 2.56 / 3.5 |
| grid16-partition-tuned-s1 | 0 / 0 | 0 / 0 | 38 / 34 | 352 / 259 | 8049 / 8054 | 2.5 / 2.5 |
| grid16-silent-default-s1 | 6 / 8 | 1 / 2 | 0 / 0 | 0 / 0 | 29599 / 28666 | 2.0 / 2.0 |
| grid16-silent-tuned-s1 | 8 / 8 | 3 / 2 | 0 / 0 | 0 / 0 | 3127 / 2856 | 2.19 / 2.0 |
| grid32-nodefail-default-s1 | 12 / 24 | 39 / 36 | 8 / 9 | 39 / 36 | 57789 / 57578 | 2.0 / 2.97 |
| grid32-nodefail-tuned-s1 | 27 / 24 | 38 / 234 | 4 / 11 | 34 / 259 | 8387 / 8535 | 2.16 / 2.97 |
| grid32-partition-default-s1 | 0 / 0 | 0 / 0 | 144 / 187 | 1078 / 1383 | 58174 / 59152 | 3.41 / 5.0 |
| grid32-partition-tuned-s1 | 0 / 0 | 0 / 0 | 78 / 148 | 259 / 5051 | 9048 / 8059 | 2.62 / 3.09 |
| grid32-silent-default-s1 | 22 / 22 | 37 / 36 | 0 / 0 | 0 / 0 | 28733 / 28668 | 2.47 / 2.0 |
| grid32-silent-tuned-s1 | 22 / 22 | 287 / 36 | 0 / 0 | 0 / 0 | 3196 / 2857 | 2.12 / 2.0 |
| grid64-nodefail-default-s1 | 20 / 24 | 1052 / 2 | 19 / 7 | 40 / 2 | 58736 / 58504 | 2.98 / 2.98 |
| grid64-nodefail-tuned-s1 | 20 / 24 | 10 / 222 | 10 / 5 | 44 / 222 | 3112 / 8511 | 2.11 / 2.0 |
| grid64-partition-default-s1 | 0 / 0 | 0 / 0 | 195 / 344 | 1050 / 36 | 30540 / 30860 | 2.66 / 3.5 |
| grid64-partition-tuned-s1 | 0 / 0 | 0 / 0 | 551 / 280 | 509 / 5051 | 8142 / 8036 | 3.05 / 3.56 |
| grid64-silent-default-s1 | 28 / 32 | 6 / 2 | 0 / 0 | 0 / 0 | 29470 / 28670 | 2.0 / 2.0 |
| grid64-silent-tuned-s1 | 24 / 32 | 14 / 224 | 0 / 0 | 0 / 0 | 3329 / 2860 | 2.03 / 2.0 |
| grid96-nodefail-default-s1 | 75 / 80 | 1089 / 36 | 14 / 18 | 1069 / 36 | 58833 / 57579 | 2.0 / 2.99 |
| grid96-nodefail-tuned-s1 | 59 / 91 | 44 / 220 | 7 / 18 | 32 / 226 | 8647 / 8536 | 5.07 / 2.99 |
| grid96-partition-default-s1 | 0 / 0 | 0 / 0 | 1250 / 1651 | 1080 / 1064 | 59762 / 58834 | 4.25 / 4.83 |
| grid96-partition-tuned-s1 | 0 / 0 | 0 / 0 | 932 / 781 | 5325 / 5260 | 8107 / 8036 | 4.0 / 3.81 |
| grid96-silent-default-s1 | 52 / 80 | 12 / 2 | 0 / 0 | 0 / 0 | 30835 / 28672 | 2.0 / 2.0 |
| grid96-silent-tuned-s1 | 51 / 80 | 17 / 224 | 0 / 0 | 0 / 0 | 3306 / 2862 | 2.0 / 2.0 |
| mesh16-nodefail-default-s1 | 0 / 0 | 0 / 0 | 3 / 3 | 37 / 36 | 57913 / 58684 | 2.06 / 2.0 |
| mesh16-nodefail-tuned-s1 | 0 / 0 | 0 / 0 | 2 / 3 | 36 / 36 | 2695 / 8126 | 1.81 / 2.0 |
| mesh16-partition-default-s1 | 1 / 2 | 998 / 28903 | 58 / 73 | 30352 / 30085 | 59820 / 59990 | 4.06 / 6.12 |
| mesh16-partition-tuned-s1 | 1 / 1 | 228 / 5051 | 49 / 22 | 5956 / 5287 | 8536 / 8164 | 3.56 / 3.88 |
| mesh16-silent-default-s1 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 29634 / 28664 | 2.0 / 2.0 |
| mesh16-silent-tuned-s1 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 2995 / 2854 | 2.0 / 1.94 |
| mesh32-nodefail-default-s1 | 0 / 0 | 0 / 0 | 1 / 2 | 529 / 2 | 57493 / 57986 | 2.34 / 3.84 |
| mesh32-nodefail-tuned-s1 | 0 / 0 | 0 / 0 | 1 / 2 | 241 / 2 | 8732 / 8485 | 2.22 / 2.97 |
| mesh32-partition-default-s1 | 0 / 0 | 0 / 0 | 130 / 118 | 29984 / 28924 | 59421 / 59615 | 4.88 / 4.62 |
| mesh32-partition-tuned-s1 | 0 / 0 | 0 / 0 | 135 / 66 | 5345 / 5201 | 8716 / 8924 | 3.66 / 3.28 |
| mesh32-silent-default-s1 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 28331 / 28629 | 2.0 / 2.0 |
| mesh32-silent-tuned-s1 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 2877 / 2674 | 2.0 / 2.0 |
| mesh64-nodefail-default-s1 | 0 / 0 | 0 / 0 | 0 / 1 | 0 / 2 | 58023 / 58504 | 2.0 / 2.98 |
| mesh64-nodefail-tuned-s1 | 0 / 0 | 0 / 0 | 1 / 1 | 1 / 228 | 2726 / 8511 | 1.94 / 2.0 |
| mesh64-partition-default-s1 | 85 / 97 | 30048 / 30361 | 801 / 1017 | 30469 / 31147 | 60734 / 60780 | 6.22 / 7.14 |
| mesh64-partition-tuned-s1 | 82 / 70 | 5154 / 5416 | 305 / 318 | 5351 / 5458 | 8941 / 8558 | 3.89 / 3.98 |
| mesh64-silent-default-s1 | 33 / 58 | 972 / 36 | 0 / 0 | 0 / 0 | 29792 / 28702 | 2.17 / 2.0 |
| mesh64-silent-tuned-s1 | 54 / 58 | 42 / 224 | 0 / 0 | 0 / 0 | 3250 / 2892 | 2.17 / 1.97 |
| mesh96-nodefail-default-s1 | 25 / 33 | 1021 / 36 | 20 / 26 | 1021 / 36 | 58246 / 58505 | 3.64 / 2.99 |
| mesh96-nodefail-tuned-s1 | 29 / 28 | 89 / 222 | 8 / 15 | 55 / 228 | 8208 / 8511 | 2.23 / 2.0 |
| mesh96-partition-default-s1 | 392 / 332 | 30314 / 30533 | 2188 / 2163 | 30299 / 30532 | 59962 / 60897 | 7.53 / 7.79 |
| mesh96-partition-tuned-s1 | 346 / 289 | 5365 / 5253 | 925 / 955 | 5365 / 5345 | 8107 / 7904 | 4.0 / 4.4 |
| mesh96-silent-default-s1 | 14 / 22 | 1033 / 36 | 0 / 0 | 0 / 0 | 31060 / 28702 | 2.0 / 2.0 |
| mesh96-silent-tuned-s1 | 17 / 22 | 150 / 223 | 0 / 0 | 0 / 0 | 2913 / 2892 | 1.94 / 1.99 |
| ring16-nodefail-default-s1 | 42 / 42 | 36 / 36 | 7 / 7 | 36 / 36 | 58264 / 58684 | 2.0 / 2.0 |
| ring16-nodefail-tuned-s1 | 36 / 42 | 36 / 36 | 6 / 7 | 36 / 36 | 8580 / 8182 | 2.0 / 2.0 |
| ring16-partition-default-s1 | 0 / 0 | 0 / 0 | 55 / 56 | 37 / 36 | 29793 / 29986 | 2.0 / 2.0 |
| ring16-partition-tuned-s1 | 0 / 0 | 0 / 0 | 56 / 55 | 37 / 36 | 2852 / 3302 | 2.0 / 1.88 |
| ring16-silent-default-s1 | 56 / 56 | 36 / 36 | 0 / 0 | 0 / 0 | 29103 / 28675 | 2.0 / 2.0 |
| ring16-silent-tuned-s1 | 31 / 56 | 223 / 186 | 0 / 0 | 0 / 0 | 2683 / 2865 | 1.62 / 1.69 |
| ring32-nodefail-default-s1 | 210 / 210 | 1038 / 36 | 20 / 15 | 1014 / 36 | 29225 / 58684 | 1.97 / 2.0 |
| ring32-nodefail-tuned-s1 | 174 / 210 | 198 / 36 | 15 / 15 | 36 / 36 | 3343 / 8211 | 1.88 / 2.0 |
| ring32-partition-default-s1 | 0 / 0 | 0 / 0 | 240 / 240 | 36 / 36 | 29064 / 30000 | 2.0 / 2.0 |
| ring32-partition-tuned-s1 | 0 / 0 | 0 / 0 | 240 / 230 | 37 / 36 | 3216 / 3316 | 2.0 / 1.84 |
| ring32-silent-default-s1 | 240 / 240 | 1077 / 36 | 0 / 0 | 0 / 0 | 29552 / 29459 | 2.0 / 2.0 |
| ring32-silent-tuned-s1 | 128 / 240 | 229 / 186 | 0 / 0 | 0 / 0 | 2684 / 2879 | 1.69 / 1.69 |
| ring64-nodefail-default-s1 | 847 / 930 | 1065 / 36 | 30 / 31 | 37 / 36 | 58737 / 58685 | 2.0 / 2.0 |
| ring64-nodefail-tuned-s1 | 657 / 930 | 32 / 36 | 32 / 31 | 32 / 36 | 2938 / 8269 | 1.72 / 1.98 |
| ring64-partition-default-s1 | 0 / 0 | 0 / 0 | 963 / 992 | 39 / 36 | 29416 / 30030 | 2.0 / 2.0 |
| ring64-partition-tuned-s1 | 0 / 0 | 0 / 0 | 757 / 992 | 36 / 36 | 2942 / 3345 | 2.0 / 1.86 |
| ring64-silent-default-s1 | 846 / 992 | 36 / 1054 | 0 / 0 | 0 / 0 | 30919 / 29525 | 2.0 / 2.0 |
| ring64-silent-tuned-s1 | 760 / 992 | 39 / 186 | 0 / 0 | 0 / 0 | 2917 / 2908 | 1.67 / 1.86 |
| ring96-nodefail-default-s1 | 1898 / 2162 | 37 / 36 | 81 / 47 | 1011 / 36 | 30303 / 58685 | 1.99 / 2.0 |
| ring96-nodefail-tuned-s1 | 1912 / 2162 | 41 / 36 | 41 / 47 | 37 / 36 | 8065 / 8327 | 2.0 / 1.99 |
| ring96-partition-default-s1 | 0 / 0 | 0 / 0 | 2095 / 2256 | 36 / 36 | 29520 / 30059 | 2.0 / 2.0 |
| ring96-partition-tuned-s1 | 0 / 0 | 0 / 0 | 1771 / 2256 | 34 / 36 | 3317 / 8174 | 1.79 / 1.91 |
| ring96-silent-default-s1 | 2210 / 2256 | 36 / 1002 | 0 / 0 | 0 / 0 | 28577 / 29562 | 2.0 / 2.0 |
| ring96-silent-tuned-s1 | 1874 / 2202 | 210 / 199 | 0 / 0 | 0 / 0 | 3257 / 2937 | 1.79 / 1.91 |

## E1 IS-IS (MODELED)

| timers | topo | N | event | seeds | live loops /64 dests | live longest ms | live past >=1 SPF | most SPFs survived | gone loops | gone longest ms | reconverged ms | SPF ms (modeled) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tuned | grid | 1000 | silent | 3 | 64 | 235 | 0 | 0 | 0 | 0 | 2944 | 11 |
| tuned | grid | 1000 | nodefail | 3 | 90 | 224 | 0 | 0 | 0 | 0 | 8609 | 11 |
| tuned | grid | 1000 | partition | 3 | 0 | 0 | 0 | 0 | 1517 | 5082 | 8062 | 11 |
| tuned | grid | 3000 | silent | 3 | 56 | 222 | 0 | 0 | 0 | 0 | 2940 | 38 |
| tuned | grid | 3000 | nodefail | 3 | 146 | 5026 | 0 | 0 | 0 | 0 | 8650 | 38 |
| tuned | grid | 3000 | partition | 3 | 0 | 0 | 0 | 0 | 3269 | 5649 | 8061 | 38 |
| tuned | grid | 10000 | silent | 3 | 50 | 230 | 0 | 0 | 0 | 0 | 3140 | 145 |
| tuned | grid | 10000 | nodefail | 3 | 49 | 2 | 0 | 0 | 0 | 0 | 8739 | 145 |
| tuned | grid | 10000 | partition | 3 | 0 | 0 | 0 | 0 | 6541 | 5220 | 8205 | 145 |
| tuned | grid | 30000 | silent | 3 | 0 | 2 | 0 | 0 | 0 | 0 | 3393 | 488 |
| tuned | grid | 30000 | nodefail | 3 | 0 | 2 | 0 | 0 | 0 | 0 | 9304 | 488 |
| tuned | grid | 100000 | silent | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 4938 | 1818 |
| tuned | grid | 100000 | nodefail | 3 | 0 | 2 | 0 | 0 | 0 | 0 | 11160 | 1818 |
| tuned | mesh | 1000 | silent | 3 | 23 | 224 | 0 | 0 | 0 | 0 | 2919 | 11 |
| tuned | mesh | 1000 | nodefail | 3 | 18 | 212 | 0 | 0 | 0 | 0 | 8974 | 11 |
| tuned | mesh | 1000 | partition | 3 | 3615 | 5622 | 274 | 5 | 4467 | 5992 | 9005 | 11 |
| tuned | mesh | 3000 | silent | 3 | 23 | 215 | 0 | 0 | 0 | 0 | 2960 | 38 |
| tuned | mesh | 3000 | nodefail | 3 | 0 | 35 | 0 | 0 | 0 | 0 | 8595 | 38 |
| tuned | mesh | 10000 | silent | 3 | 18 | 224 | 0 | 0 | 0 | 0 | 3056 | 145 |
| tuned | mesh | 10000 | nodefail | 3 | 7 | 36 | 0 | 0 | 0 | 0 | 8951 | 145 |
| tuned | mesh | 30000 | silent | 3 | 5 | 226 | 0 | 0 | 0 | 0 | 3414 | 488 |
| tuned | mesh | 30000 | nodefail | 3 | 8 | 245 | 0 | 0 | 0 | 0 | 9669 | 488 |
| tuned | mesh | 100000 | silent | 3 | 2 | 160 | 0 | 0 | 0 | 0 | 4820 | 1818 |
| tuned | mesh | 100000 | nodefail | 3 | 0 | 36 | 0 | 0 | 0 | 0 | 11301 | 1818 |
| tuned | ring | 1000 | silent | 3 | 16286 | 199 | 0 | 0 | 0 | 0 | 3738 | 11 |
| tuned | ring | 1000 | nodefail | 3 | 15538 | 4802 | 0 | 0 | 0 | 0 | 9020 | 11 |
| tuned | ring | 1000 | partition | 3 | 0 | 0 | 0 | 0 | 21977 | 36 | 9047 | 11 |
| tuned | ring | 3000 | silent | 3 | 48863 | 198 | 0 | 0 | 0 | 0 | 5493 | 38 |
| tuned | ring | 3000 | nodefail | 3 | 47010 | 4802 | 0 | 0 | 0 | 0 | 10865 | 38 |
| tuned | ring | 3000 | partition | 3 | 0 | 0 | 0 | 0 | 70966 | 36 | 10879 | 38 |
| tuned | ring | 10000 | silent | 3 | 169740 | 36 | 0 | 0 | 0 | 0 | 11723 | 145 |
| tuned | ring | 10000 | nodefail | 3 | 150151 | 4802 | 0 | 0 | 0 | 0 | 17162 | 145 |
| tuned | ring | 10000 | partition | 3 | 0 | 0 | 0 | 0 | 236996 | 36 | 17309 | 145 |
| default | grid | 1000 | silent | 3 | 62 | 2 | 0 | 0 | 0 | 0 | 29858 | 11 |
| default | grid | 1000 | nodefail | 3 | 88 | 2 | 0 | 0 | 0 | 0 | 58763 | 11 |
| default | grid | 1000 | partition | 3 | 0 | 0 | 0 | 0 | 3241 | 2082 | 59390 | 11 |
| default | grid | 3000 | silent | 3 | 54 | 2 | 0 | 0 | 0 | 0 | 29832 | 38 |
| default | grid | 3000 | nodefail | 3 | 156 | 1081 | 0 | 0 | 0 | 0 | 58805 | 38 |
| default | grid | 3000 | partition | 3 | 0 | 0 | 0 | 0 | 19770 | 2050 | 59590 | 38 |
| default | grid | 10000 | silent | 3 | 49 | 2 | 0 | 0 | 0 | 0 | 30054 | 145 |
| default | grid | 10000 | nodefail | 3 | 49 | 2 | 0 | 0 | 0 | 0 | 58749 | 145 |
| default | grid | 10000 | partition | 3 | 0 | 0 | 0 | 0 | 6055 | 1078 | 31551 | 145 |
| default | grid | 30000 | silent | 3 | 0 | 2 | 0 | 0 | 0 | 0 | 30384 | 488 |
| default | grid | 30000 | nodefail | 3 | 0 | 2 | 0 | 0 | 0 | 0 | 59459 | 488 |
| default | grid | 100000 | silent | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 31975 | 1818 |
| default | grid | 100000 | nodefail | 3 | 0 | 2 | 0 | 0 | 0 | 0 | 61315 | 1818 |
| default | mesh | 1000 | silent | 3 | 23 | 36 | 0 | 0 | 0 | 0 | 29833 | 11 |
| default | mesh | 1000 | nodefail | 3 | 22 | 1120 | 0 | 1 | 0 | 0 | 58127 | 11 |
| default | mesh | 1000 | partition | 3 | 4449 | 31473 | 1720 | 9 | 14340 | 31658 | 60940 | 11 |
| default | mesh | 3000 | silent | 3 | 23 | 36 | 0 | 0 | 0 | 0 | 29874 | 38 |
| default | mesh | 3000 | nodefail | 3 | 0 | 1043 | 0 | 0 | 0 | 0 | 58805 | 38 |
| default | mesh | 10000 | silent | 3 | 18 | 36 | 0 | 0 | 0 | 0 | 29970 | 145 |
| default | mesh | 10000 | nodefail | 3 | 7 | 36 | 0 | 0 | 0 | 0 | 59388 | 145 |
| default | mesh | 30000 | silent | 3 | 5 | 32 | 0 | 0 | 0 | 0 | 30331 | 488 |
| default | mesh | 30000 | nodefail | 3 | 5 | 1058 | 0 | 0 | 0 | 0 | 59459 | 488 |
| default | mesh | 100000 | silent | 3 | 2 | 36 | 0 | 0 | 0 | 0 | 31880 | 1818 |
| default | mesh | 100000 | nodefail | 3 | 0 | 36 | 0 | 0 | 0 | 0 | 61737 | 1818 |
| default | ring | 1000 | silent | 3 | 16305 | 1002 | 0 | 0 | 0 | 0 | 30664 | 11 |
| default | ring | 1000 | nodefail | 3 | 15579 | 1002 | 0 | 0 | 0 | 0 | 59185 | 11 |
| default | ring | 1000 | partition | 3 | 0 | 0 | 0 | 0 | 19352 | 1002 | 31331 | 11 |
| default | ring | 3000 | silent | 3 | 48882 | 1002 | 0 | 0 | 0 | 0 | 32198 | 38 |
| default | ring | 3000 | nodefail | 3 | 47014 | 1002 | 0 | 0 | 0 | 0 | 59228 | 38 |
| default | ring | 3000 | partition | 3 | 0 | 0 | 0 | 0 | 68569 | 1015 | 32854 | 38 |
| default | ring | 10000 | silent | 3 | 169739 | 1002 | 0 | 0 | 0 | 0 | 38556 | 145 |
| default | ring | 10000 | nodefail | 3 | 150163 | 1002 | 0 | 0 | 0 | 0 | 59388 | 145 |
| default | ring | 10000 | partition | 3 | 0 | 0 | 0 | 0 | 236694 | 1024 | 39285 | 145 |

## E2 SPB agreement (MODELED, digest per SPF run)

| timers | topo | N | event | views/bridge p50 (max) | bridges detecting | mismatches per detecting bridge p50 (max) | msgs per port max | ports agreed by ms | held ms per port |
|---|---|---|---|---|---|---|---|---|---|
| tuned | grid | 1000 | silent | 2 (2) | 100% | 4 (4) | 8 | 2997 | 258 |
| tuned | grid | 1000 | nodefail | 3 (4) | 100% | 6 (8) | 16 | 7956 | 633 |
| tuned | grid | 1000 | partition | 4 (5) | 100% | 8 (13) | 20 | 8080 | 5578 |
| tuned | grid | 3000 | silent | 2 (2) | 100% | 4 (4) | 8 | 3065 | 257 |
| tuned | grid | 3000 | nodefail | 3 (4) | 100% | 6 (8) | 16 | 8028 | 630 |
| tuned | grid | 3000 | partition | 5 (5) | 100% | 10 (14) | 20 | 8066 | 5660 |
| tuned | grid | 10000 | silent | 2 (2) | 100% | 4 (4) | 8 | 3318 | 257 |
| tuned | grid | 10000 | nodefail | 2 (3) | 100% | 4 (6) | 12 | 8547 | 575 |
| tuned | grid | 10000 | partition | 5 (5) | 100% | 10 (14) | 20 | 8215 | 5679 |
| tuned | grid | 30000 | silent | 2 (2) | 100% | 4 (4) | 8 | 3769 | 256 |
| tuned | grid | 30000 | nodefail | 3 (4) | 100% | 6 (8) | 16 | 8729 | 631 |
| tuned | grid | 100000 | silent | 2 (2) | 100% | 4 (4) | 8 | 5445 | 256 |
| tuned | grid | 100000 | nodefail | 3 (4) | 100% | 6 (8) | 16 | 10408 | 629 |
| tuned | mesh | 1000 | silent | 2 (2) | 100% | 2 (8) | 8 | 3036 | 280 |
| tuned | mesh | 1000 | nodefail | 2 (3) | 100% | 3 (16) | 12 | 7917 | 600 |
| tuned | mesh | 1000 | partition | 5 (5) | 96% | 10 (37) | 20 | 8947 | 5644 |
| tuned | mesh | 3000 | silent | 2 (2) | 100% | 2 (11) | 8 | 3063 | 238 |
| tuned | mesh | 3000 | nodefail | 2 (3) | 100% | 2 (14) | 12 | 8181 | 430 |
| tuned | mesh | 10000 | silent | 2 (2) | 100% | 2 (12) | 8 | 7890 | 277 |
| tuned | mesh | 10000 | nodefail | 2 (2) | 100% | 2 (9) | 8 | 8404 | 257 |
| tuned | mesh | 30000 | silent | 2 (2) | 100% | 2 (10) | 8 | 3553 | 277 |
| tuned | mesh | 30000 | nodefail | 2 (3) | 100% | 3 (13) | 12 | 3841 | 304 |
| tuned | mesh | 100000 | silent | 2 (2) | 100% | 2 (12) | 8 | 4918 | 302 |
| tuned | mesh | 100000 | nodefail | 2 (2) | 100% | 2 (10) | 8 | 10115 | 571 |
| tuned | ring | 1000 | silent | 2 (2) | 100% | 2 (2) | 8 | 9550 | 4579 |
| tuned | ring | 1000 | nodefail | 2 (2) | 100% | 2 (2) | 8 | 10013 | 4721 |
| tuned | ring | 1000 | partition | 2 (2) | 100% | 2 (2) | 8 | 9052 | 3005 |
| tuned | ring | 3000 | silent | 2 (2) | 100% | 2 (2) | 8 | 13180 | 7272 |
| tuned | ring | 3000 | nodefail | 2 (2) | 100% | 2 (2) | 8 | 13643 | 7327 |
| tuned | ring | 3000 | partition | 2 (2) | 100% | 2 (2) | 8 | 10891 | 5493 |
| tuned | ring | 10000 | silent | 2 (2) | 100% | 2 (3) | 8 | 21822 | 11679 |
| tuned | ring | 10000 | nodefail | 2 (2) | 100% | 2 (3) | 8 | 22031 | 11711 |
| tuned | ring | 10000 | partition | 2 (2) | 100% | 2 (2) | 8 | 17345 | 9293 |
| default | grid | 1000 | silent | 2 (2) | 100% | 4 (4) | 8 | 29911 | 1286 |
| default | grid | 1000 | nodefail | 3 (4) | 100% | 6 (10) | 16 | 30869 | 2411 |
| default | grid | 1000 | partition | 6 (6) | 100% | 11 (17) | 24 | 59409 | 30835 |
| default | grid | 3000 | silent | 2 (2) | 100% | 4 (4) | 8 | 29979 | 1285 |
| default | grid | 3000 | nodefail | 3 (4) | 100% | 6 (10) | 16 | 30933 | 2234 |
| default | grid | 3000 | partition | 5 (5) | 100% | 10 (12) | 20 | 59602 | 31094 |
| default | grid | 10000 | silent | 2 (2) | 100% | 4 (5) | 8 | 30233 | 1284 |
| default | grid | 10000 | nodefail | 3 (3) | 100% | 6 (6) | 12 | 31386 | 2441 |
| default | grid | 10000 | partition | 4 (5) | 100% | 8 (14) | 20 | 31562 | 3162 |
| default | grid | 30000 | silent | 2 (2) | 100% | 4 (6) | 8 | 30684 | 1284 |
| default | grid | 30000 | nodefail | 3 (4) | 100% | 6 (10) | 16 | 31642 | 2001 |
| default | grid | 100000 | silent | 2 (2) | 100% | 4 (6) | 8 | 32329 | 1284 |
| default | grid | 100000 | nodefail | 3 (4) | 100% | 6 (10) | 16 | 33229 | 1948 |
| default | mesh | 1000 | silent | 2 (2) | 100% | 2 (6) | 8 | 29950 | 1302 |
| default | mesh | 1000 | nodefail | 3 (4) | 100% | 4 (14) | 16 | 30906 | 2066 |
| default | mesh | 1000 | partition | 9 (10) | 96% | 14 (60) | 40 | 60985 | 33387 |
| default | mesh | 3000 | silent | 2 (2) | 100% | 2 (8) | 8 | 29977 | 1271 |
| default | mesh | 3000 | nodefail | 2 (4) | 100% | 2 (11) | 16 | 30933 | 1715 |
| default | mesh | 10000 | silent | 2 (2) | 100% | 2 (9) | 8 | 30090 | 1307 |
| default | mesh | 10000 | nodefail | 2 (2) | 100% | 2 (9) | 8 | 30380 | 1251 |
| default | mesh | 30000 | silent | 2 (2) | 100% | 2 (9) | 8 | 30467 | 1304 |
| default | mesh | 30000 | nodefail | 2 (3) | 100% | 2 (12) | 12 | 31393 | 1252 |
| default | mesh | 100000 | silent | 2 (2) | 100% | 2 (9) | 8 | 31833 | 1303 |
| default | mesh | 100000 | nodefail | 2 (2) | 100% | 2 (10) | 8 | 32090 | 1191 |
| default | ring | 1000 | silent | 2 (2) | 100% | 2 (3) | 8 | 30667 | 1064 |
| default | ring | 1000 | nodefail | 2 (2) | 100% | 2 (3) | 8 | 31988 | 1669 |
| default | ring | 1000 | partition | 2 (2) | 100% | 2 (3) | 8 | 31335 | 1368 |
| default | ring | 3000 | silent | 2 (2) | 100% | 2 (3) | 8 | 34294 | 2863 |
| default | ring | 3000 | nodefail | 2 (2) | 100% | 2 (3) | 8 | 35619 | 3091 |
| default | ring | 3000 | partition | 2 (2) | 100% | 2 (3) | 8 | 32866 | 1823 |
| default | ring | 10000 | silent | 2 (2) | 100% | 2 (3) | 8 | 47015 | 9100 |
| default | ring | 10000 | nodefail | 2 (2) | 100% | 2 (3) | 8 | 48338 | 9150 |
| default | ring | 10000 | partition | 2 (2) | 100% | 2 (3) | 8 | 39320 | 4724 |

