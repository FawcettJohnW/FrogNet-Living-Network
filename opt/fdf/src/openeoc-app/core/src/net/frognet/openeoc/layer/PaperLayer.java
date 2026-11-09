// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.layer;

import net.frognet.openeoc.Layer;

import java.util.Map;

/** [FEMA_EOC_V1] EOCAdmin: the EOC's paperwork (ui/layers/paper.js: the ICS forms filled from the boards, the IAP, the
 *  after-action report and improvement plan). Logged in as an EOC user (role eoc, or a role in the EOC category); its
 *  forms are records on the boards (IcsLayer: forms, aar, ip). On the map as the EOC; it follows every team's RAM. */
public final class PaperLayer extends Layer {
    public String name() { return "paper"; }
    public Map<String, Role> roles() { return roles("eocadmin", new Role("login", "eoc", true)); }
}
