################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# D -- "How CroakOS Was Discovered", John's text (2026-11-15), verbatim, in the front matter before "How This Book Is
# Shaped". The same text is on the site's About page; the conversion guide carries his paragraph before step 5e.
from patchlib import *

ANCHOR = '      H1("How This Book Is Shaped"),\n'
PARAS = [
    "The first is literal. Knowing how to find out is different from knowing. I did not begin with a finished architecture or an expert understanding of every field CroakOS would touch. I learned what was necessary as I went, applying the fundamentals of physics and computer science, building experiments, and examining what happened.",
    "When something failed, I investigated. When something worked, I tested it and measured it. When a working implementation revealed a better way to arrange the computation, I threw it away and built that instead. Working code was evidence and a means of discovery, not a reason to stop.",
    "That is how CroakOS emerged. The architecture developed alongside my understanding. Existing interfaces defined the behavior to preserve; existing tests and measured experiments judged whether each replacement did its job.",
    "AI made successive implementations affordable. I supplied the questions, the architectural decisions, and the judgment about what the results meant.",
    "“But it works” means there is something concrete to examine: running systems, source code, tests, measurements, and experiments somebody else can repeat.",
]
NEW = ('      frontTitle("How CroakOS Was Discovered"),\n'
       '      // [DISCOVERED_V1] John, 2026-11-15, verbatim\n'
       '      lead([ t("' + U("There are two things you should understand about this research:") + '") ]),\n'
       '      P([ b("' + U("1.  I have no idea what I’m doing.") + '") ]),\n'
       '      P([ b("' + U("2.  But it works.") + '") ]),\n'
       + "".join('      P([ t("' + U(p) + '") ]),\n' for p in PARAS))
sub(ANCHOR, NEW + ANCHOR)
done()
