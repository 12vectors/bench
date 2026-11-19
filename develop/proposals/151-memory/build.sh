#!/bin/sh
# Assemble the #151 Memory proposal pages from src/ into plans/.
#
#     sh develop/proposals/151-memory/build.sh
#
# Each page is one self-contained HTML file: the board's /files/ route
# serves flat names only, runs no script in an .html (CSP sandbox,
# script-src 'none') and sends .css as application/octet-stream, so the
# shared stylesheet is inlined into every page rather than linked.
# A page is: its head, the shared style, the chrome, its body, the tail.
set -eu
cd "$(dirname "$0")"
mkdir -p plans
for page in proposal mock-1-library mock-2-review mock-3-ticket \
            mock-4-guide-vs-history mock-5-states; do
  chrome=src/chrome-mock.html
  [ "$page" = proposal ] && chrome=src/chrome-doc.html
  cat "src/head-$page.html" src/style.css src/style-close.html "$chrome" \
      "src/$page.body.html" src/tail.html > "plans/151-memory-$page.html"
  echo "wrote plans/151-memory-$page.html"
done
