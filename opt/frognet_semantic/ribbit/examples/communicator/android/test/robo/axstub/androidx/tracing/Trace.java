package androidx.tracing;
public final class Trace {
    public static void beginSection(String s) { android.os.Trace.beginSection(s.length() > 127 ? s.substring(0, 127) : s); }
    public static void endSection() { android.os.Trace.endSection(); }
    public static boolean isEnabled() { return false; }
    public static void forceEnableAppTracing() { }
}
