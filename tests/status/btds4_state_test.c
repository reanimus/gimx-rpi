/* Compile the actual patched implementation; fake only the clock and L2CAP transport. */
#include <assert.h>
#include <limits.h>
#include "/opt/gimx-src/core/connectors/btds4.c"
static gtime test_now = 10000000000ULL;
static int send_result;
static int send_calls;
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
  send_calls++;
  (void)channel; (void)buf; (void)blocking;
  if (send_result == -1) { errno = EAGAIN; return -1; }
  return send_result == -2 ? len - 1 : len;
}
static int fake_close(int channel) { (void)channel; return 0; }
s_l2cap_abs *l2cap_abs_get(void) {
  static s_l2cap_abs impl = { .send = fake_send, .close = fake_close };
  return &impl;
}
static void test_auth_sent_events(void) {
  struct btds4_state *s = &states[3];
  s->usb_auth = (struct ds4_usb_auth *)1; s->ps4_channels.control.id = 3;
  FILE *capture = tmpfile(); assert(capture);
  int saved = dup(STDERR_FILENO); assert(saved >= 0);
  assert(dup2(fileno(capture), STDERR_FILENO) >= 0);
  usb_reply = (struct ds4_usb_reply){.length=48, .data={0xa3, 4}};
  send_result = -1; assert(read_usb_auth((void*)3) == 1);
  s->sys.shutdown = 0; send_result = -2;
  assert(read_usb_auth((void*)3) == 1);
  s->sys.shutdown = 0; send_result = 0; s->auth_fault = 2;
  assert(read_usb_auth((void*)3) == 0);
  s->auth_fault = 0; assert(read_usb_auth((void*)3) == 0);
  usb_reply = (struct ds4_usb_reply){.length=65, .data={0xa3, 0xf1}};
  assert(read_usb_auth((void*)3) == 0);
  usb_reply = (struct ds4_usb_reply){.length=1, .data={0}};
  assert(read_usb_auth((void*)3) == 0); /* Local handshake isn't auth sent. */
  fflush(stderr); assert(dup2(saved, STDERR_FILENO) >= 0); close(saved);
  rewind(capture); char line[512]; int count = 0;
  while (fgets(line, sizeof(line), capture)) {
    assert(!strstr(line, "acceptance="));
    if (strstr(line, "auth sent")) {
      assert(strstr(line, count == 0 ? "reason=auth2_reply" : "reason=rsa_response_page"));
      count++;
    }
  }
  assert(count == 2); fclose(capture); memset(s, 0, sizeof(*s));
  puts("PASS: auth sent only after full response writes, never on failed/short/dropped replies or handshakes");
}
static void test_auth_faults(void) {
  struct btds4_state *s = &states[2];
  unsigned char packet[48] = {0xa3, 0x04}, original[48];
  memcpy(original, packet, sizeof(packet));
  assert(auth_fault_reply(s, packet, sizeof(packet)) == 0);
  assert(!memcmp(packet, original, sizeof(packet)));
  s->auth_fault = 1;
  assert(auth_fault_reply(s, packet, 47) == 0 && s->auth_fault_count == 0);
  packet[1] = 0xf1;
  assert(auth_fault_reply(s, packet, 48) == 0 && s->auth_fault_count == 0);
  packet[1] = 4;
  assert(auth_fault_reply(s, packet, 48) == 0 && s->auth_fault_count == 1);
  assert(packet[4] == 1 && !memcmp(packet, original, 4));
  assert(!memcmp(packet + 5, original + 5, 39));
  uint32_t crc = (uint32_t)packet[44] | (uint32_t)packet[45] << 8 |
      (uint32_t)packet[46] << 16 | (uint32_t)packet[47] << 24;
  assert(crc == 0x1e47068aU); /* Independent Python zlib reference. */
  s->auth_fault = 2;
  memcpy(packet, original, sizeof(packet));
  assert(auth_fault_reply(s, packet, 48) == 1 && s->auth_fault_count == 2);
  assert(!memcmp(packet, original, sizeof(packet)));
  /* Exercise the real local-auth send path: ignored replies must not send. */
  struct btds4_state *local = &states[0];
  local->usb_auth = (struct ds4_usb_auth *)1;
  local->ps4_channels.control.id = 3;
  local->auth_fault = 2;
  usb_reply = (struct ds4_usb_reply){.length=48, .data={0xa3, 4}};
  send_calls = 0;
  assert(read_usb_auth((void*)0) == 0 && send_calls == 0);
  local->auth_fault = 1;
  assert(read_usb_auth((void*)0) == 0 && send_calls == 1);
  memset(local, 0, sizeof(*local));
  puts("PASS: faults default off, AUTH2-only, valid corrupted CRC, drop without send");
}
static void test_disconnect_logs(void) {
  struct btds4_state *s = &states[1];
  FILE *capture = tmpfile(); assert(capture);
  int saved = dup(STDERR_FILENO); assert(saved >= 0);
  assert(dup2(fileno(capture), STDERR_FILENO) >= 0);
  s->ds4_channels.control.id = 5;
  errno = EBUSY;
  close_ds4_control((void*)1);
  assert(errno == EBUSY);
  close_ds4_control((void*)1); /* Duplicate callback stays silent. */
  s->ps4_channels.interrupt.id = 6;
  s->ps4_channels.interrupt.pending = 1;
  close_ps4_interrupt((void*)1);
  s->closing = 1;
  s->ps4_channels.control.id = 7;
  close_ps4_control((void*)1); /* Intentional cleanup stays silent. */
  s->closing = 0; s->sys.shutdown = 0; s->auth_mode = 2;
  auth_failure((void*)1, "worker_receive_failed", EPIPE);
  usb_auth_failed((void*)1); /* Backend shutdown is reported once. */
  s->sys.shutdown = 0; s->auth_mode = 1;
  usb_auth_failed((void*)1);
  s->sys.shutdown = 0; s->status_initialized = 1;
  s->ps4_channels.control.id = 7; s->ps4_channels.interrupt.id = 8;
  s->ps4_channels.control.pending = s->ps4_channels.interrupt.pending = 1;
  connected_event(s, 1); /* Both pending: silent. */
  s->ps4_channels.interrupt.pending = 0;
  connected_event(s, 1); /* Partial connection: silent. */
  s->ps4_channels.control.pending = 0;
  connected_event(s, 1); connected_event(s, 1); /* Emit exactly once. */
  s->ds4_channels.control.id = 9; s->ds4_channels.interrupt.id = -1;
  connected_event(s, 0);
  s->ds4_channels.interrupt.id = 10;
  connected_event(s, 0); connected_event(s, 0);
  s->closing = 1; close_ps4_control((void*)1);
  s->ps4_channels.control.id = 7;
  connected_event(s, 1); /* Cleanup: silent even with both channels. */
  s->closing = 0; s->sys.shutdown = 0;
  connected_event(s, 1); /* A new connection may emit again. */
  fflush(stderr);
  assert(dup2(saved, STDERR_FILENO) >= 0); close(saved);
  rewind(capture);
  char line[512]; int count = 0;
  while (fgets(line, sizeof(line), capture)) {
    assert(line[0] == '[' && line[5] == '-' && strstr(line, "Z] GIMX connection:"));
    assert(strstr(line, "controller=1"));
    if (count == 0) assert(strstr(line, "DS4 disconnected") && strstr(line, "errno=0"));
    if (count == 1) assert(strstr(line, "PS4 connection failed") && strstr(line, "connection_not_established"));
    if (count == 2) assert(strstr(line, "Software authentication backend failed") && strstr(line, "worker_receive_failed"));
    if (count == 3) assert(strstr(line, "USB authentication backend failed") && strstr(line, "worker_channel_closed"));
    if (count == 4 || count == 6) assert(strstr(line, "PS4 connected"));
    if (count == 5) assert(strstr(line, "DS4 connected"));
    count++;
  }
  assert(count == 7); fclose(capture);
  puts("PASS: timestamped disconnect/failure logs, pending distinction, cleanup suppression and duplicate suppression");
}
void test_btds4_state(void) {
  test_auth_sent_events();
  test_auth_faults();
  test_disconnect_logs();
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
