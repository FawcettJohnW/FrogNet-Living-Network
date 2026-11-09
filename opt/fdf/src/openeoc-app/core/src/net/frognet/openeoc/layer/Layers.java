// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.layer;

import net.frognet.openeoc.Layer;

import java.util.ArrayList;
import java.util.List;

/** [RT_LAYERS_V1] the core's layers by name, as an app lists them (the page's ui/layers/<name>.js are the same names) */
public final class Layers {
    public static final String ALL = "help,field,eoc,team,medical,units,public,ics,fema,paper";
    public static List<Layer> of(String names) {
        List<Layer> out = new ArrayList<Layer>();
        for (String n : (names == null || names.trim().isEmpty() ? ALL : names).split(",")) {
            n = n.trim();
            if (n.equals("help")) out.add(new HelpLayer());
            else if (n.equals("field")) out.add(new FieldLayer());
            else if (n.equals("eoc")) out.add(new EocLayer());
            else if (n.equals("team")) out.add(new TeamLayer());
            else if (n.equals("medical")) out.add(new MedicalLayer());
            else if (n.equals("units")) out.add(new UnitsLayer());
            else if (n.equals("public")) out.add(new PublicLayer());
            else if (n.equals("ics")) out.add(new IcsLayer());
            else if (n.equals("fema")) { }                   // [FEMA_EOC_V1] a page layer on the ics boards: no core of its own
            else if (n.equals("paper")) out.add(new PaperLayer());
            else if (!n.isEmpty()) throw new IllegalArgumentException("no layer " + n + " (the layers: " + ALL + ")");
        }
        return out;
    }
    private Layers() { }
}
