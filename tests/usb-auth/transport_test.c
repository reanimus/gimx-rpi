/* Exercise the real helper with only hidraw ioctls replaced by a USB donor.
 * CRC fixtures are generated independently with Python's zlib.
 */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <sys/ioctl.h>
static int fake_ioctl(int fd, unsigned long request, ...);
#define ioctl fake_ioctl
#include "/opt/gimx-src/core/connectors/linux/usb_auth/ds4_usb_auth.c"
#undef ioctl
#include "fixtures.h"
#include "auth2_fixtures.h"

static struct ds4_usb_auth test_transport = {.hid=1};
static uint8_t donor[64], written[64];
static int calls, fail_errno, short_read, wrong_bus;
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition = PTHREAD_COND_INITIALIZER;
static int block_usb, entered_usb;

static int fake_ioctl(int fd, unsigned long request, ...)
{
  (void)fd;
  va_list args;
  va_start(args, request);
  void *data = va_arg(args, void *);
  va_end(args);
  if (request == HIDIOCGRAWINFO) {
    struct hidraw_devinfo *info = data;
    info->bustype = wrong_bus ? BUS_BLUETOOTH : BUS_USB;
    info->vendor = 0x054c;
    info->product = 0x09cc;
    return 0;
  }
  ++calls;
  pthread_mutex_lock(&mutex);
  entered_usb = 1;
  pthread_cond_broadcast(&condition);
  while (block_usb) pthread_cond_wait(&condition, &mutex);
  pthread_mutex_unlock(&mutex);
  if (fail_errno) { errno = fail_errno; return -1; }
  size_t length = _IOC_SIZE(request);
  assert(length <= sizeof(donor));
  if (_IOC_NR(request) == 0x07) {
    assert(*(uint8_t *)data == donor[0]);
    memcpy(data, donor, length);
  } else {
    assert(_IOC_NR(request) == 0x06 && length == sizeof(written));
    memcpy(written, data, length);
  }
  return (int)length - short_read;
}

static void test_translation(void)
{
  struct ds4_usb_reply reply;
  assert(crc32((const uint8_t *)"123456789", 9) == 0xcbf43926);
  exchange(&test_transport, bt_f0, sizeof(bt_f0), &reply);
  assert(reply.length == 1 && reply.data[0] == 0 && !reply.error);
  assert(!memcmp(written, usb_f0, sizeof(usb_f0)));
  memcpy(donor, usb_f1, sizeof(usb_f1));
  uint8_t get[] = {0x43, 0xf1};
  exchange(&test_transport, get, sizeof(get), &reply);
  assert(reply.length == sizeof(bt_f1) && !reply.error);
  assert(!memcmp(reply.data, bt_f1, sizeof(bt_f1)));
  memcpy(donor, usb_f2, sizeof(usb_f2));
  get[1] = 0xf2;
  exchange(&test_transport, get, sizeof(get), &reply);
  assert(reply.length == sizeof(bt_f2) && !memcmp(reply.data, bt_f2, sizeof(bt_f2)));

  for (size_t i = 0; i < 49; ++i) donor[i] = i;
  donor[0] = 0xa3;
  get[1] = 0xa3;
  exchange(&test_transport, get, sizeof(get), &reply);
  assert(reply.length == 50 && !memcmp(reply.data + 1, donor, 49));
  get[1] = 0x06;
  exchange(&test_transport, get, sizeof(get), &reply);
  assert(reply.length == 54 && reply.data[1] == 0x06 && valid_crc(reply.data, 54));
  assert(!memcmp(reply.data + 2, donor + 1, 48));

  donor[0] = 0x02;
  get[1] = 0x02;
  exchange(&test_transport, get, sizeof(get), &reply);
  const uint8_t grouped[] = {7,8,11,12,15,16,9,10,13,14,17,18};
  assert(reply.length == 38 && !memcmp(reply.data + 8, grouped, 12));
  assert(!memcmp(reply.data + 20, donor + 19, 18));
  get[1] = 0x05;
  exchange(&test_transport, get, sizeof(get), &reply);
  assert(reply.length == 42 && reply.data[1] == 5 && valid_crc(reply.data, 42));
  assert(!memcmp(reply.data + 8, grouped, 12));
  puts("PASS: F0/F1/F2 independent CRC fixtures; calibration and firmware translation");
}

