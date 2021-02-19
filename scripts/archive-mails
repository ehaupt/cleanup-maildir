#!/bin/sh

BASE=$HOME/Maildir
ARCHIVEBASE=$HOME/Maildir/archive.

for folder in `find $BASE -maxdepth 1 -type d \! -regex '.*/archive\..*' \! -name cur \! -name tmp \! -name new`
do
  folder=$(basename $folder)
  if [ "${folder}" = "Maildir" ]; then folder=INBOX; fi
  ./cleanup-maildir.py --archive-folder=${ARCHIVEBASE}${folder} --maildir-root=$BASE --folder-prefix= --age=365 -d 1 -k -u -v archive ${folder}
done
