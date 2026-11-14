/* Exercise the actual network dispatcher, with a captured send function. */
#include <assert.h>
#include <arpa/inet.h>
#include "/opt/gimx-src/core/controller.c"
__typeof__(gimx_params) gimx_params;
static unsigned char captured[1024];
static unsigned int captured_len;
int gudp_send(struct gudp_socket *socket, const void *buf, unsigned int count, struct gudp_address address) {
  (void)socket; (void)address; assert(count <= sizeof(captured));
  memcpy(captured,buf,count); captured_len=count; return count;
}
uint32_t gudp_htonl(uint32_t value) { return htonl(value); }
uint32_t gudp_ntohl(uint32_t value) { return ntohl(value); }
void test_btds4_state(void);
static int query(const unsigned char *data, int len) {
  captured_len = 0;
  return network_read_callback((void*)0,data,len,(struct gudp_address){0});
}
int main(void) {
  test_btds4_state();
  adapters[0].ctype=C_TYPE_DS4;
  adapters[0].atype=E_ADAPTER_TYPE_BLUETOOTH;
  gimx_params.clock_source=CLOCK_INPUT;
  unsigned char request[]={2,1,0x12,0x34,0x56,0x78};
  assert(query(request,6)==0); // Status must not clock an input-driven sender.
  assert(captured_len==16 && captured[0]==2 && captured[1]==1 && captured[2]==0);
  assert(captured[3]==C_TYPE_DS4 && !memcmp(captured+4,request+2,4));
  assert(captured[8]==0 && captured[9]==0 && captured[10]==1 && captured[11]==0x81);
  assert(captured[12]==255 && captured[13]==255 && captured[14]==255 && captured[15]==254);
  assert(adapters[0].send_command==0);
  for(int n=0;n<6;n++) { query(request,n); assert(captured_len==0); }
  unsigned char long_request[7]={2,1,1,2,3,4,5};
  query(long_request,7); assert(!captured_len);
  request[1]=2; query(request,6); assert(captured_len==16 && captured[2]==2);
  request[1]=1; adapters[0].atype=E_ADAPTER_TYPE_REMOTE_GIMX;
  query(request,6); assert(captured[2]==1 && !memcmp(captured+8,"\0\0\0\0",4));
  unsigned char legacy[]={0}; query(legacy,1);
  assert(captured_len==2 && captured[0]==0 && captured[1]==C_TYPE_DS4);
  unsigned char short_input[]={1}; query(short_input,1); assert(!captured_len);
  unsigned char input[]={1,1,137,0,0,0,255}; query(input,sizeof(input));
  assert(adapters[0].axis[abs_axis_9]==255 && adapters[0].send_command==1);
  unsigned char unknown[]={255,1}; query(unknown,2); assert(!captured_len);
  puts("PASS: exact wire layout, nonce, bad lengths/versions, unsupported transport, legacy query/input, status causes no input output");
  return 0;
}