static void test_rejections(void)
{
  struct ds4_usb_reply reply;
  const uint8_t unsupported[][2] = {{0x53,3},{0x43,4},{0x53,0x13},{0x53,0x14},{0x43,0xff}};
  int before = calls;
  for (size_t i = 0; i < sizeof(unsupported)/sizeof(*unsupported); ++i) {
    exchange(&test_transport, unsupported[i], 2, &reply);
    assert(reply.unsupported && reply.data[0] == 3 && calls == before);
  }
  uint8_t corrupt[65];
  memcpy(corrupt, bt_f0, sizeof(corrupt));
  corrupt[5] ^= 1;
  exchange(&test_transport, corrupt, sizeof(corrupt), &reply);
  assert(reply.data[0] == 4 && calls == before);
  exchange(&test_transport, bt_f0, sizeof(bt_f0)-1, &reply);
  assert(reply.data[0] == 4 && calls == before);
  for (size_t n = 0; n < 2; ++n) {
    exchange(&test_transport, bt_f0, n, &reply);
    assert(reply.data[0] == 4 && calls == before);
  }
  uint8_t get[] = {0x4b, 0xf1, 63, 0};
  exchange(&test_transport, get, sizeof(get), &reply);
  assert(reply.data[0] == 4 && calls == before);
  memcpy(donor, usb_f1, sizeof(donor));
  get[2] = 64;
  exchange(&test_transport, get, sizeof(get), &reply);
  assert(reply.length == 65);
  donor[63] ^= 1;
  exchange(&test_transport, get, sizeof(get), &reply);
  assert(reply.error == EBADMSG && reply.data[0] == 0x0e);
  memcpy(donor, usb_f1, sizeof(donor));
  short_read = 1;
  exchange(&test_transport, get, sizeof(get), &reply);
  assert(reply.error == EPROTO && reply.data[0] == 0x0e);
  short_read = 0;
  fail_errno = ENODEV;
  exchange(&test_transport, bt_f0, sizeof(bt_f0), &reply);
  assert(reply.error == ENODEV && reply.data[0] == 0x0e);
  fail_errno = 0;
  puts("PASS: malformed/unsupported reports never reach USB; CRC, short transfer and unplug errors fail closed");
}

static void test_worker(void)
{
  char path[] = "/tmp/gimx-usb-test-XXXXXX";
  int fd = mkstemp(path);
  assert(fd >= 0);
  close(fd);
  wrong_bus = 1;
  assert(!ds4_usb_open(path) && errno == ENODEV);
  wrong_bus = 0;
  struct ds4_usb_auth *auth = ds4_usb_open(path);
  assert(auth && ds4_usb_alive(auth));
  assert(!ds4_usb_open(path) && (errno == EAGAIN || errno == EWOULDBLOCK));
  assert(ds4_usb_submit(auth, bt_f0, 66) == -1 && errno == EMSGSIZE);
  pthread_mutex_lock(&mutex);
  block_usb = 1;
  entered_usb = 0;
  pthread_mutex_unlock(&mutex);
  assert(ds4_usb_submit(auth, bt_f0, sizeof(bt_f0)) == 0);
  pthread_mutex_lock(&mutex);
  while (!entered_usb) pthread_cond_wait(&condition, &mutex);
  pthread_mutex_unlock(&mutex);
  assert(ds4_usb_submit(auth, bt_f0, sizeof(bt_f0)) == -1 && errno == EBUSY);
  struct ds4_usb_reply reply;
  assert(ds4_usb_receive(auth, &reply) == -1 && errno == EAGAIN);
  pthread_mutex_lock(&mutex);
  block_usb = 0;
  pthread_cond_broadcast(&condition);
  pthread_mutex_unlock(&mutex);
  struct pollfd ready = {.fd=ds4_usb_fd(auth), .events=POLLIN};
  assert(poll(&ready, 1, 2000) == 1);
  assert(ds4_usb_receive(auth, &reply) == 0 && reply.data[0] == 0);
  assert(ds4_usb_submit(auth, bt_f0, sizeof(bt_f0)) == 0);
  assert(poll(&ready, 1, 2000) == 1);
  assert(ds4_usb_receive(auth, &reply) == 0);
  ds4_usb_close(auth);
  auth = ds4_usb_open(path); /* Last close releases the advisory donor lock. */
  assert(auth);
  int cable[2];
  assert(socketpair(AF_UNIX, SOCK_STREAM, 0, cable) == 0);
  close(auth->hid);
  auth->hid = cable[0];
  assert(ds4_usb_alive(auth));
  close(cable[1]);
  assert(!ds4_usb_alive(auth));
  ds4_usb_close(auth);
  unlink(path);
  puts("PASS: USB bus validation, donor locking, nonblocking main thread, transaction ordering, cleanup and unplug detection");
}

