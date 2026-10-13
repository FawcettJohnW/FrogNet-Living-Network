#!/bin/bash
# syntax check of the files changed since the last commit (T4b, T4c, T5; sandbox helper)
cd /home/claude/clean || exit 2
ok=0; bad=0
files=$( { git status --short | grep -v '^?? docs/' | grep -v '^ D ' | awk '{print $2}'; echo opt/fdf/bin/fdf-wg-impl; echo opt/fdf/src/fdf-source/fdf/bin/fdf-wg-impl; echo opt/frognet_semantic/core/build_id.py; } | sort -u )
for f in $files; do
  [ -f "$f" ] || continue
  case "$f" in
    *.php)
      if php -l "$f" >/dev/null 2>&1; then ok=$((ok+1)); echo "php -l OK   $f"; else bad=$((bad+1)); echo "BAD $f"; fi ;;
    var/www/html/frognet_monitor)
      if python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$f"; then ok=$((ok+1)); echo "py OK       $f"; else bad=$((bad+1)); echo "BAD $f"; fi ;;
    *.sh|*.bash|*/mapInterfaces|*/runMerge*|*/propogate*|*/frognet-netstart|*/runPingEverything|*/frognet_monitor|*/90-frognet-merge|*/fdf-region-create|*/fdf-wg-impl|*/header.sh|*/getEth0Address|*/fdf-region-join|*/fdf-leave)
      if bash -n "$f" 2>/dev/null; then ok=$((ok+1)); echo "bash -n OK  $f"; else bad=$((bad+1)); echo "BAD $f"; fi ;;
    *.py|*/fdf-facts|*/fdf-hub|*/fdf-reach)
      if python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$f"; then ok=$((ok+1)); echo "py OK       $f"; else bad=$((bad+1)); echo "BAD $f"; fi ;;
    *.js)
      if node --check "$f" 2>/dev/null; then ok=$((ok+1)); echo "node OK     $f"; else bad=$((bad+1)); echo "BAD $f"; fi ;;
    *.html)   # T5: the inline <script> of a changed page
      if awk '/<script>/{f=1;next}/<\/script>/{f=0}f' "$f" > /tmp/syntax-page.js && node --check /tmp/syntax-page.js 2>/dev/null; then ok=$((ok+1)); echo "page js OK  $f"; else bad=$((bad+1)); echo "BAD $f"; fi ;;
    etc/sudoers.d/*)
      if visudo -cf "$f" >/dev/null 2>&1; then ok=$((ok+1)); echo "visudo OK   $f"; else bad=$((bad+1)); echo "BAD $f"; fi ;;
  esac
done
for f in fdf-facts fdf-hub fdf-region-create fdf-wg-impl fdf-reach fdf-mapping.sh fdf-ram-manager.sh fdf-member.sh fdf-region-join fdf-leave fdf-pond-add; do
  cmp -s opt/fdf/src/fdf-source/fdf/bin/$f opt/fdf/bin/$f && echo "cmp same    opt/fdf/bin/$f" || { echo "cmp DIFF    $f"; bad=$((bad+1)); }
done
cmp -s opt/fdf/src/fdf-source/fdf/bin/fdf-hub opt/fdf/src/fdf-source/rinab/tools/fdf-hub && echo "cmp same    rinab/tools/fdf-hub" || { echo "cmp DIFF rinab fdf-hub"; bad=$((bad+1)); }
cmp -s opt/fdf/src/fdf-source/fdf/fdf.env opt/fdf/fdf.env && echo "cmp same    opt/fdf/fdf.env" || { echo "cmp DIFF fdf.env"; bad=$((bad+1)); }
for f in fdf_dashboard.html getParticipants.php waitParticipants.php; do   # T5: the FD&F pages, three copies
  for c in opt/fdf/src/fdf-source/fdf/www/fdf opt/fdf/src/fdf-source/rinab/www/fdf; do
    cmp -s opt/fdf/www/fdf/$f $c/$f && echo "cmp same    $c/$f" || { echo "cmp DIFF    $c/$f"; bad=$((bad+1)); }
  done
done
echo "syntax: $ok files OK, $bad bad"
