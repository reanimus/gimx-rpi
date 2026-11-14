CC ?= cc
CFLAGS ?= -O2 -g -Wall -Wextra -Werror -std=gnu11
all: pairing-gadget
pairing-gadget: src/pairing.c src/descriptors.h
	$(CC) $(CFLAGS) -pthread -o $@ src/pairing.c
clean:
	rm -f pairing-gadget
.PHONY: all clean
