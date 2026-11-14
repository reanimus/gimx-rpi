#!/bin/sh
set -eu
cd "$(dirname "$0")"
cc -O1 -g -std=gnu11 -ffunction-sections -fdata-sections \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  -I/opt/gimx-src/core -I/opt/gimx-src/shared \
  -Wl,--gc-sections -o status-c-tests udp_dispatch_test.c btds4_state_test.c -lmhash
./status-c-tests
