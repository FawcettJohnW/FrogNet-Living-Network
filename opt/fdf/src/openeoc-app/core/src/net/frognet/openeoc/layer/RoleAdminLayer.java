// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.layer;

import net.frognet.openeoc.Layer;

import java.util.HashMap;
import java.util.Map;

/** [ROLE_ADMIN_V1] (John 2026-11-10: "Create -Admin roles for everything that has a non-admin version. For now, it's just
 *  the RAM host") one Admin view per role that has instances: OpenEOC -- TeamAdmin, -- HospitalAdmin, -- AmbulanceAdmin,
 *  -- ShelterAdmin, -- PowerAdmin, -- WaterAdmin, -- TelecomAdmin (ui/layers/roleadmin.js). Logged in as a Command Center
 *  user; it writes the role's one configuration at the door (op=roleconf), which every instance of the role holds a read on. */
public final class RoleAdminLayer extends Layer {
    public static final String[] ROLES = {"team", "hospital", "ambulance", "shelter", "power", "water", "telecom"};
    public String name() { return "roleadmin"; }
    public Map<String, Role> roles() {
        Map<String, Role> m = new HashMap<String, Role>();
        for (String r : ROLES) m.put(r + "admin", new Role("login", "eoc", true));
        return m;
    }
}
