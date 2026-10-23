import net.frognet.kart.core.*;
import java.util.*;

/** The phone's game core, without the phone: host port name bots readyAfterS seconds. Prints one line a second. */
public class Headless {
    public static void main(String[] a) throws Exception {
        String host = a[0]; int port = Integer.parseInt(a[1]); String name = a[2];
        int bots = Integer.parseInt(a[3]); double readyAfter = Double.parseDouble(a[4]); double runFor = Double.parseDouble(a[5]);
        Game g = new Game(name, "jvm-phone", host, port, bots, true, 2, new Region.Log() { public void say(String l) { System.out.println("phone: " + l); System.out.flush(); } });
        g.play(true);
        double t0 = System.nanoTime() / 1e9, last = t0, said = t0; long w0 = 0; int frames = 0; boolean readied = false;
        while (System.nanoTime() / 1e9 - t0 < runFor) {
            double now = System.currentTimeMillis() / 1000.0, mono = System.nanoTime() / 1e9, dt = mono - last; last = mono;
            if (g.state.equals("lobby") && !g.ready() && g.myRace() == null && mono - t0 > readyAfter) g.pressStart();
            g.tick(now, dt, 0, 0); frames++;
            if (mono - said >= 1.0) {
                long w = g.writes();
                System.out.printf(Locale.ROOT, "phone: [TEST] %s %s %.1f fps, %d other kart(s) seen, %.1f writes/s, me lap %d at (%.0f,%.0f)%n", name, g.state,
                        frames / (mono - said), g.others(now).size(), (w - w0) / (mono - said), g.me.lap, g.me.x, g.me.y);
                System.out.flush(); w0 = w; frames = 0; said = mono;
            }
            Thread.sleep(16);
        }
        g.stop(); Thread.sleep(300);
    }
}
