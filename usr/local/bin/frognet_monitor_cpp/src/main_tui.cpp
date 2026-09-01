/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
// main_tui.cpp — the curses dashboard.
//
// One rule governs the structure: THE DRAW LOOP NEVER BLOCKS ON A SOCKET.
// A worker thread does every network read and publishes a finished Snapshot by
// whole-value swap; the main loop only paints and reads keys. A repaint sees a
// complete snapshot or the previous complete snapshot, never half of one.
#include "dashboard.h"
#include "frognet.h"

#include <ncurses.h>

#include <atomic>
#include <chrono>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

using namespace frognet;

namespace {

// Colour pairs. The model speaks in Ink; only this file knows about curses.
enum { CP_DIM = 1, CP_NORMAL, CP_GOOD, CP_WARN, CP_BAD, CP_HEAD, CP_TITLE, CP_SELECT };

int inkPair(Ink i) {
  switch (i) {
    case Ink::Dim:    return CP_DIM;
    case Ink::Good:   return CP_GOOD;
    case Ink::Warn:   return CP_WARN;
    case Ink::Bad:    return CP_BAD;
    case Ink::Head:   return CP_HEAD;
    case Ink::Title:  return CP_TITLE;
    case Ink::Select: return CP_SELECT;
    default:          return CP_NORMAL;
  }
}

void initColors() {
  if (!has_colors()) return;
  start_color();
  use_default_colors();
  init_pair(CP_DIM,    COLOR_BLACK,   -1);
  init_pair(CP_NORMAL, -1,            -1);
  init_pair(CP_GOOD,   COLOR_GREEN,   -1);
  init_pair(CP_WARN,   COLOR_YELLOW,  -1);
  init_pair(CP_BAD,    COLOR_RED,     -1);
  init_pair(CP_HEAD,   COLOR_CYAN,    -1);
  init_pair(CP_TITLE,  COLOR_WHITE,   COLOR_BLUE);
  init_pair(CP_SELECT, COLOR_BLACK,   COLOR_CYAN);
}

// Write at most the remaining width; never let a long value corrupt the layout.
void put(int row, int col, const std::string& s, int pair, bool bold = false) {
  int maxy = 0, maxx = 0;
  getmaxyx(stdscr, maxy, maxx);
  if (row < 0 || row >= maxy || col >= maxx) return;
  // NOTE: byte length, not display width. The load bars are multibyte UTF-8, so
  // this truncation is deliberately generous rather than exact -- it exists to
  // stop a runaway value corrupting the screen, not to lay out columns.
  std::string t = s;
  int budget = (maxx - col - 1) * 4;
  if (budget > 0 && static_cast<int>(t.size()) > budget)
    t = t.substr(0, static_cast<size_t>(budget));
  attron(COLOR_PAIR(pair) | (bold ? A_BOLD : 0));
  mvaddstr(row, col, t.c_str());
  attroff(COLOR_PAIR(pair) | (bold ? A_BOLD : 0));
}

// Two boards over the same peers, answering different questions:
//   LINK -- can I reach it, and how well is the transport doing
//   LOAD -- what is that machine actually doing while it serves
// A node can be perfect on one and in trouble on the other, which is the
// reason both exist rather than one merged table.
enum class Board { Link, Load };
Board g_board = Board::Link;

std::atomic<bool> g_stop{false};
std::atomic<bool> g_refresh_now{false};
std::atomic<long long> g_gen{0};

void worker(Client* c, State* st, std::string domain) {
  while (!g_stop.load()) {
    Snapshot s = collect(*c, domain);
    s.generation = ++g_gen;
    st->set(std::move(s));
    // Sleep in slices so quit and manual refresh are responsive without the
    // draw loop ever waiting on this thread.
    for (int i = 0; i < 30 && !g_stop.load(); ++i) {
      if (g_refresh_now.exchange(false)) break;
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
  }
}

int drawTitle(const Snapshot& s, int row) {
  int maxy = 0, maxx = 0;
  getmaxyx(stdscr, maxy, maxx);
  (void)maxy;
  std::string bar(static_cast<size_t>(std::max(0, maxx - 2)), ' ');
  put(row, 1, bar, CP_TITLE, true);

  std::string left = "  FrogNet Monitor  \u2014  " +
                     (s.domain.empty() ? std::string("(no identity)") : s.domain) +
                     (g_board == Board::Link ? "   [LINK]" : "   [LOAD]");
  put(row, 1, left, CP_TITLE, true);

  // [MERGE_IS_VISIBLE_V1] Three states. "Unknown" is not "no merge": a merge
  // degrades every latency figure on this screen while it runs, so a reader who
  // cannot tell must be told that, not shown a clean answer.
  std::string right;
  if (!s.merge_known)      right = "\u27f3 MERGE STATE UNKNOWN";
  else if (s.merge_pending) right = "\u27f3 MERGE RUNNING " + std::to_string((long)s.merge_age_s) + "s";
  if (!right.empty()) put(row, std::max(1, maxx - (int)right.size() - 3), right, CP_TITLE, true);

  return row + 1;
}

int drawHeader(int row) {
  auto h = (g_board == Board::Link) ? columnHeaders() : loadHeaders();
  auto w = (g_board == Board::Link) ? columnWidths()  : loadWidths();
  int col = 2;
  for (size_t i = 0; i < h.size(); ++i) {
    put(row, col, h[i], CP_HEAD, true);
    col += w[i];
  }
  ++row;
  int maxy = 0, maxx = 0;
  getmaxyx(stdscr, maxy, maxx);
  (void)maxy;
  put(row, 2, std::string(static_cast<size_t>(std::max(0, maxx - 4)), '-'), CP_DIM);
  return row + 1;
}

void drawRow(int row, const Peer& p, bool selected) {
  auto cells = (g_board == Board::Link) ? peerRow(p) : loadRow(p);
  auto w = (g_board == Board::Link) ? columnWidths() : loadWidths();
  int maxy = 0, maxx = 0;
  getmaxyx(stdscr, maxy, maxx);
  (void)maxy;

  if (selected)
    put(row, 1, std::string(static_cast<size_t>(std::max(0, maxx - 2)), ' '), CP_SELECT);

  int col = 2;
  for (size_t i = 0; i < cells.size() && i < w.size(); ++i) {
    put(row, col, cells[i].text, selected ? CP_SELECT : inkPair(cells[i].ink),
        p.is_dbhost && i == 0);
    col += w[i];
  }
}

void drawFooter(const Snapshot& s, int row) {
  int maxy = 0, maxx = 0;
  getmaxyx(stdscr, maxy, maxx);
  (void)maxy;
  put(row, 2, std::string(static_cast<size_t>(std::max(0, maxx - 4)), '-'), CP_DIM);
  ++row;
  put(row, 2, "* = databasehost", CP_DIM);
  std::string help = "j/k move   TAB link/load   r refresh   q quit";
  put(row, std::max(20, maxx - (int)help.size() - 3), help, CP_DIM);
  ++row;
  if (!s.dbhost_ip.empty())
    put(row, 2, "databasehost: " + s.dbhost_ip, CP_DIM);
  else
    // Not "no data host elected" -- we could not read /etc/hosts. Different
    // problem, and saying which is the whole point of the line.
    put(row, 2, "databasehost: (could not read /etc/hosts)", CP_WARN);
}

}  // namespace

int main(int argc, char** argv) {
  std::string proxy = "127.0.0.1";
  int port = 80;
  std::string domain;

  for (int i = 1; i < argc; ++i) {
    if (!std::strcmp(argv[i], "--proxy") && i + 1 < argc) { proxy = argv[++i]; continue; }
    if (!std::strcmp(argv[i], "--port")  && i + 1 < argc) { port = std::atoi(argv[++i]); continue; }
    if (!std::strcmp(argv[i], "--domain") && i + 1 < argc) { domain = argv[++i]; continue; }
    if (!std::strcmp(argv[i], "-h") || !std::strcmp(argv[i], "--help")) {
      std::fprintf(stderr,
        "usage: frognet_monitor_tui [--proxy HOST] [--port N] [--domain NAME]\n"
        "\n"
        "  Identity is asked for, not assumed: own address -> .1 -> frognet_echo.php.\n"
        "  --domain overrides that for a client that is not on a pond of its own.\n");
      return 0;
    }
  }

  Client client(proxy, port);

  // Identity BEFORE curses takes the terminal, so a failure is readable.
  if (domain.empty()) {
    std::string me = localFrogNetAddress();
    std::string dot1 = me.empty() ? "" : dotOneOf(me);
    if (!dot1.empty()) {
      Identity id = client.echo(dot1, 5);
      if (id.ok) domain = id.domain;
    }
  }
  if (domain.empty()) {
    // [IDENTITY_FAILS_LOUD_V1] No fallback name. Without a domain there are no
    // sensor names to ask for, so there is nothing to draw -- say so and stop
    // rather than opening an empty dashboard that looks like an idle network.
    std::fprintf(stderr,
      "identity failed: could not determine this pond's domain.\n"
      "Pass --domain NAME, or --proxy the address of a node that can answer.\n");
    return 1;
  }

  State state;
  std::thread th(worker, &client, &state, domain);

  initscr();
  cbreak();
  noecho();
  curs_set(0);
  keypad(stdscr, TRUE);
  nodelay(stdscr, TRUE);     // never block on input; the repaint owns the clock
  initColors();

  int selected = 0;
  long long drawn_gen = -1;

  while (true) {
    int ch = getch();
    if (ch == 'q' || ch == 'Q') break;
    if (ch == 'r' || ch == 'R') g_refresh_now.store(true);
    if (ch == '\t') g_board = (g_board == Board::Link) ? Board::Load : Board::Link;
    if (ch == 'j' || ch == KEY_DOWN) ++selected;
    if (ch == 'k' || ch == KEY_UP)   --selected;

    Snapshot s = state.get();

    if (s.peers.empty()) {
      erase();
      int row = drawTitle(s, 0);
      put(row + 1, 2, "waiting for the first refresh\u2026", CP_DIM);
      refresh();
      std::this_thread::sleep_for(std::chrono::milliseconds(80));
      continue;
    }

    if (selected < 0) selected = 0;
    if (selected >= (int)s.peers.size()) selected = (int)s.peers.size() - 1;

    // Repaint on new data or on a keypress; otherwise leave the screen alone.
    if (s.generation != drawn_gen || ch != ERR) {
      drawn_gen = s.generation;
      erase();
      int row = drawTitle(s, 0);
      ++row;
      row = drawHeader(row);
      for (size_t i = 0; i < s.peers.size(); ++i)
        drawRow(row + (int)i, s.peers[i], (int)i == selected);
      drawFooter(s, row + (int)s.peers.size() + 1);
      refresh();
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(80));
  }

  endwin();
  g_stop.store(true);
  th.join();
  return 0;
}
