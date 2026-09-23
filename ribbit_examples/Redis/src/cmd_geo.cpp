// Geo: a sorted set whose score is the 52-bit interleaved geohash (Redis's encoding, reproduced from
// geohash.c so GEOPOS/GEOHASH/GEODIST answer to the same bit). Search walks the numeric index over the
// nine geohash cells around the center (the step chosen from the radius, as geohash_helper.c does) and
// applies the exact circle/box test to what it finds; nothing outside those cells is looked at.
#include "server.hpp"
#include <cmath>
#include <cstring>
#include <algorithm>

static const double GEO_LAT_MIN = -85.05112878, GEO_LAT_MAX = 85.05112878, GEO_LONG_MIN = -180, GEO_LONG_MAX = 180;
static const double EARTH_RADIUS_IN_METERS = 6372797.560856;
static const int GEO_STEP_MAX = 26;

static uint64_t interleave64(uint32_t xlo, uint32_t ylo) {
    static const uint64_t B[] = {0x5555555555555555ULL, 0x3333333333333333ULL, 0x0F0F0F0F0F0F0F0FULL, 0x00FF00FF00FF00FFULL, 0x0000FFFF0000FFFFULL};
    static const unsigned int S[] = {1, 2, 4, 8, 16};
    uint64_t x = xlo, y = ylo;
    x = (x | (x << S[4])) & B[4]; y = (y | (y << S[4])) & B[4];
    x = (x | (x << S[3])) & B[3]; y = (y | (y << S[3])) & B[3];
    x = (x | (x << S[2])) & B[2]; y = (y | (y << S[2])) & B[2];
    x = (x | (x << S[1])) & B[1]; y = (y | (y << S[1])) & B[1];
    x = (x | (x << S[0])) & B[0]; y = (y | (y << S[0])) & B[0];
    return x | (y << 1);
}
static uint64_t deinterleave64(uint64_t interleaved) {
    static const uint64_t B[] = {0x5555555555555555ULL, 0x3333333333333333ULL, 0x0F0F0F0F0F0F0F0FULL, 0x00FF00FF00FF00FFULL, 0x0000FFFF0000FFFFULL, 0x00000000FFFFFFFFULL};
    static const unsigned int S[] = {0, 1, 2, 4, 8, 16};
    uint64_t x = interleaved, y = interleaved >> 1;
    x = (x | (x >> S[0])) & B[0]; y = (y | (y >> S[0])) & B[0];
    x = (x | (x >> S[1])) & B[1]; y = (y | (y >> S[1])) & B[1];
    x = (x | (x >> S[2])) & B[2]; y = (y | (y >> S[2])) & B[2];
    x = (x | (x >> S[3])) & B[3]; y = (y | (y >> S[3])) & B[3];
    x = (x | (x >> S[4])) & B[4]; y = (y | (y >> S[4])) & B[4];
    x = (x | (x >> S[5])) & B[5]; y = (y | (y >> S[5])) & B[5];
    return x | (y << 32);
}
static bool geo_encode(double lon, double lat, double lonmin, double lonmax, double latmin, double latmax, int step, uint64_t& bits) {
    if (lon > GEO_LONG_MAX || lon < GEO_LONG_MIN || lat > GEO_LAT_MAX || lat < GEO_LAT_MIN) return false;
    if (lat < latmin || lat > latmax || lon < lonmin || lon > lonmax) return false;
    double lat_offset = (lat - latmin) / (latmax - latmin), long_offset = (lon - lonmin) / (lonmax - lonmin);
    lat_offset *= (double)(1ULL << step); long_offset *= (double)(1ULL << step);
    bits = interleave64((uint32_t)lat_offset, (uint32_t)long_offset);
    return true;
}
static void geo_decode(uint64_t bits, double& lon, double& lat) {
    uint64_t sep = deinterleave64(bits);
    uint32_t ilato = (uint32_t)sep, ilono = (uint32_t)(sep >> 32);
    double lat_scale = GEO_LAT_MAX - GEO_LAT_MIN, long_scale = GEO_LONG_MAX - GEO_LONG_MIN;
    double latmin = GEO_LAT_MIN + (ilato * 1.0 / (1ULL << GEO_STEP_MAX)) * lat_scale, latmax = GEO_LAT_MIN + ((ilato + 1) * 1.0 / (1ULL << GEO_STEP_MAX)) * lat_scale;
    double lonmin = GEO_LONG_MIN + (ilono * 1.0 / (1ULL << GEO_STEP_MAX)) * long_scale, lonmax = GEO_LONG_MIN + ((ilono + 1) * 1.0 / (1ULL << GEO_STEP_MAX)) * long_scale;
    lon = std::min(GEO_LONG_MAX, std::max(GEO_LONG_MIN, (lonmin + lonmax) / 2));
    lat = std::min(GEO_LAT_MAX, std::max(GEO_LAT_MIN, (latmin + latmax) / 2));
}
static double deg_rad(double d) { return d * M_PI / 180.0; }
static double lat_distance(double lat1, double lat2) { return EARTH_RADIUS_IN_METERS * fabs(deg_rad(lat2) - deg_rad(lat1)); }
static double geo_distance(double lon1, double lat1, double lon2, double lat2) {
    double lon1r = deg_rad(lon1), lon2r = deg_rad(lon2), v = sin((lon2r - lon1r) / 2);
    if (v == 0.0) return lat_distance(lat1, lat2);
    double lat1r = deg_rad(lat1), lat2r = deg_rad(lat2), u = sin((lat2r - lat1r) / 2);
    double a = u * u + cos(lat1r) * cos(lat2r) * v * v;
    return 2.0 * EARTH_RADIUS_IN_METERS * asin(sqrt(a));
}
static bool in_rectangle(double width_m, double height_m, double x1, double y1, double x2, double y2, double& distance) {
    if (lat_distance(y2, y1) > height_m / 2) return false;
    if (geo_distance(x2, y2, x1, y2) > width_m / 2) return false;
    distance = geo_distance(x1, y1, x2, y2); return true;
}
static bool unit_of(const std::string& u, double& m, Reply& r) {
    std::string l = lower(u);
    if (l == "m") m = 1; else if (l == "km") m = 1000; else if (l == "ft") m = 0.3048; else if (l == "mi") m = 1609.34;
    else { r.error("ERR unsupported unit provided. please use M, KM, FT, MI"); return false; }
    return true;
}
static bool lonlat_of(const std::string& lons, const std::string& lats, double& lon, double& lat, Reply& r) {
    long double a, b;
    if (!string2ld(lons, a) || !string2ld(lats, b)) { err_notfloat(r); return false; }
    lon = (double)a; lat = (double)b;
    if (lon < GEO_LONG_MIN || lon > GEO_LONG_MAX || lat < GEO_LAT_MIN || lat > GEO_LAT_MAX) { char buf[128]; snprintf(buf, sizeof buf, "ERR invalid longitude,latitude pair %f,%f", lon, lat); r.error(buf); return false; }
    return true;
}
static bool member_lonlat(const VarPtr& v, const std::string& m, double& lon, double& lat) {
    double d; if (!z_score(v, m, d)) return false;
    geo_decode((uint64_t)d, lon, lat); return true;
}
static std::string human_ld(double d) { return ld2string_human((long double)d); }

