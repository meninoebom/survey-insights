#!/usr/bin/env bash
# Double-click this in Finder to launch the survey TUI (macOS). It changes into
# the project folder and runs ./run-tui.sh for you, so there is nothing to type.
# The only prerequisite is Docker installed and running.
#
# First time only: macOS may block a downloaded file. If a double-click is
# refused, right-click the file, choose Open, then Open again.

cd "$(dirname "$0")" || {
  echo "Could not find the project folder next to this file."
  read -r -p "Press Return to close this window. "
  exit 1
}

./run-tui.sh
status=$?

# 0 = quit with 'q', 130 = quit with Ctrl+C. Both are normal exits.
if [ "$status" -eq 0 ] || [ "$status" -eq 130 ]; then
  exit 0
fi

echo
echo "Something went wrong above and the TUI did not start."
read -r -p "Press Return to close this window. "
exit 1
