// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.layer;

import net.frognet.openeoc.Layer;
import java.util.Map;

/** [RT_LAYERS_V1] the EOC: logged in (its user's role is the admin's "eoc", any case); it follows every team's RAM */
public final class EocLayer extends Layer {
    public String name() { return "eoc"; }
    public Map<String, Role> roles() { return roles("eoc", new Role("login", "eoc", true)); }
}
