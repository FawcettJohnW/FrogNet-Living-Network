cmake_minimum_required(VERSION 3.16)
project(frognet_monitor_cpp
        VERSION 1.0.0
        DESCRIPTION "FrogNet monitor - C++ reader for the shared memory"
        LANGUAGES CXX)

# ---------------------------------------------------------------------------
# The point of this project is that a FrogNet client needs nothing but an HTTP
# client and a JSON parser. libcurl is the HTTP client; the JSON parser is 200
# lines in src/json.cpp, vendored so there is no package to fetch. ncurses is
# only for the dashboard and the headless tool does not link it.
#
# Nothing here implements any part of FrogNet. There is no wire protocol, no
# codex, no SDK. The whole contract is:
#     GET http://127.0.0.1:80/...   with   Host: databasehost.frognet
# ---------------------------------------------------------------------------

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

add_compile_options(-Wall -Wextra -Wpedantic)

find_package(CURL REQUIRED)

# --- core: parsers + HTTP + the two API calls. No terminal, no ncurses. -----
add_library(frognet_core STATIC
  src/json.cpp
  src/parsers.cpp
  src/http.cpp
  src/dashboard.cpp
  src/frognet.cpp
)
target_include_directories(frognet_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(frognet_core PUBLIC CURL::libcurl)

# --- headless reader: discovers, reads, prints. The whole demonstration. ----
add_executable(frognet_monitor src/main_headless.cpp)
target_link_libraries(frognet_monitor PRIVATE frognet_core)

# --- oracle: runs the shared vectors. Built and run by `ctest`. -------------
add_executable(frognet_parser_oracle tests/parser_oracle.cpp)
target_link_libraries(frognet_parser_oracle PRIVATE frognet_core)

enable_testing()
add_test(NAME parser_vectors
         COMMAND frognet_parser_oracle
                 ${CMAKE_CURRENT_SOURCE_DIR}/../frognet_monitor_shared/parser_vectors.json)

# The dashboard's formatting and status thresholds are testable without a
# terminal, which is the reason they live in dashboard.cpp and not in the
# drawing code.
add_executable(frognet_dashboard_oracle tests/dashboard_oracle.cpp)
target_link_libraries(frognet_dashboard_oracle PRIVATE frognet_core)
add_test(NAME dashboard_model COMMAND frognet_dashboard_oracle)

# --- curses dashboard ------------------------------------------------------
# ON by default. Turn it off for a target without ncurses; the headless tool
# still builds and still demonstrates the whole client contract.
option(FROGNET_BUILD_TUI "Build the ncurses dashboard" ON)
if(FROGNET_BUILD_TUI)
  find_package(Curses REQUIRED)
  add_executable(frognet_monitor_tui src/main_tui.cpp)
  target_include_directories(frognet_monitor_tui PRIVATE ${CURSES_INCLUDE_DIRS})
  target_link_libraries(frognet_monitor_tui PRIVATE frognet_core ${CURSES_LIBRARIES})
endif()

install(TARGETS frognet_monitor RUNTIME DESTINATION bin)
if(FROGNET_BUILD_TUI)
  install(TARGETS frognet_monitor_tui RUNTIME DESTINATION bin)
endif()
