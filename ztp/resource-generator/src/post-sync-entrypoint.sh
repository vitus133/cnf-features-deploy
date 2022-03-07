#!/bin/bash

source $(dirname "$0")/common.sh
init $1

python watcher.py $(./get-pre-sync-rv.sh $1) $RESOURCE_NAME debug

unlock_message=$(oc delete configmap/openshift-ztp-lock)
unlock_result=$?
"ztp-hooks.postsync $(date -R) INFO [post-sync-entrypoint] Sync unlock: $unlock_message, result $unlock_result 
