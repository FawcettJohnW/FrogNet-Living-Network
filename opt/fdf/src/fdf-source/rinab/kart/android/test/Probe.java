import net.frognet.kart.core.*;
import java.util.*;
/** Probe: own write goes with the session; a held read waits and returns the write. */
public class Probe {
    public static void main(String[] a) throws Exception {
        String h = a[0]; int p = Integer.parseInt(a[1]);
        RamSession w = new RamSession(h, p, 3000), r = new RamSession(h, p, 3000);
        w.post("POST", "/ram.php?op=write", Json.write(Json.o("service","kart","variable","lobby","instance","phone","bag",Json.o("name","phone"),"own",true)));
        Thread.sleep(300);
        Map<String,Object> o = r.call("GET", "/ram.php?op=read&service=kart&variable=lobby", "", 5);
        List<Object> cells = Json.arr(o.containsKey("rows") ? o.get("rows") : o.get("cells"));
        long last = 0; for (Object c : cells) last = Math.max(last, Json.num(Json.obj(c).get("id")));
        System.out.println("1 read after the posted own write: " + Json.write(cells));
        final long after = last; final long t0 = System.currentTimeMillis();
        Thread t = new Thread(() -> { try {
            Map<String,Object> q = r.call("GET", "/ram.php?op=read&service=kart&variable=lobby&after=" + after + "&wait_s=10", "", 20);
            System.out.println("2 held read returned after " + (System.currentTimeMillis()-t0) + " ms: " + Json.write(q.get(q.containsKey("rows")?"rows":"cells")));
        } catch (Exception e) { System.out.println("2 held read failed: " + e); } });
        t.start(); Thread.sleep(1000); w.close(); t.join();
    }
}
