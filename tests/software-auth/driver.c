/* Test harness for the actual worker and wire translations. Never shipped as a
 * runtime binary. Synthetic AUTH2 material is used unless a private table path
 * is explicitly provided in GIMX_DS4_AUTH2_TABLE. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
static int forbidden_ioctl(int fd, unsigned long request, ...);
#define ioctl forbidden_ioctl
#include "/opt/gimx-src/core/connectors/linux/usb_auth/ds4_usb_auth.c"
#undef ioctl
static int forbidden_ioctl(int fd, unsigned long request, ...)
{
  (void)fd; (void)request;
  abort(); /* Software mode must never contact a USB controller. */
}

int main(void)
{
  struct ds4_usb_auth *auth;
  if (getenv("GIMX_DS4_AUTH2_TABLE")) auth = ds4_usb_open(NULL);
  else {
    auth = calloc(1, sizeof(*auth));
    assert(auth);
    auth->hid = -1;
    auth->software = ds4_software_open(getenv("GIMX_DS4_KEYS"));
    if (!auth->software) { free(auth); return 2; }
    for (size_t i = 0; i < sizeof(auth->table); ++i) ((uint8_t *)auth->table)[i] = i;
    memset(auth->salt, 0xa5, sizeof(auth->salt));
    auth->auth2_enabled = 1;
    assert(socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, auth->channel) == 0);
    assert(pthread_create(&auth->worker, NULL, worker, auth) == 0);
  }
  if (!auth) return 2;
  uint8_t sizes[2], packet[65];
  while (fread(sizes, 1, 2, stdin) == 2) {
    unsigned length = sizes[0] | (unsigned)sizes[1] << 8;
    if (!length || length > sizeof(packet)) break;
    if (fread(packet, 1, length, stdin) != length) break;
    assert(ds4_usb_submit(auth, packet, length) == 0);
    struct pollfd ready = {.fd=ds4_usb_fd(auth), .events=POLLIN};
    assert(poll(&ready, 1, 5000) == 1);
    struct ds4_usb_reply reply;
    assert(ds4_usb_receive(auth, &reply) == 0);
    sizes[0] = reply.length;
    sizes[1] = reply.length >> 8;
    assert(fwrite(sizes, 1, 2, stdout) == 2);
    assert(fwrite(reply.data, 1, reply.length, stdout) == reply.length);
    fflush(stdout);
  }
  ds4_usb_close(auth);
  return 0;
}
