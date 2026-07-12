#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: encrypted_backup.sh SOURCE_DB DESTINATION.enc" >&2
  exit 2
fi
if [ -z "${AI_CIO_BACKUP_PASSPHRASE:-}" ]; then
  echo "AI_CIO_BACKUP_PASSPHRASE is required" >&2
  exit 2
fi

openssl enc -aes-256-cbc -salt -pbkdf2 -in "$1" -out "$2" -pass env:AI_CIO_BACKUP_PASSPHRASE
shasum -a 256 "$2" > "$2.sha256"
