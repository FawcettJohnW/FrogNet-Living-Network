import com.fawcett.ribbit.Ribbit;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
public final class BindingTest {
    private static int checks;
    private static void check(boolean ok,String label){if(!ok)throw new AssertionError(label);++checks;System.out.println("PASS "+label);}
    private static boolean rejects(Runnable action,Class<?> type){try{action.run();return false;}catch(RuntimeException e){return type.isInstance(e);}}
    public static void main(String[] args)throws Exception{
        Ribbit r=new Ribbit(args[0],"senz-binding");check(r.IsConnected(),"connection opens");
        r.Set("binding","unicode","sensor","{\"unit\":\"温度😀\"}");check(r.Get("binding","unicode","sensor").contains("rows"),"get returns JSON bag");
        check(rejects(()->r.Set("binding","bad","sensor","[]"),IllegalArgumentException.class),"nonobject rejected");
        check(rejects(()->r.Set("binding","bad","sensor","{bad"),IllegalArgumentException.class),"malformed JSON rejected");
        check(rejects(()->r.Set("binding","bad","","{}"),IllegalArgumentException.class),"family write rejected");
        check(rejects(()->r.Set("binding","bad","a\0b","{}"),IllegalArgumentException.class),"NUL rejected");
        check(rejects(()->r.Set("binding","bad","sensor",null),NullPointerException.class),"null rejected");
        check(rejects(()->r.Set("binding","bad","\uD800","{}"),IllegalArgumentException.class),"unpaired surrogate rejected");
        r.DisconnectRibbit();r.DisconnectRibbit();check(!r.IsConnected(),"disconnect idempotent");
        check(rejects(()->r.Get("binding","unicode","sensor"),IllegalStateException.class),"closed access rejected");
        check(rejects(()->new Ribbit("invalid-address","senz-binding"),RuntimeException.class),"connection failure propagates");
        Ribbit watcher=new Ribbit(args[0],"senz-binding");CountDownLatch changed=new CountDownLatch(1);AtomicReference<String> result=new AtomicReference<>();
        watcher.Watch("binding","desired","sensor",value->{if(value.contains("\"v\":7")){result.set(value);changed.countDown();}});
        watcher.Set("binding","desired","other","{\"v\":99}");watcher.Set("binding","desired","sensor","{\"v\":7,\"unit\":\"温度😀\"}");
        check(changed.await(5,TimeUnit.SECONDS),"held callback arrives");check(result.get().contains("sensor")&&!result.get().contains("other"),"exact selection");
        check(rejects(watcher::DisconnectRibbit,IllegalStateException.class),"unsafe disconnect rejected");
        check(result.get().contains("\\u6e29")&&result.get().contains("\\ud83d\\ude00"),"UTF8 and non-BMP roundtrip");watcher.CheckCallbacks();
        CountDownLatch bad=new CountDownLatch(1),safe=new CountDownLatch(1);
        watcher.Watch("binding","errors","sensor",value->{if(value.contains("\"boom\":true")){bad.countDown();throw new IllegalArgumentException("deliberate callback failure");}if(value.contains("\"safe\":true"))safe.countDown();});
        watcher.Set("binding","errors","sensor","{\"boom\":true}");check(bad.await(5,TimeUnit.SECONDS),"failing callback executes");
        watcher.Set("binding","errors","sensor","{\"safe\":true}");check(safe.await(5,TimeUnit.SECONDS),"callback remains usable");
        check(rejects(watcher::CheckCallbacks,IllegalStateException.class),"callback failure observable");
        Ribbit held=new Ribbit(args[0],"senz-binding");held.Set("binding","held","a","{\"n\":1}");
        String first=held.HeldRead("binding","held","",0,5);check(first.contains("\"n\":1")&&first.contains("\"after\""),"held read returns existing rows and a cursor");
        long after=Long.parseLong(first.replaceAll(".*\"after\":(\\d+).*","$1"));
        long t0=System.nanoTime();String none=held.HeldRead("binding","held","",after,0.3);long ms=(System.nanoTime()-t0)/1000000;
        check(none.contains("\"rows\":[]")&&ms>=250,"held read with nothing new waits, then returns no rows");
        Thread later=new Thread(()->{try{Thread.sleep(200);}catch(InterruptedException e){}held.Set("binding","held","b","{\"n\":2}");});later.start();
        String next=held.HeldRead("binding","held","",after,5);later.join();check(next.contains("\"n\":2")&&!next.contains("\"n\":1"),"held read wakes on the next write only");
        check(rejects(()->held.HeldRead("binding","held","",-1,1),IllegalArgumentException.class)&&rejects(()->held.HeldRead("binding","held","",0,31),IllegalArgumentException.class),"invalid cursor or wait rejected");
        held.DisconnectRibbit();check(rejects(()->held.HeldRead("binding","held","",0,1),IllegalStateException.class),"held read on a closed connection rejected");
        System.out.println("RESULT PASS JNI binding ("+checks+" checks)");
    }
}
