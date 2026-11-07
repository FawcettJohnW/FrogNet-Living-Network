// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak.layer;

import net.frognet.rangertrak.Layer;
import java.util.Map;

/** [RT_LAYERS_V1] [RT_SUITE_APPS_V1] the utility and service units: each connects as its unit's name and is on the map as
 *  its own kind */
public final class UnitsLayer extends Layer {
    public String name() { return "units"; }
    public Map<String, Role> roles() {
        return roles("water", new Role("unit", "water", false), "power", new Role("unit", "power", false),
                     "shelter", new Role("unit", "shelter", false), "phone", new Role("unit", "phone", false));
    }
}
