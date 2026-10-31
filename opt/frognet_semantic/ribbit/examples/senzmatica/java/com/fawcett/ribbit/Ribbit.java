package com.fawcett.ribbit;
import java.nio.charset.StandardCharsets;
import java.util.Objects;
import java.util.function.Consumer;

/** Java 8 binding to native API++. Watch registration/lifecycle are caller-serialized.
 * Watched sessions live until JVM exit; the base API has no cancellation handle. */
public final class Ribbit implements AutoCloseable {
    static { System.loadLibrary("ribbit_senz"); }
    private long handle;
    public Ribbit(String address,String vendor) { handle=nConnect(bytes(address),bytes(vendor)); }
    private static byte[] bytes(String value) {
        Objects.requireNonNull(value);
        for(int i=0;i<value.length();++i) {
            char ch=value.charAt(i);
            if(Character.isHighSurrogate(ch)) {
                if(++i==value.length() || !Character.isLowSurrogate(value.charAt(i)))throw new IllegalArgumentException("Unpaired UTF-16 surrogate");
            } else if(Character.isLowSurrogate(ch))throw new IllegalArgumentException("Unpaired UTF-16 surrogate");
        }
        return value.getBytes(StandardCharsets.UTF_8);
    }
    private long session() { if(handle==0)throw new IllegalStateException("Ribbit is disconnected");return handle; }
    public boolean IsConnected() {return handle!=0;}
    public void DisconnectRibbit() {if(handle!=0){nDisconnect(handle);handle=0;}}
    @Override public void close(){DisconnectRibbit();}
    public void Set(String a,String b,String c,String bag){nCall(session(),0,bytes(a),bytes(b),bytes(c),bytes(bag));}
    public String Get(String a,String b,String c){return new String(nCall(session(),1,bytes(a),bytes(b),bytes(c),null),StandardCharsets.UTF_8);}
    public void SetCapability(String a,String b,String c,String bag){nCall(session(),2,bytes(a),bytes(b),bytes(c),bytes(bag));}
    public boolean CheckCapability(String a,String b,String c){return Boolean.parseBoolean(new String(nCall(session(),3,bytes(a),bytes(b),bytes(c),null),StandardCharsets.UTF_8));}
    public void Watch(String a,String b,String c,Consumer<String> on){watch(a,b,c,on,false);}
    public void WatchCapabilities(String a,String b,String c,Consumer<String> on){watch(a,b,c,on,true);}
    private void watch(String a,String b,String c,Consumer<String> on,boolean capabilities){Objects.requireNonNull(on);Consumer<byte[]> decoded=value->on.accept(new String(value,StandardCharsets.UTF_8));nWatch(session(),bytes(a),bytes(b),bytes(c),decoded,capabilities);}
    /** A held read on the calling thread: rows past {@code after}, or none when {@code waitSeconds} expires
     *  (0 < waitSeconds <= 30). Throws when the session has ended, so the caller can take a fresh one. */
    public String HeldRead(String a,String b,String c,long after,double waitSeconds){return new String(nHeld(session(),bytes(a),bytes(b),bytes(c),after,waitSeconds),StandardCharsets.UTF_8);}
    public void CheckCallbacks(){if(nFailures(session())!=0)throw new IllegalStateException("A watch callback failed; see JVM error log");}
    private static native long nConnect(byte[] address,byte[] vendor);
    private static native void nDisconnect(long handle);
    private static native byte[] nCall(long handle,int operation,byte[] a,byte[] b,byte[] c,byte[] bag);
    private static native void nWatch(long handle,byte[] a,byte[] b,byte[] c,Consumer<byte[]> callback,boolean capabilities);
    private static native int nFailures(long handle);
    private static native byte[] nHeld(long handle,byte[] a,byte[] b,byte[] c,long after,double waitSeconds);
}
