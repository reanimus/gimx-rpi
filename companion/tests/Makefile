obj-m += dummy_hcd.o
all:
	$(MAKE) -C /lib/modules/$(shell uname -r)/build M=$(CURDIR) modules
