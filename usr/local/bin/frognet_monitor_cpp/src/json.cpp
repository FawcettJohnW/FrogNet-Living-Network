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
// json.cpp — a small JSON reader, vendored so this project has no package
// dependency beyond libcurl. It handles what the API returns: the envelope, and
// arbitrary jsonData payloads whose shape a client cannot know in advance.
#include "frognet.h"

#include <cctype>
#include <cmath>
#include <cstdlib>
#include <sstream>

namespace frognet {
namespace {

struct Parser {
  const std::string& s;
  size_t i = 0;
  std::string err;

  explicit Parser(const std::string& in) : s(in) {}

  void ws() {
    while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) ++i;
  }
  bool eof() const { return i >= s.size(); }
  bool fail(const std::string& m) { if (err.empty()) err = m; return false; }

  bool lit(const char* w) {
    size_t n = 0; while (w[n]) ++n;
    if (s.compare(i, n, w) != 0) return false;
    i += n; return true;
  }

  bool value(Json& out) {
    ws();
    if (eof()) return fail("unexpected end of input");
    switch (s[i]) {
      case 'n': if (lit("null"))  { out = Json{}; return true; } return fail("bad literal");
      case 't': if (lit("true"))  { out.type = Json::Type::Bool; out.boolean = true;  return true; } return fail("bad literal");
      case 'f': if (lit("false")) { out.type = Json::Type::Bool; out.boolean = false; return true; } return fail("bad literal");
      case '"': { out.type = Json::Type::String; return string(out.str); }
      case '[': return array(out);
      case '{': return object(out);
      default:  return number(out);
    }
  }

  bool string(std::string& out) {
    if (s[i] != '"') return fail("expected string");
    ++i;
    out.clear();
    while (true) {
      if (eof()) return fail("unterminated string");
      char c = s[i++];
      if (c == '"') return true;
      if (c != '\\') { out += c; continue; }
      if (eof()) return fail("trailing escape");
      char e = s[i++];
      switch (e) {
        case '"': out += '"';  break;
        case '\\': out += '\\'; break;
        case '/': out += '/';  break;
        case 'b': out += '\b'; break;
        case 'f': out += '\f'; break;
        case 'n': out += '\n'; break;
        case 'r': out += '\r'; break;
        case 't': out += '\t'; break;
        case 'u': {
          if (i + 4 > s.size()) return fail("short \\u escape");
          unsigned cp = static_cast<unsigned>(std::strtoul(s.substr(i, 4).c_str(), nullptr, 16));
          i += 4;
          // Surrogate pair; a lone surrogate becomes U+FFFD rather than
          // producing invalid UTF-8 downstream.
          if (cp >= 0xD800 && cp <= 0xDBFF) {
            if (i + 6 <= s.size() && s[i] == '\\' && s[i + 1] == 'u') {
              unsigned lo = static_cast<unsigned>(std::strtoul(s.substr(i + 2, 4).c_str(), nullptr, 16));
              if (lo >= 0xDC00 && lo <= 0xDFFF) {
                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                i += 6;
              } else cp = 0xFFFD;
            } else cp = 0xFFFD;
          } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
            cp = 0xFFFD;
          }
          if (cp < 0x80) out += static_cast<char>(cp);
          else if (cp < 0x800) {
            out += static_cast<char>(0xC0 | (cp >> 6));
            out += static_cast<char>(0x80 | (cp & 0x3F));
          } else if (cp < 0x10000) {
            out += static_cast<char>(0xE0 | (cp >> 12));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
          } else {
            out += static_cast<char>(0xF0 | (cp >> 18));
            out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
          }
          break;
        }
        default: return fail("bad escape");
      }
    }
  }

  bool number(Json& out) {
    size_t start = i;
    if (!eof() && (s[i] == '-' || s[i] == '+')) ++i;
    bool any = false;
    while (!eof() && (std::isdigit(static_cast<unsigned char>(s[i])) || s[i] == '.' ||
                      s[i] == 'e' || s[i] == 'E' || s[i] == '-' || s[i] == '+')) { ++i; any = true; }
    if (!any) return fail("expected value");
    out.type = Json::Type::Number;
    out.number = std::strtod(s.substr(start, i - start).c_str(), nullptr);
    return true;
  }

