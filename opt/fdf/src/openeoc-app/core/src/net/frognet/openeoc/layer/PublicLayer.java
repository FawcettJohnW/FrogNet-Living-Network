// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.layer;

import net.frognet.openeoc.Layer;
import java.util.Map;

/** [RT_LAYERS_V1] the public: reads the world; its position and presence are not published; no chat */
public final class PublicLayer extends Layer {
    public String name() { return "public"; }
    public Map<String, Role> roles() { return roles("public", new Role("public", "", false)); }
}
