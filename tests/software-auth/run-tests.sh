#!/bin/sh
set -eu
cd "$(dirname "$0")"
cc -Wall -Wextra -Werror -std=gnu11 -O1 -g -pthread \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  -o software-driver driver.c \
  /opt/gimx-src/core/connectors/linux/usb_auth/ds4_software_auth.c -lmhash -lcrypto
python3 test_software.py