  bool array(Json& out) {
    out.type = Json::Type::Array;
    ++i; ws();
    if (!eof() && s[i] == ']') { ++i; return true; }
    while (true) {
      Json v;
      if (!value(v)) return false;
      out.array.push_back(std::move(v));
      ws();
      if (eof()) return fail("unterminated array");
      if (s[i] == ',') { ++i; continue; }
      if (s[i] == ']') { ++i; return true; }
      return fail("expected , or ] in array");
    }
  }

  bool object(Json& out) {
    out.type = Json::Type::Object;
    ++i; ws();
    if (!eof() && s[i] == '}') { ++i; return true; }
    while (true) {
      ws();
      std::string k;
      if (eof() || s[i] != '"') return fail("expected object key");
      if (!string(k)) return false;
      ws();
      if (eof() || s[i] != ':') return fail("expected : after key");
      ++i;
      Json v;
      if (!value(v)) return false;
      out.object.emplace_back(std::move(k), std::move(v));
      ws();
      if (eof()) return fail("unterminated object");
      if (s[i] == ',') { ++i; continue; }
      if (s[i] == '}') { ++i; return true; }
      return fail("expected , or } in object");
    }
  }
};

void dump(const Json& j, std::ostringstream& o) {
  switch (j.type) {
    case Json::Type::Null:   o << "null"; break;
    case Json::Type::Bool:   o << (j.boolean ? "true" : "false"); break;
    case Json::Type::Number: {
      if (j.number == std::floor(j.number) && std::fabs(j.number) < 1e15)
        o << static_cast<long long>(j.number);
      else o << j.number;
      break;
    }
    case Json::Type::String: {
      o << '"';
      for (char c : j.str) {
        switch (c) {
          case '"':  o << "\\\""; break;
          case '\\': o << "\\\\"; break;
          case '\n': o << "\\n";  break;
          case '\r': o << "\\r";  break;
          case '\t': o << "\\t";  break;
          default:
            if (static_cast<unsigned char>(c) < 0x20) o << ' ';
            else o << c;
        }
      }
      o << '"';
      break;
    }
    case Json::Type::Array: {
      o << '[';
      for (size_t k = 0; k < j.array.size(); ++k) { if (k) o << ','; dump(j.array[k], o); }
      o << ']';
      break;
    }
    case Json::Type::Object: {
      o << '{';
      for (size_t k = 0; k < j.object.size(); ++k) {
        if (k) o << ',';
        Json key; key.type = Json::Type::String; key.str = j.object[k].first;
        dump(key, o); o << ':'; dump(j.object[k].second, o);
      }
      o << '}';
      break;
    }
  }
}

}  // namespace

const Json* Json::find(const std::string& key) const {
  if (type != Type::Object) return nullptr;
  for (const auto& kv : object) if (kv.first == key) return &kv.second;
  return nullptr;
}

std::string Json::atString(const std::string& key) const {
  const Json* v = find(key);
  if (!v) return "";
  if (v->type == Type::String) return v->str;
  if (v->type == Type::Number) {
    std::ostringstream o;
    if (v->number == std::floor(v->number) && std::fabs(v->number) < 1e15)
      o << static_cast<long long>(v->number);
    else o << v->number;
    return o.str();
  }
  if (v->type == Type::Bool) return v->boolean ? "true" : "false";
  return "";
}

std::string Json::toCompactString() const {
  std::ostringstream o;
  dump(*this, o);
  return o.str();
}

Json Json::parse(const std::string& text, std::string* error) {
  Parser p(text);
  Json out;
  if (!p.value(out)) {
    if (error) *error = p.err.empty() ? "parse failed" : p.err;
    return Json{};
  }
  p.ws();
  if (!p.eof() && error) *error = "trailing data after value";
  return out;
}

}  // namespace frognet