static void test_auth2(void)
{
  struct ds4_usb_auth auth = {.hid = 1, .auth2_enabled = 1};
  for (size_t i = 0; i < sizeof(auth.table); ++i) ((uint8_t *)auth.table)[i] = i;
  for (size_t i = 0; i < sizeof(auth.salt); ++i) auth.salt[i] = 0xa0 + i;
  uint8_t set[41] = {0x53, 3}, get[] = {0x4b, 4, 47, 0};
  struct ds4_usb_reply reply;
  int before = calls;
  auth_exchange(&auth, get, sizeof(get), &reply);
  assert(reply.length == 1 && reply.data[0] == 1); /* GET before any SET */
  for (int slot = 0; slot < 16; ++slot) {
    set[2] = 254 + slot; /* Include sequence wrap. */
    set[3] = slot;
    for (int i = 0; i < 32; ++i) set[4 + i] = i + slot;
    put_crc(set, 40);
    auth_exchange(&auth, set, 40, &reply);
    assert(reply.length == 1 && reply.data[0] == 0 && !reply.error && !reply.unsupported);
    auth_exchange(&auth, get, sizeof(get), &reply);
    assert(reply.length == 48 && !memcmp(reply.data, auth2_expected[slot], 48));
    /* Repeated GET is stable; an undersized GET must not discard the reply. */
    get[2] = 46;
    auth_exchange(&auth, get, sizeof(get), &reply);
    assert(reply.length == 1 && reply.data[0] == 4);
    get[2] = 47;
    auth_exchange(&auth, (uint8_t[]){0x43,4}, 2, &reply);
    assert(reply.length == 48 && !memcmp(reply.data, auth2_expected[slot], 48));
    auth_exchange(&auth, get, 3, &reply);
    assert(reply.length == 1 && reply.data[0] == 4);
  }
  assert(calls == before); /* AUTH2 never calls USB. */
  uint8_t expected[48];
  memcpy(expected, auth.auth2_response, sizeof(expected));
  auth_exchange(&auth, bt_f0, sizeof(bt_f0), &reply); /* RSA traffic coexists. */
  assert(reply.data[0] == 0 && calls == before + 1);
  auth_exchange(&auth, get, sizeof(get), &reply);
  assert(reply.length == 48 && !memcmp(reply.data, expected, 48));
  for (int invalid = 0; invalid < 5; ++invalid) {
    set[3] = 0;
    put_crc(set, 40);
    size_t length = 40;
    if (invalid == 0) set[4] ^= 1; /* Corrupt CRC. */
    if (invalid == 1) { set[3] = 16; put_crc(set, 40); }
    if (invalid == 2) { set[3] = 255; put_crc(set, 40); }
    if (invalid == 3) length = 39;
    if (invalid == 4) length = 41;
    auth.auth2_ready = 1;
    auth_exchange(&auth, set, length, &reply);
    assert(reply.length == 1 && reply.data[0] == 4 && !reply.error);
    auth_exchange(&auth, get, sizeof(get), &reply);
    assert(reply.length == 1 && reply.data[0] == 1); /* No stale result. */
  }
  assert(calls == before + 1);
  puts("PASS: AUTH2 independent SHA-1/CRC fixtures, all slots, sequence wrap, retries, malformed requests and RSA interleaving");
}

