/* Compile the actual patched implementation; fake only the clock and L2CAP transport. */
#include <assert.h>
#include <limits.h>
#include "/opt/gimx-src/core/connectors/btds4.c"
static gtime test_now = 10000000000ULL;
static int send_result;
static int usb_present;
static struct ds4_usb_reply usb_reply;
static int usb_receive_error;
int ds4_usb_receive(struct ds4_usb_auth *auth, struct ds4_usb_reply *reply) {
  assert(auth == (struct ds4_usb_auth *)1);
  if (usb_receive_error) { errno = usb_receive_error; return -1; }
  *reply = usb_reply;
  return 0;
}
int ds4_usb_alive(const struct ds4_usb_auth *auth) {
  assert(auth == (const struct ds4_usb_auth *)1);
  return usb_present;
}
gtime gtime_gettime(void) { return test_now; }
static int fake_send(int channel, const unsigned char *buf, int len, int blocking) {
  (void)channel; (void)buf; (void)blocking;
  if (send_result == -1) { errno = EAGAIN; return -1; }
  return send_result == -2 ? len - 1 : len;
}
static int fake_close(int channel) { (void)channel; return 0; }
s_l2cap_abs *l2cap_abs_get(void) {
  static s_l2cap_abs impl = { .send = fake_send, .close = fake_close };
  return &impl;
}
void test_btds4_state(void) {
  uint32_t flags, age;
  btds4_get_status(0, &flags, &age);
  assert(flags == 0 && age == UINT32_MAX); // static zero IDs must NOT look connected
  btds4_get_status(-1, &flags, &age); assert(flags == 0);
  btds4_get_status(MAX_CONTROLLERS, &flags, &age); assert(flags == 0);
  struct btds4_state *s = &states[0];
  s->status_initialized = 1;
  s->ps4_channels.control.id = s->ps4_channels.interrupt.id = -1;
  s->ds4_channels.control.id = s->ds4_channels.interrupt.id = -1;
  btds4_get_status(0, &flags, &age); assert(flags == BTDS4_STATUS_INITIALIZED);
  s->ps4_channels.control.id = 3; s->ps4_channels.control.pending = 1;
  s->ps4_channels.interrupt.id = 4; s->ps4_channels.interrupt.pending = 1;
  btds4_get_status(0, &flags, &age);
  assert(flags == (BTDS4_STATUS_INITIALIZED | BTDS4_STATUS_PS4_CONTROL_PENDING | BTDS4_STATUS_PS4_INTERRUPT_PENDING));
  s->ps4_channels.control.pending = s->ps4_channels.interrupt.pending = 0;
  s->ds4_channels.control.id = 5; s->ds4_channels.interrupt.id = 6;
  btds4_get_status(0, &flags, &age); assert(flags == 31 && age == UINT32_MAX);
  s_report_ds4 report = {0};
  send_result = -1; assert(btds4_send_interrupt(0, &report, 1) == 0);
  btds4_get_status(0, &flags, &age); assert(!(flags & BTDS4_STATUS_REPORT_SENT));
  send_result = -2; btds4_send_interrupt(0, &report, 1);
  btds4_get_status(0, &flags, &age); assert(!(flags & BTDS4_STATUS_REPORT_SENT));
  send_result = 0; assert(btds4_send_interrupt(0, &report, 1) > 0);
  test_now += 123000000ULL;
  btds4_get_status(0, &flags, &age); assert(flags == (31 | BTDS4_STATUS_REPORT_SENT) && age == 123);
  close_ds4_control((void*)0); // Exercise an actual disconnect callback.
  btds4_get_status(0, &flags, &age); assert(!(flags & BTDS4_STATUS_DS4_CONTROL));
  close_ps4_control((void*)0);
  btds4_get_status(0, &flags, &age);
  assert(flags == (BTDS4_STATUS_INITIALIZED | BTDS4_STATUS_SHUTDOWN | BTDS4_STATUS_REPORT_SENT));
  test_now += (gtime)UINT32_MAX * 1000000ULL;
  btds4_get_status(0, &flags, &age); assert(age == UINT32_MAX - 1);
  s->sys.shutdown = 0;
  s->ps4_channels.control.id = 3;
  s->ds4_channels.control.id = s->ds4_channels.interrupt.id = -1;
  s->auth_mode = 1;
  s->usb_auth = (struct ds4_usb_auth *)1;
  usb_present = 1;
  btds4_get_status(0, &flags, &age);
  assert((flags & (BTDS4_STATUS_USB_AUTH | BTDS4_STATUS_USB_CONNECTED)) ==
         (BTDS4_STATUS_USB_AUTH | BTDS4_STATUS_USB_CONNECTED));
  assert(!(flags & (BTDS4_STATUS_DS4_CONTROL | BTDS4_STATUS_DS4_INTERRUPT)));
  assert(btds4_send_interrupt(0, &report, 1) > 0); // No donor Bluetooth socket required.
  usb_reply = (struct ds4_usb_reply){.length=1, .header=0x43, .report=4,
                                   .unsupported=1, .data={3}};
  assert(read_usb_auth((void*)0) == 0);
  btds4_get_status(0, &flags, &age); assert(flags & BTDS4_STATUS_USB_UNSUPPORTED);
  usb_receive_error = EAGAIN;
  assert(read_usb_auth((void*)0) == 0 && !s->sys.shutdown);
  usb_receive_error = 0;
  s->auth_mode = 2;
  btds4_get_status(0, &flags, &age);
  assert((flags & (BTDS4_STATUS_SOFTWARE_AUTH | BTDS4_STATUS_SOFTWARE_READY)) ==
         (BTDS4_STATUS_SOFTWARE_AUTH | BTDS4_STATUS_SOFTWARE_READY));
  assert(!(flags & (BTDS4_STATUS_USB_AUTH | BTDS4_STATUS_USB_CONNECTED | BTDS4_STATUS_DS4_CONTROL | BTDS4_STATUS_DS4_INTERRUPT)));
  assert(btds4_send_interrupt(0, &report, 1) > 0);
  s->auth_mode = 1;
  usb_present = 0;
  assert(btds4_send_interrupt(0, &report, 1) == -1);
  btds4_get_status(0, &flags, &age);
  assert((flags & BTDS4_STATUS_SHUTDOWN) && !(flags & BTDS4_STATUS_USB_CONNECTED));
  s->auth_mode = s->usb_unsupported = 0;
  s->usb_auth = NULL; // Restore the original shutdown snapshot for UDP wire tests.
  test_now += (gtime)UINT32_MAX * 1000000ULL;
  puts("PASS: uninitialized/disconnected/pending/connected/shutdown states; successful writes only; monotonic age saturation");
}
