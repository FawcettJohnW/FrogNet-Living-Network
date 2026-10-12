package androidx.test.platform.io;
import android.net.Uri;
import java.io.*;
import java.util.*;
/** Test-only stand-in for FileTestStorage (whose Kotlin helper is not compiled here): nothing is stored. */
final class NoOpPlatformTestStorage implements PlatformTestStorage {
    public InputStream openInputFile(String p) throws FileNotFoundException { throw new FileNotFoundException(p); }
    public String getInputArg(String a) { return null; }
    public Map<String, String> getInputArgs() { return new HashMap<String, String>(); }
    public OutputStream openOutputFile(String p) { return new ByteArrayOutputStream(); }
    public OutputStream openOutputFile(String p, boolean append) { return new ByteArrayOutputStream(); }
    public void addOutputProperties(Map<String, Serializable> m) { }
    public Map<String, Serializable> getOutputProperties() { return new HashMap<String, Serializable>(); }
    public Uri getInputFileUri(String p) { return Uri.EMPTY; }
    public Uri getOutputFileUri(String p) { return Uri.EMPTY; }
    public boolean isTestStorageFilePath(String p) { return false; }
}
