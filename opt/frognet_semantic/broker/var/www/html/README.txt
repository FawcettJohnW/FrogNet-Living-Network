FrogNet Living Network — website install bundle
================================================

Complete static site. Every page is self-contained HTML plus the shared runtime
(support.js), styles (site.css), and behavior (site.js). No build step, no server
framework, no database required for the site itself.

INSTALL
-------
0. DOCTRINE.txt, CHANGES.txt, and checksite.py are build/maintenance files —
   do NOT copy them to the public web root. Keep them with the bundle.
1. Unpack into your web root (e.g. /var/www/fawcettinnovations.com).
2. Serve with any static web server (Apache, nginx, Caddy, or `python3 -m http.server`).
   Open index.dc.html as the home page.
3. All links are relative, so it works from a subfolder (/newsite) or the domain root.

*** DROP IN YOUR VIDEOS ***
The Demonstrations page and The Claim page play two real captures from media/.
Copy your two files into media/ with these EXACT names:
    media/communicator-ladder.mp4   ← "Bandwidth Experiments" (the ladder run)
    media/communicator-proof.mp4    ← "Proof it works" (HD baseline over 900 MHz)
Branded first-run poster frames are already in media/ and show until the files land.
Full details in media/README.txt.

HOME
----
  index.dc.html

THE ARGUMENT (engineer path)
----------------------------
  the-claim.dc.html   — "Two assumptions the field got wrong" + both proof videos
  why.dc.html         — Why FrogNet exists: seven engineering observations
  demonstrations.dc.html — the five-demo sequence, flagship videos + engineering facts

CORE BOOKS
----------
  How to Think Like a Frog.dc.html   — the "why" book
  Magnum Croakus.html                — "How to Work Like a Frog", the build manual
  Magnum Croakus-print.html          — print-optimized edition

MAIN PAGES
----------
  products.dc.html   use-cases.dc.html   library.dc.html   track-record.dc.html
  services.dc.html   about.dc.html       privacy.dc.html
  contact.dc.html    license.dc.html     licensing.dc.html

LIBRARY / ESSAYS
----------------
  The 13 Shifts, REST vs UnREST, UGV Swarm Autonomy (each .dc.html + a self-contained
  "(standalone).html"), UnREST Programming Models (+print), the Starve Test article
  + announcement, FrogNet for FarSight UGVs, the FrogNet Living Network deck.

RUNTIME / ASSETS (do not remove)
--------------------------------
  support.js  site.css  site.js  deck-stage.js
  deck-scenes.jsx  animations.jsx  shifts-app.jsx  shifts-video.jsx
  assets/  (logos and marks)   media/  (videos + posters)

OPTIONAL — LICENSE FORM BACKEND
-------------------------------
  license_endpoint.py is an optional backend for the license request form. The site
  works without it: contact.dc.html / license.dc.html compose the request as an email
  in the visitor's own mail client, so nothing is stored unless you wire this in.

NOTES
-----
- All web fonts ship locally in assets/fonts/ (loaded via site.css); no external
  font requests. The site renders identically offline.
- Until the two .mp4 files are in media/, the browser console logs a failed-media
  warning for each — expected, and the poster frames cover it.
