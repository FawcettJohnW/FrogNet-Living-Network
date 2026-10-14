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
# Oct 8 audit, part 1 (FNWP is the wire) -- verbatim from chat a3c5615d turn 31
p='build_magnum.js'; s=open(p,encoding='utf-8').read()
def sub(o,n):
    global s
    c=s.count(o); assert c==1,(o[:80],c); s=s.replace(o,n)
DEF=("FNWP is the wire: length-prefixed, opcode- and sequence-tagged frames carrying BLDC-1 exchanges over a socket it does not open. "
     "Whoever opens the socket decides where it goes \\u2014 under FD&F the transport opens one per machine to the RAM manager; "
     "FrogNet's compatibility path opens them node to node; an application may open its own, as the Communicator does for media. "
     "FNWP defines no application semantics, no routing and no connection.")
sub('t(" It is the dedicated persistent socket between a machine and the RAM manager. The transport establishes it during discovery or attachment. FNWP does not decide application semantics and does not define routing.")',
    't(" '+DEF[len("FNWP is the wire: "):].replace("length-prefixed","It is the wire: length-prefixed",1)+'")')
sub('bullet([ b("FNWP"), t(" \\u2014 the persistent socket/session carrying Ribbit operations between a machine and the RAM manager.") ]),',
    'bullet([ b("FNWP"), t(" \\u2014 the wire: framed BLDC-1 exchanges on a socket someone else opened. Under FD&F that socket runs from each machine to the RAM manager.") ]),')
sub('FNWP                   connection: persistent session','FNWP                   wire: frames on an open socket')
sub('gloss("FNWP", "The persistent framed session used between a participant and the service endpoint selected by the transport. BLDC-1 representations ride over it; the transport determines how the endpoint is reached.")',
    'gloss("FNWP", "'+DEF+'")')
sub('Current architecture uses FNWP as the transport-independent persistent-session layer.','Current architecture uses FNWP as the transport-independent wire: it frames exchanges on a socket and never opens one.')
sub('each machine connects outward over FNWP, and application','each machine opens one outbound socket to it, carrying FNWP, and application')
sub('FNWP for the persistent session, and the selected transport','FNWP for the wire, and the selected transport')
sub('while FNWP supplies the persistent framed session','while FNWP is the wire those exchanges travel on')
sub('every participant establishes an outbound FNWP connection to the RAM manager','every participant opens one outbound socket to the RAM manager and speaks FNWP on it')
sub("Establish the machine's FNWP socket to that RAM manager.","Open the machine's socket to that RAM manager; FNWP runs on it.")
sub('Persistent carriage belongs to FNWP.','The wire belongs to FNWP.')
sub('Liveness is the FNWP connection.',"Liveness is the machine's connection to the RAM manager.")
sub('FNWP provides the persistent framed session used by the current implementations.','FNWP is the wire the current implementations use: framed exchanges on a socket that the transport or application opened.')
sub('and FNWP sessions remain separate layers','and the FNWP wire remain separate layers')
sub('the standing FNWP session is the carriage underneath','FNWP frames on the standing socket are the carriage underneath')
sub("each participant's FNWP connection terminates at the RAM manager rather than at another participant","each participant's socket, and so its FNWP, terminates at the RAM manager rather than at another participant")
sub('FNWP carries it persistently, and','FNWP frames it onto a standing socket, and')
sub('FNWP session, and transport','FNWP wire, and transport')
sub('FNWP provides the persistent framed session that carries those representations','FNWP is the wire that frames those representations onto a socket it does not open')
sub('FNWP is the persistent session; FrogNet or FD&F supplies transport','FNWP is the wire; FrogNet or FD&F supplies transport')
sub('FNWP keeps the session standing so that semantic state can be reused across exchanges.','The socket under FNWP stays standing, so semantic state can be reused across exchanges.')
sub('two persistent FNWP planes, declared at connect','two persistent sockets, each carrying FNWP, declared at connect')
sub('FNWP owns the persistent session, and FrogNet','FNWP owns the wire, and FrogNet')
sub('Each machine establishes one outbound FNWP connection to the manager.','Each machine opens one outbound socket to the manager and speaks FNWP on it.')
sub("Open the machine's outbound FNWP session to that RAM manager.","Open the machine's outbound socket to that RAM manager; FNWP runs on it.")
sub('crosses an FD&F FNWP session to a RAM manager','crosses an FD&F socket to a RAM manager')
sub('t(" is the persistent framed session used underneath the semantic exchange. ','t(" is the wire underneath the semantic exchange: framed SAME/DIFF/FULL on a standing socket that the proxy opened. ')
open(p,'w',encoding='utf-8').write(s); print("ok part a")
