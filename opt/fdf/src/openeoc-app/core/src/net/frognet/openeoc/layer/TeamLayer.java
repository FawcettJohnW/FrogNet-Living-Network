// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.layer;

import net.frognet.openeoc.Layer;
import java.util.Map;

/** [RT_LAYERS_V1] a team: it follows its own team's RAM (the team RAMs, hosting and placing a team are the base's) */
public final class TeamLayer extends Layer {
    public String name() { return "team"; }
    public Map<String, Role> roles() { return roles("team", new Role("team", "team", false)); }
}
