// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.desktop;

/** [ADMIN_APP_V1] the admin app's entry point (JavaFX on the class path, as Launcher) */
public final class AdminLauncher {
    public static void main(String[] a) { javafx.application.Application.launch(AdminMain.class, a); }
}
