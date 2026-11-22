#!/bin/sh
# Starts as root only to make sure the data folder is writable, then drops to the unprivileged user.
set -e
umask 077   # database (API keys) and audio files readable by the app user only
if [ "$(id -u)" = "0" ]; then
  if ! setpriv --reuid=10001 --regid=10001 --clear-groups test -w /data 2>/dev/null; then
    chown 10001:10001 /data
    chmod 1777 /data   # keep the host's screen agent able to write its token file (Linux hosts)
  fi
  exec setpriv --reuid=10001 --regid=10001 --clear-groups "$@"
fi
exec "$@"
