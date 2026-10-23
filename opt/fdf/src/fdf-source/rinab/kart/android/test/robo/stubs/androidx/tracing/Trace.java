package androidx.tracing;
/** stand-in for androidx.tracing.Trace: no tracing (a test on a JVM) */
public final class Trace { public static void beginSection(String s) { } public static void endSection() { } public static void forceEnableAppTracing() { } }