// ---------------------------------------------------------------- GEOADD / GEOPOS / GEODIST / GEOHASH
static void cmd_geoadd(Client& c, const Argv& a, Reply& r) {
    size_t j = 2; bool nx = false, xx = false, ch = false;
    for (; j < a.size(); j++) { std::string o = lower(a[j]); if (o == "nx") nx = true; else if (o == "xx") xx = true; else if (o == "ch") ch = true; else break; }
    if (nx && xx) { err_syntax(r); return; }
    size_t elements = a.size() - j;
    if (elements == 0 || elements % 3) { err_syntax(r); return; }
    std::vector<std::pair<std::string, double>> ms;
    for (size_t i = j; i < a.size(); i += 3) {
        double lon, lat; if (!lonlat_of(a[i], a[i+1], lon, lat, r)) return;
        uint64_t bits; if (!geo_encode(lon, lat, GEO_LONG_MIN, GEO_LONG_MAX, GEO_LAT_MIN, GEO_LAT_MAX, GEO_STEP_MAX, bits)) { err_syntax(r); return; }
        ms.emplace_back(a[i+2], (double)bits);
    }
    VarPtr v; if (z_var(c, a[1], v, r) < 0) return;
    std::vector<std::pair<std::string, double>> put; long long added = 0, changed = 0;
    for (auto& m : ms) {
        double old; bool had = z_score(v, m.first, old);
        if (had && nx) continue;
        if (!had && xx) continue;
        if (!had) added++; else if (old != m.second) changed++;
        if (!had || old != m.second) put.push_back(m);
    }
    z_put(c.db, a[1], v, put);
    r.integer(ch ? added + changed : added);
}
static void cmd_geopos(Client& c, const Argv& a, Reply& r) {
    VarPtr v; if (z_var(c, a[1], v, r) < 0) return;
    r.array(a.size() - 2);
    for (size_t i = 2; i < a.size(); i++) {
        double lon, lat;
        if (!member_lonlat(v, a[i], lon, lat)) { r.null_array(); continue; }
        r.array(2); r.bulk(human_ld(lon)); r.bulk(human_ld(lat));
    }
}
static void cmd_geodist(Client& c, const Argv& a, Reply& r) {
    if (a.size() > 5) { err_syntax(r); return; }
    double to_m = 1; if (a.size() == 5 && !unit_of(a[4], to_m, r)) return;
    VarPtr v; if (z_var(c, a[1], v, r) < 0) return;
    double lon1, lat1, lon2, lat2;
    if (!member_lonlat(v, a[2], lon1, lat1) || !member_lonlat(v, a[3], lon2, lat2)) { r.null(); return; }
    char b[64]; snprintf(b, sizeof b, "%.4f", geo_distance(lon1, lat1, lon2, lat2) / to_m); r.bulk(b);
}
static void cmd_geohash(Client& c, const Argv& a, Reply& r) {
    VarPtr v; if (z_var(c, a[1], v, r) < 0) return;
    const char* alphabet = "0123456789bcdefghjkmnpqrstuvwxyz";
    r.array(a.size() - 2);
    for (size_t i = 2; i < a.size(); i++) {
        double lon, lat;
        if (!member_lonlat(v, a[i], lon, lat)) { r.null(); continue; }
        uint64_t bits = 0; geo_encode(lon, lat, -180, 180, -90, 90, 26, bits);
        char buf[12];
        for (int k = 0; k < 11; k++) {
            int idx;
            if (k == 10) idx = 0;                                  // the last digit: the 52nd bit and padding, as Redis does
            else idx = (int)((bits >> (52 - ((k + 1) * 5))) & 0x1f);
            buf[k] = alphabet[idx];
        }
        buf[11] = 0; r.bulk(std::string(buf, 11));
    }
}

