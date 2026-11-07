// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak.desktop;

/** [RT_APP_DESKTOP_V1] the entry point: JavaFX is on the class path (one folder of jars beside the app's own runtime),
 *  so main() is in a class that is not the Application itself. */
public final class Launcher {
    public static void main(String[] a) { javafx.application.Application.launch(Main.class, a); }
}
