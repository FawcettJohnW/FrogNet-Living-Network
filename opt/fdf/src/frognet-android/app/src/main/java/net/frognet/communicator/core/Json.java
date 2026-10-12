// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** JSON in and out: objects are LinkedHashMap, arrays ArrayList, numbers Double (or Long when written as integers),
 *  strings, Boolean, null. A malformed document is refused (IllegalArgumentException), never guessed at. */
public final class Json {
    private final String s;
    private int i;

    private Json(String s) { this.s = s; }

    public static Object parse(String text) {
        Json p = new Json(text);
        p.ws();
        Object v = p.value();
        p.ws();
        if (p.i != p.s.length()) throw new IllegalArgumentException("trailing characters at " + p.i);
        return v;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> obj(Object o) {
        return o instanceof Map ? (Map<String, Object>) o : new LinkedHashMap<String, Object>();
    }

    @SuppressWarnings("unchecked")
    public static List<Object> arr(Object o) { return o instanceof List ? (List<Object>) o : new ArrayList<Object>(); }

    public static String str(Object o) { return o == null ? "" : o instanceof String ? (String) o : String.valueOf(o); }

    public static long num(Object o) {
        if (o instanceof Number) return ((Number) o).longValue();
        if (o instanceof String) { try { return (long) Double.parseDouble((String) o); } catch (NumberFormatException e) { return 0; } }
        return 0;
    }

    public static double dbl(Object o) {
        if (o instanceof Number) return ((Number) o).doubleValue();
        if (o instanceof String) { try { return Double.parseDouble((String) o); } catch (NumberFormatException e) { return 0; } }
        return 0;
    }

    public static boolean bool(Object o) {
        if (o instanceof Boolean) return (Boolean) o;
        if (o instanceof Number) return ((Number) o).doubleValue() != 0;
        return false;
    }

    private void ws() { while (i < s.length() && " \t\r\n".indexOf(s.charAt(i)) >= 0) i++; }

    private Object value() {
        if (i >= s.length()) throw new IllegalArgumentException("unexpected end");
        char c = s.charAt(i);
        if (c == '{') return object();
        if (c == '[') return array();
        if (c == '"') return string();
        if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
        if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
        if (s.startsWith("null", i)) { i += 4; return null; }
        return number();
    }

    private Map<String, Object> object() {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        i++; ws();
        if (i < s.length() && s.charAt(i) == '}') { i++; return m; }
        while (true) {
            ws();
            if (i >= s.length() || s.charAt(i) != '"') throw new IllegalArgumentException("expected a key at " + i);
            String k = string();
            ws();
            if (i >= s.length() || s.charAt(i) != ':') throw new IllegalArgumentException("expected ':' at " + i);
            i++; ws();
            m.put(k, value());
            ws();
            if (i >= s.length()) throw new IllegalArgumentException("unterminated object");
            char c = s.charAt(i++);
            if (c == '}') return m;
            if (c != ',') throw new IllegalArgumentException("expected ',' or '}' at " + (i - 1));
        }
    }

    private List<Object> array() {
        List<Object> a = new ArrayList<Object>();
        i++; ws();
        if (i < s.length() && s.charAt(i) == ']') { i++; return a; }
        while (true) {
            ws();
            a.add(value());
            ws();
            if (i >= s.length()) throw new IllegalArgumentException("unterminated array");
            char c = s.charAt(i++);
            if (c == ']') return a;
            if (c != ',') throw new IllegalArgumentException("expected ',' or ']' at " + (i - 1));
        }
    }

    private String string() {
        StringBuilder b = new StringBuilder();
        i++;
        while (true) {
            if (i >= s.length()) throw new IllegalArgumentException("unterminated string");
            char c = s.charAt(i++);
            if (c == '"') return b.toString();
            if (c != '\\') { b.append(c); continue; }
            if (i >= s.length()) throw new IllegalArgumentException("unterminated escape");
            char e = s.charAt(i++);
            switch (e) {
                case '"': b.append('"'); break;
                case '\\': b.append('\\'); break;
                case '/': b.append('/'); break;
                case 'b': b.append('\b'); break;
                case 'f': b.append('\f'); break;
                case 'n': b.append('\n'); break;
                case 'r': b.append('\r'); break;
                case 't': b.append('\t'); break;
                case 'u':
                    if (i + 4 > s.length()) throw new IllegalArgumentException("short \\u escape");
                    b.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; break;
                default: throw new IllegalArgumentException("bad escape \\" + e);
            }
        }
    }

    private Object number() {
        int st = i;
        while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
        String t = s.substring(st, i);
        if (t.isEmpty()) throw new IllegalArgumentException("unexpected character '" + s.charAt(st) + "' at " + st);
        if (t.indexOf('.') < 0 && t.indexOf('e') < 0 && t.indexOf('E') < 0) {
            try { return Long.parseLong(t); } catch (NumberFormatException ignored) { }
        }
        return Double.parseDouble(t);
    }

    // ---- out ------------------------------------------------------------------------------------------------
    public static String quote(String v) {
        StringBuilder b = new StringBuilder(v.length() + 2).append('"');
        for (int k = 0; k < v.length(); k++) {
            char c = v.charAt(k);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20) b.append(String.format("\\u%04x", (int) c)); else b.append(c);
            }
        }
        return b.append('"').toString();
    }

    public static String write(Object o) {
        StringBuilder b = new StringBuilder();
        write(b, o);
        return b.toString();
    }

    @SuppressWarnings("unchecked")
    private static void write(StringBuilder b, Object o) {
        if (o == null) { b.append("null"); return; }
        if (o instanceof String) { b.append(quote((String) o)); return; }
        if (o instanceof Boolean) { b.append(((Boolean) o) ? "true" : "false"); return; }
        if (o instanceof Long || o instanceof Integer || o instanceof Short) { b.append(String.valueOf(o)); return; }
        if (o instanceof Number) {
            double d = ((Number) o).doubleValue();
            if (d == Math.floor(d) && Math.abs(d) < 9e15) b.append(String.valueOf((long) d)); else b.append(String.valueOf(d));
            return;
        }
        if (o instanceof Map) {
            b.append('{');
            Iterator<Map.Entry<String, Object>> it = ((Map<String, Object>) o).entrySet().iterator();
            while (it.hasNext()) {
                Map.Entry<String, Object> e = it.next();
                b.append(quote(e.getKey())).append(':');
                write(b, e.getValue());
                if (it.hasNext()) b.append(',');
            }
            b.append('}');
            return;
        }
        if (o instanceof List) {
            b.append('[');
            List<Object> l = (List<Object>) o;
            for (int k = 0; k < l.size(); k++) { if (k > 0) b.append(','); write(b, l.get(k)); }
            b.append(']');
            return;
        }
        throw new IllegalArgumentException("not a JSON value: " + o.getClass());
    }

    /** A small builder: Json.o("a", 1, "b", "x") */
    public static Map<String, Object> o(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        for (int k = 0; k + 1 < kv.length; k += 2) m.put((String) kv[k], kv[k + 1]);
        return m;
    }
}