// ---------------------------------------------------------------- search
struct GeoPoint { std::string member; double score, dist, lon, lat; };
enum { F_COORDS = 1, F_MEMBER = 2, F_NOSTORE = 4, F_SEARCH = 8, F_SEARCHSTORE = 16 };
// ---- the cells to scan: ported from geohash_helper.c (estimate step, center cell, eight neighbours)
static const double MERCATOR_MAX = 20037726.37;
static int geo_steps_for(double range_meters, double lat) {
    if (range_meters == 0) return 26;
    int step = 1;
    while (range_meters < MERCATOR_MAX) { range_meters *= 2; step++; }
    step -= 2;
    if (lat > 66 || lat < -66) { step--; if (lat > 80 || lat < -80) step--; }
    if (step < 1) step = 1; if (step > 26) step = 26;
    return step;
}
static uint64_t move_x(uint64_t bits, int step, int d) {
    if (d == 0) return bits;
    uint64_t x = bits & 0xaaaaaaaaaaaaaaaaULL, y = bits & 0x5555555555555555ULL;
    uint64_t zz = 0x5555555555555555ULL >> (64 - step * 2);
    if (d > 0) x = x + (zz + 1); else { x = x | zz; x = x - (zz + 1); }
    x &= (0xaaaaaaaaaaaaaaaaULL >> (64 - step * 2));
    return x | y;
}
static uint64_t move_y(uint64_t bits, int step, int d) {
    if (d == 0) return bits;
    uint64_t x = bits & 0xaaaaaaaaaaaaaaaaULL, y = bits & 0x5555555555555555ULL;
    uint64_t zz = 0xaaaaaaaaaaaaaaaaULL >> (64 - step * 2);
    if (d > 0) y = y + (zz + 1); else { y = y | zz; y = y - (zz + 1); }
    y &= (0x5555555555555555ULL >> (64 - step * 2));
    return x | y;
}
// the lon/lat extent of a cell at `step`
static void cell_area(uint64_t bits, int step, double& lonmin, double& lonmax, double& latmin, double& latmax) {
    uint64_t sep = deinterleave64(bits);
    uint32_t ilato = (uint32_t)sep, ilono = (uint32_t)(sep >> 32);
    double lat_scale = GEO_LAT_MAX - GEO_LAT_MIN, long_scale = GEO_LONG_MAX - GEO_LONG_MIN;
    latmin = GEO_LAT_MIN + (ilato * 1.0 / (1ULL << step)) * lat_scale; latmax = GEO_LAT_MIN + ((ilato + 1) * 1.0 / (1ULL << step)) * lat_scale;
    lonmin = GEO_LONG_MIN + (ilono * 1.0 / (1ULL << step)) * long_scale; lonmax = GEO_LONG_MIN + ((ilono + 1) * 1.0 / (1ULL << step)) * long_scale;
}
// score ranges [lo, hi] (inclusive, as doubles) covering the search shape -- geohashCalculateAreasByShapeWGS84,
// ported: estimate the step, take the center cell and its eight neighbours, and if a neighbour does not reach
// the shape's bounding box (which is what happens at a pole, where a neighbour wraps) go one step coarser once.
// width/height are the shape's extent in metres (a circle: 2r x 2r).
static std::vector<std::pair<double, double>> geo_cells(double lon, double lat, double width_m, double height_m) {
    double lat_delta = (height_m / 2 / EARTH_RADIUS_IN_METERS) * 180.0 / M_PI;
    double long_delta_top = (width_m / 2 / EARTH_RADIUS_IN_METERS / cos(deg_rad(lat + lat_delta))) * 180.0 / M_PI;
    double long_delta_bottom = (width_m / 2 / EARTH_RADIUS_IN_METERS / cos(deg_rad(lat - lat_delta))) * 180.0 / M_PI;
    bool south_hemi = lat < 0;
    double min_lon = south_hemi ? lon - long_delta_bottom : lon - long_delta_top;
    double max_lon = south_hemi ? lon + long_delta_bottom : lon + long_delta_top;
    double min_lat = lat - lat_delta, max_lat = lat + lat_delta;
    int step = geo_steps_for(std::max(width_m, height_m) / 2, lat);
    uint64_t center = 0;
    geo_encode(lon, lat, GEO_LONG_MIN, GEO_LONG_MAX, GEO_LAT_MIN, GEO_LAT_MAX, step, center);
    auto reaches = [&](int st, uint64_t c) {
        double lonmin, lonmax, latmin, latmax;
        cell_area(move_y(c, st, 1), st, lonmin, lonmax, latmin, latmax);  if (latmax < max_lat) return false;
        cell_area(move_y(c, st, -1), st, lonmin, lonmax, latmin, latmax); if (latmin > min_lat) return false;
        cell_area(move_x(c, st, 1), st, lonmin, lonmax, latmin, latmax);  if (lonmax < max_lon) return false;
        cell_area(move_x(c, st, -1), st, lonmin, lonmax, latmin, latmax); if (lonmin > min_lon) return false;
        return true;
    };
    if (step > 1 && !reaches(step, center)) { step--; geo_encode(lon, lat, GEO_LONG_MIN, GEO_LONG_MAX, GEO_LAT_MIN, GEO_LAT_MAX, step, center); }
    std::vector<std::pair<double, double>> out;
    int shift = 2 * (26 - step);
    // Redis's neighbour order (center, N, S, E, W, NE, NW, SE, SW): the unsorted reply keeps it
    static const int order[9][2] = {{0,0},{0,1},{0,-1},{1,0},{-1,0},{1,1},{-1,1},{1,-1},{-1,-1}};
    for (auto& d : order) {
        int dx = d[0], dy = d[1];
        uint64_t bits = move_y(move_x(center, step, dx), step, dy);
        double lo = (double)(bits << shift), hi = (double)(((bits + 1) << shift) - 1);
        bool dup = false; for (auto& p : out) if (p.first == lo) dup = true;
        if (!dup) out.emplace_back(lo, hi);
    }
    return out;
}