static void test_auth2_load(void)
{
  struct ds4_usb_auth auth = {0};
  char path[] = "/tmp/gimx-auth2-test-XXXXXX";
  int fd = mkstemp(path);
  assert(fd >= 0);
  assert(auth2_load(&auth, path) == -1 && errno == EINVAL);
  uint8_t zero[513] = {0};
  assert(write(fd, zero, 512) == 512);
  assert(auth2_load(&auth, path) == -1 && errno == EBADMSG);
  assert(!auth.auth2_enabled && !memcmp(auth.table, zero, 512));
  assert(write(fd, zero, 1) == 1);
  assert(auth2_load(&auth, path) == -1 && errno == EINVAL);
  close(fd);
  unlink(path);
  assert(auth2_load(&auth, path) == -1 && errno == ENOENT);
  puts("PASS: missing, empty, oversized and wrong-hash tables rejected");
  const char *real = getenv("GIMX_TEST_AUTH2_TABLE");
  if (!real) { puts("SKIP: private table known-answer test (set GIMX_TEST_AUTH2_TABLE)"); return; }
  assert(auth2_load(&auth, real) == 0);
  assert(auth.auth2_enabled && !auth.auth2_ready);
  /* Public I2C vector: itewqq/ps4-controller-weak-auth. No table embedded. */
  static const uint8_t challenge[32] = {
    0x78,0x06,0x4a,0xc4,0x11,0xf6,0xc4,0x3b,0x97,0x96,0xb2,0xdc,0x93,0x4f,0x9a,0x32,
    0x04,0x52,0x60,0x05,0x27,0x0e,0xc7,0x1e,0xd0,0x82,0x2c,0xf4,0x44,0x9d,0x16,0xd1
  };
  static const uint8_t answer[42] = {
    0x11,0x00,0xc0,0x11,0x47,0x0c,0x76,0x08,0xeb,0x92,0x33,0xd9,0xf2,0x12,0xb5,0x7c,
    0x0f,0xae,0x00,0x91,0xc3,0x2a,0xcd,0x53,0xe3,0xe2,0xd8,0xa8,0x3f,0x1a,0x45,0x5a,
    0x47,0x70,0xce,0x16,0x6f,0x5e,0x74,0x8c,0x41,0x65
  };
  memcpy(auth.salt, answer + 22, 20);
  uint8_t set[40] = {0x53,3,0x11,0};
  memcpy(set + 4, challenge, 32);
  put_crc(set, sizeof(set));
  struct ds4_usb_reply reply;
  auth_exchange(&auth, set, sizeof(set), &reply);
  assert(reply.length == 1 && reply.data[0] == 0);
  auth_exchange(&auth, (uint8_t[]){0x4b,4,47,0}, 4, &reply);
  assert(reply.length == 48 && !memcmp(reply.data + 2, answer, 42) && valid_crc(reply.data, 48));
  explicit_bzero(&auth, sizeof(auth));
  /* Exercise the actual worker's dispatch, environment loading and session reset. */
  strcpy(path, "/tmp/gimx-auth2-test-XXXXXX");
  fd = mkstemp(path);
  assert(fd >= 0);
  close(fd);
  assert(setenv("GIMX_DS4_AUTH2_TABLE", real, 1) == 0);
  struct ds4_usb_auth *worker_auth = ds4_usb_open(path);
  assert(worker_auth && worker_auth->auth2_enabled && !worker_auth->auth2_ready);
  assert(ds4_usb_submit(worker_auth, set, sizeof(set)) == 0);
  struct pollfd ready = {.fd=ds4_usb_fd(worker_auth), .events=POLLIN};
  assert(poll(&ready, 1, 2000) == 1 && ds4_usb_receive(worker_auth, &reply) == 0);
  assert(reply.data[0] == 0 && !reply.error);
  assert(ds4_usb_submit(worker_auth, (uint8_t[]){0x43,4}, 2) == 0);
  assert(poll(&ready, 1, 2000) == 1 && ds4_usb_receive(worker_auth, &reply) == 0);
  assert(reply.length == 48 && valid_crc(reply.data, 48));
  ds4_usb_close(worker_auth);
  worker_auth = ds4_usb_open(path);
  assert(worker_auth && !worker_auth->auth2_ready);
  ds4_usb_close(worker_auth);
  unsetenv("GIMX_DS4_AUTH2_TABLE");
  unlink(path);
  puts("PASS: external controller table matches public known answer; real worker dispatch and new-session reset");
}

int main(void)
{
  test_translation();
  test_rejections();
  test_worker();
  test_auth2();
  test_auth2_load();
  return 0;
}