static void georadius_generic(Client& c, const Argv& a, Reply& r, int srcidx, int flags) {
    std::string storekey; bool storedist = false;
    VarPtr v; int st = z_var(c, a[srcidx], v, r); if (st < 0) return;
    size_t base; bool circular = true; double xy[2] = {0, 0}, radius = 0, width = 0, height = 0, conv = 1;
    if (flags & F_COORDS) {
        base = 6;
        if (!lonlat_of(a[2], a[3], xy[0], xy[1], r)) return;
        long double d; if (!string2ld(a[4], d)) { r.error("ERR need numeric radius"); return; }
        if (d < 0) { r.error("ERR radius cannot be negative"); return; }
        if (!unit_of(a[5], conv, r)) return; radius = (double)d * conv;
    } else if (flags & F_MEMBER) {
        base = 5;
        if (st) { if (!member_lonlat(v, a[2], xy[0], xy[1])) { r.error("ERR could not decode requested zset member"); return; } }
        long double d; if (!string2ld(a[3], d)) { r.error("ERR need numeric radius"); return; }
        if (d < 0) { r.error("ERR radius cannot be negative"); return; }
        if (!unit_of(a[4], conv, r)) return; radius = (double)d * conv;
    } else { base = (flags & F_SEARCHSTORE) ? 3 : 2; if (flags & F_SEARCHSTORE) storekey = a[1]; }
    bool withdist = false, withhash = false, withcoords = false, frommember = false, fromloc = false, byradius = false, bybox = false, any = false;
    int sort = 0; long long count = 0;
    for (size_t i = base; i < a.size(); i++) {
        std::string arg = lower(a[i]); size_t remaining = a.size() - i - 1;
        if (arg == "withdist") withdist = true;
        else if (arg == "withhash") withhash = true;
        else if (arg == "withcoord") withcoords = true;
        else if (arg == "any") any = true;
        else if (arg == "asc") sort = 1;
        else if (arg == "desc") sort = 2;
        else if (arg == "count" && remaining >= 1) { if (!string2ll(a[i+1], count)) { err_notint(r); return; } if (count <= 0) { r.error("ERR COUNT must be > 0"); return; } i++; }
        else if (arg == "store" && remaining >= 1 && !(flags & F_NOSTORE) && !(flags & F_SEARCH)) { storekey = a[i+1]; storedist = false; i++; }
        else if (arg == "storedist" && remaining >= 1 && !(flags & F_NOSTORE) && !(flags & F_SEARCH)) { storekey = a[i+1]; storedist = true; i++; }
        else if (arg == "storedist" && (flags & F_SEARCH) && (flags & F_SEARCHSTORE)) storedist = true;
        else if (arg == "frommember" && remaining >= 1 && (flags & F_SEARCH) && !fromloc) {
            if (st && !member_lonlat(v, a[i+1], xy[0], xy[1])) { r.error("ERR could not decode requested zset member"); return; }
            frommember = true; i++;
        } else if (arg == "fromlonlat" && remaining >= 2 && (flags & F_SEARCH) && !frommember) { if (!lonlat_of(a[i+1], a[i+2], xy[0], xy[1], r)) return; fromloc = true; i += 2; }
        else if (arg == "byradius" && remaining >= 2 && (flags & F_SEARCH) && !bybox) {
            long double d; if (!string2ld(a[i+1], d)) { r.error("ERR need numeric radius"); return; }
            if (d < 0) { r.error("ERR radius cannot be negative"); return; }
            if (!unit_of(a[i+2], conv, r)) return; radius = (double)d * conv; circular = true; byradius = true; i += 2;
        } else if (arg == "bybox" && remaining >= 3 && (flags & F_SEARCH) && !byradius) {
            long double w, h; if (!string2ld(a[i+1], w) || !string2ld(a[i+2], h)) { r.error("ERR need numeric width or height"); return; }
            if (w < 0 || h < 0) { r.error("ERR height or width cannot be negative"); return; }
            if (!unit_of(a[i+3], conv, r)) return; width = (double)w * conv; height = (double)h * conv; circular = false; bybox = true; i += 3;
        } else { err_syntax(r); return; }
    }
    if (!storekey.empty() && (withdist || withhash || withcoords)) { r.error(std::string("ERR ") + ((flags & F_SEARCHSTORE) ? "GEOSEARCHSTORE" : "STORE option in GEORADIUS") + " is not compatible with WITHDIST, WITHHASH and WITHCOORD options"); return; }
    if ((flags & F_SEARCH) && !(frommember || fromloc)) { r.error("ERR exactly one of FROMMEMBER or FROMLONLAT can be specified for " + a[0]); return; }
    if ((flags & F_SEARCH) && !(byradius || bybox)) { r.error("ERR exactly one of BYRADIUS and BYBOX can be specified for " + a[0]); return; }
    if (any && !count) { r.error("ERR the ANY argument requires COUNT argument"); return; }
    if (!st) { if (!storekey.empty()) { db::del(c.db, storekey); r.integer(0); } else r.array(0); return; }
    if (count != 0 && sort == 0 && !any) sort = 1;
    std::vector<GeoPoint> pts;
    bool stop = false;
    for (auto& cell : geo_cells(xy[0], xy[1], circular ? radius * 2 : width, circular ? radius * 2 : height)) {
        if (stop) break;
        z_range_scores(v, cell.first, cell.second, [&](const std::string& member, double score) {
            GeoPoint gp; gp.member = member; gp.score = score; geo_decode((uint64_t)score, gp.lon, gp.lat);
            if (circular) { gp.dist = geo_distance(xy[0], xy[1], gp.lon, gp.lat); if (gp.dist > radius) return true; }
            else if (!in_rectangle(width, height, xy[0], xy[1], gp.lon, gp.lat, gp.dist)) return true;
            pts.push_back(std::move(gp));
            if (any && count && (long long)pts.size() >= count) { stop = true; return false; }
            return true;
        });
    }
    if (sort == 1) std::stable_sort(pts.begin(), pts.end(), [](const GeoPoint& x, const GeoPoint& y) { return x.dist < y.dist; });
    else if (sort == 2) std::stable_sort(pts.begin(), pts.end(), [](const GeoPoint& x, const GeoPoint& y) { return x.dist > y.dist; });
    size_t returned = (count == 0 || (long long)pts.size() < count) ? pts.size() : (size_t)count;
    if (storekey.empty()) {
        int opts = withdist + withhash + withcoords;
        r.array(returned);
        for (size_t i = 0; i < returned; i++) {
            GeoPoint& gp = pts[i];
            if (opts == 0) { r.bulk(gp.member); continue; }
            r.array(opts + 1); r.bulk(gp.member);
            if (withdist) { char b[64]; snprintf(b, sizeof b, "%.4f", gp.dist / conv); r.bulk(b); }
            if (withhash) r.integer((long long)gp.score);
            if (withcoords) { r.array(2); r.bulk(human_ld(gp.lon)); r.bulk(human_ld(gp.lat)); }
        }
        return;
    }
    std::vector<std::pair<std::string, double>> ms;
    for (size_t i = 0; i < returned; i++) ms.emplace_back(pts[i].member, storedist ? pts[i].dist / conv : pts[i].score);
    VarPtr dv; if (z_var(c, storekey, dv, r) < 0) return;
    z_store(c.db, storekey, ms);
    r.integer((long long)returned);
}
static void cmd_georadius(Client& c, const Argv& a, Reply& r) { georadius_generic(c, a, r, 1, F_COORDS); }
static void cmd_georadiusbymember(Client& c, const Argv& a, Reply& r) { georadius_generic(c, a, r, 1, F_MEMBER); }
static void cmd_georadius_ro(Client& c, const Argv& a, Reply& r) { georadius_generic(c, a, r, 1, F_COORDS | F_NOSTORE); }
static void cmd_georadiusbymember_ro(Client& c, const Argv& a, Reply& r) { georadius_generic(c, a, r, 1, F_MEMBER | F_NOSTORE); }
static void cmd_geosearch(Client& c, const Argv& a, Reply& r) { georadius_generic(c, a, r, 1, F_SEARCH); }
static void cmd_geosearchstore(Client& c, const Argv& a, Reply& r) { georadius_generic(c, a, r, 2, F_SEARCH | F_SEARCHSTORE); }

void register_geo_commands() {
    register_cmd("geoadd", cmd_geoadd); register_cmd("geopos", cmd_geopos); register_cmd("geodist", cmd_geodist); register_cmd("geohash", cmd_geohash);
    register_cmd("georadius", cmd_georadius); register_cmd("georadiusbymember", cmd_georadiusbymember); register_cmd("georadius_ro", cmd_georadius_ro); register_cmd("georadiusbymember_ro", cmd_georadiusbymember_ro);
    register_cmd("geosearch", cmd_geosearch); register_cmd("geosearchstore", cmd_geosearchstore);
}
