// SPDX-License-Identifier: GPL-3.0-only
// USB EMUPS4PAIRING companion. See ../NOTICE for protocol/data provenance.
#define _GNU_SOURCE
#include <endian.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/usb/ch9.h>
#include <linux/usb/raw_gadget.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>
#include "descriptors.h"

#define MAX_DATA 1024
struct state { unsigned char magic[8], local[6], peer[6], key[16]; };
_Static_assert(sizeof(struct state) == 36, "state layout");
static struct state state = { .magic = {'G','I','M','X','P','A','I','R'}, .local = {1,2,3,4,5,6} };
static int fd, state_dir;
static struct { struct usb_raw_ep_io io; unsigned char data[MAX_DATA]; } ep0;
static struct usb_endpoint_descriptor in_desc = {
    .bLength = USB_DT_ENDPOINT_SIZE, .bDescriptorType = USB_DT_ENDPOINT,
    .bEndpointAddress = 0x84, .bmAttributes = USB_ENDPOINT_XFER_INT,
    .wMaxPacketSize = 64, .bInterval = 5
};
static struct usb_endpoint_descriptor out_desc = {
    .bLength = USB_DT_ENDPOINT_SIZE, .bDescriptorType = USB_DT_ENDPOINT,
    .bEndpointAddress = 0x03, .bmAttributes = USB_ENDPOINT_XFER_INT,
    .wMaxPacketSize = 64, .bInterval = 5
};
static const unsigned char device_desc[] = {
    18,1,0,2,0,0,0,64,0x4c,0x05,0xc4,0x05,0,1,1,2,0,1
};
static unsigned char config_desc[] = {
    9,2,41,0,1,1,0,0xc0,250,
    9,4,0,0,2,3,0,0,0,
    9,0x21,0x11,1,0,1,0x22,sizeof(hid_report)&255,sizeof(hid_report)>>8,
    7,5,0x84,3,64,0,5,
    7,5,0x03,3,64,0,5
};
struct worker { int ep; bool input; pthread_t thread; bool started; atomic_bool stopping; };
static struct worker workers[2];
static unsigned char configuration, idle_rate;

static void die(const char *what) { perror(what); exit(1); }
static bool nonzero(const unsigned char *p, size_t n) {
    for (size_t i=0;i<n;i++) if(p[i]) return true;
    return false;
}
static void persist(const struct state *next) {
    int f = openat(state_dir, "state.tmp", O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW, 0600);
    if (f < 0) die("open state.tmp");
    if (write(f, next, sizeof(*next)) != sizeof(*next)) die("write state");
    if (fsync(f)) die("fsync state");
    if (close(f)) die("close state");
    if (renameat(state_dir,"state.tmp",state_dir,"state.bin")) die("rename state");
    if (fsync(state_dir)) die("fsync state directory");
    state = *next;
    fprintf(stderr,"State saved durably; PS4 key %s (key not logged).\n",
            nonzero(state.key,16) ? "present" : "cleared");
}
static void load_state(const char *dir) {
    if (mkdir(dir,0700) && errno != EEXIST) die("mkdir state directory");
    state_dir = open(dir,O_RDONLY|O_DIRECTORY|O_NOFOLLOW);
    if(state_dir < 0) die("open state directory");
    int lock = openat(state_dir,"lock",O_RDWR|O_CREAT|O_NOFOLLOW,0600);
    if(lock < 0 || flock(lock,LOCK_EX|LOCK_NB)) die("state already in use");
    int f = openat(state_dir,"state.bin",O_RDONLY|O_NOFOLLOW);
    if(f < 0) {
        if(errno != ENOENT) die("open state");
        persist(&state);
        return;
    }
    struct state data;
    unsigned char extra;
    if(read(f,&data,sizeof(data)) != sizeof(data) || read(f,&extra,1) != 0 ||
       memcmp(data.magic,"GIMXPAIR",8)) {
        fprintf(stderr,"Invalid state file; refusing to overwrite it.\n"); exit(1);
    }
    close(f); state=data;
}
static int transfer(bool in, const void *data, size_t len, size_t requested) {
    if(len > requested) len=requested;
    if(len > MAX_DATA) { errno=EOVERFLOW; return -1; }
    memset(&ep0,0,sizeof(ep0));
    ep0.io.length=len;
    if(in && data) memcpy(ep0.data,data,len);
    // End short IN transfers on a packet boundary with a zero-length packet.
    if(in && len < requested && len % 64 == 0) ep0.io.flags=USB_RAW_IO_FLAGS_ZERO;
    return ioctl(fd,in ? USB_RAW_IOCTL_EP0_WRITE : USB_RAW_IOCTL_EP0_READ,&ep0);
}
static void stall(void) {
    if(ioctl(fd,USB_RAW_IOCTL_EP0_STALL,0) < 0 && errno != ENODEV) perror("EP0 stall");
}
static void interrupt_worker(int signal_number) { (void)signal_number; }
static void *endpoint_worker(void *arg) {
    struct worker *w=arg;
    struct { struct usb_raw_ep_io io; unsigned char data[64]; } buf;
    while(!atomic_load(&w->stopping)) {
        memset(&buf,0,sizeof(buf)); buf.io.ep=w->ep;
        buf.io.length=w->input ? sizeof(input_report) : 64;
        if(w->input) memcpy(buf.data,input_report,sizeof(input_report));
        if(ioctl(fd,w->input ? USB_RAW_IOCTL_EP_WRITE : USB_RAW_IOCTL_EP_READ,&buf) < 0) {
            if(errno == EINTR) continue;
            break;
        }
        if(w->input) usleep(5000);
    }
    return NULL;
}
static void stop_endpoints(void) {
    // Raw Gadget rejects EP_DISABLE while an endpoint ioctl is outstanding.
    // Interrupt and join workers first; the kernel then dequeues their requests.
    for(int i=0;i<2;i++) if(workers[i].started)
        atomic_store(&workers[i].stopping,true);
    for(int i=0;i<2;i++) if(workers[i].started) {
        int err=EBUSY;
        for(int attempt=0;attempt<200;attempt++) {
            err=pthread_tryjoin_np(workers[i].thread,NULL);
            if(err!=EBUSY) break;
            pthread_kill(workers[i].thread,SIGUSR1);
            usleep(10000);
        }
        if(err) { errno=err; die("join endpoint worker"); }
        if(ioctl(fd,USB_RAW_IOCTL_EP_DISABLE,(unsigned long)workers[i].ep)<0)
            die("disable endpoint");
        workers[i].started=false;
    }
    configuration=0;
}
static int start_endpoints(void) {
    stop_endpoints();
    struct usb_endpoint_descriptor *descs[]={&in_desc,&out_desc};
    for(int i=0;i<2;i++) {
        workers[i].ep=ioctl(fd,USB_RAW_IOCTL_EP_ENABLE,descs[i]);
        if(workers[i].ep < 0) { perror("enable endpoint"); stop_endpoints(); return -1; }
        workers[i].input=(i==0);
        atomic_store(&workers[i].stopping,false);
        int err=pthread_create(&workers[i].thread,NULL,endpoint_worker,&workers[i]);
        if(err) { errno=err; die("pthread_create"); }
        workers[i].started=true;
    }
    if(ioctl(fd,USB_RAW_IOCTL_CONFIGURE,0) < 0) die("configure gadget");
    ioctl(fd,USB_RAW_IOCTL_VBUS_DRAW,250UL);
    configuration=1; return 0;
}
static void choose_endpoints(void) {
    struct usb_raw_eps_info info={0};
    int n=ioctl(fd,USB_RAW_IOCTL_EPS_INFO,&info);
    if(n < 0) die("endpoint capabilities");
    bool have_in=false,have_out=false;
    for(int i=0;i<n;i++) {
        const struct usb_raw_ep_info *e=&info.eps[i];
        if(!e->caps.type_int || e->limits.maxpacket_limit < 64) continue;
        if(!have_in && e->caps.dir_in) {
            in_desc.bEndpointAddress=e->addr == USB_RAW_EP_ADDR_ANY ? 0x84 : (e->addr|0x80);
            have_in=true; continue;
        }
        if(!have_out && e->caps.dir_out) {
            out_desc.bEndpointAddress=e->addr == USB_RAW_EP_ADDR_ANY ? 0x03 : (e->addr&0x7f);
            have_out=true;
        }
    }
    if(!have_in || !have_out) { fprintf(stderr,"UDC lacks interrupt IN/OUT endpoints\n"); exit(1); }
    config_desc[29]=in_desc.bEndpointAddress;
    config_desc[36]=out_desc.bEndpointAddress;
}
static void control(const struct usb_ctrlrequest *r) {
    unsigned int value=le16toh(r->wValue), index=le16toh(r->wIndex), len=le16toh(r->wLength);
    if(getenv("PAIRING_TRACE")) fprintf(stderr,"SETUP %02x %02x %04x %04x %u\n",r->bRequestType,r->bRequest,value,index,len);
    unsigned char data[MAX_DATA]={0};
    const void *reply=data;
    size_t size=0;
    bool in=(r->bRequestType & USB_DIR_IN)!=0;
    if(!in && len > MAX_DATA) { stall(); return; }
    if((r->bRequestType & USB_TYPE_MASK)==USB_TYPE_STANDARD) {
        switch(r->bRequest) {
        case USB_REQ_GET_DESCRIPTOR:
            if(!in) { stall(); return; }
            switch(value>>8) {
            case USB_DT_DEVICE:
                if(r->bRequestType!=0x80 || (value&255)) { stall();return; }
                reply=device_desc;size=sizeof(device_desc);break;
            case USB_DT_CONFIG:
                if(r->bRequestType!=0x80 || (value&255)) { stall();return; }
                reply=config_desc;size=sizeof(config_desc);break;
            case USB_DT_STRING: {
                if(r->bRequestType!=0x80) { stall();return; }
                if((value&255)==0) { data[0]=4;data[1]=3;data[2]=9;data[3]=4;size=4;break; }
                const char *s=(value&255)==1 ? "Sony Computer Entertainment" :
                              (value&255)==2 ? "Wireless Controller" : NULL;
                if(!s) { stall();return; }
                size=2+2*strlen(s);data[0]=size;data[1]=3;
                for(size_t i=0;i<strlen(s);i++) data[2+2*i]=s[i];
                break;
            }
            case 0x21:
                if(r->bRequestType!=0x81 || index) { stall();return; }
                reply=config_desc+18;size=9;break;
            case 0x22:
                if(r->bRequestType!=0x81 || index) { stall();return; }
                reply=hid_report;size=sizeof(hid_report);break;
            default: stall();return;
            }
            break;
        case USB_REQ_SET_CONFIGURATION:
            if(r->bRequestType!=0 || index || len || value>1) { stall();return; }
            if(value==1) { if(start_endpoints()) { stall();return; } }
            else stop_endpoints();
            break;
        case USB_REQ_GET_CONFIGURATION:
            if(r->bRequestType!=0x80 || index || value || len!=1) { stall();return; }
            data[0]=configuration;size=1;break;
        case USB_REQ_GET_STATUS:
            if(!in || value || len!=2) { stall();return; }
            if(r->bRequestType==0x80 && !index) data[0]=1; // Self powered, as upstream.
            else if(!(r->bRequestType==0x81 && !index)) { stall();return; }
            size=2;break;
        case USB_REQ_GET_INTERFACE:
            if(r->bRequestType!=0x81 || index || value || len!=1) { stall();return; }
            size=1;break;
        case USB_REQ_SET_INTERFACE:
            if(r->bRequestType!=1 || index || value || len) { stall();return; }
            break;
        default: stall();return;
        }
    } else if((r->bRequestType & USB_TYPE_MASK)==USB_TYPE_CLASS && index==0) {
        if(r->bRequestType==0xa1 && r->bRequest==1) { // HID GET_REPORT
            switch(value) {
            case 0x03a3: reply=feature_a3;size=sizeof(feature_a3);break;
            case 0x0302: reply=feature_02;size=sizeof(feature_02);break;
            case 0x0312:
                data[0]=0x12;memcpy(data+1,state.local,6);data[7]=8;data[8]=0x25;
                memcpy(data+10,state.peer,6);size=16;break;
            case 0x0313: reply=state.key;size=16;break; // Upstream extension, no report ID.
            default: stall();return;
            }
        } else if(r->bRequestType==0x21 && r->bRequest==9) { // HID SET_REPORT
            if(!((value==0x0312 && len==6) || (value==0x0313 && len==23) ||
                 (value==0x0314 && len>0 && len<=64))) { stall();return; }
            int got=transfer(false,NULL,len,len);
            if(got < 0) { perror("SET_REPORT read");return; }
            if((unsigned)got!=len) { fprintf(stderr,"Ignored short SET_REPORT\n");return; }
            struct state next=state;
            if(value==0x0312) memcpy(next.local,ep0.data,6);
            else if(value==0x0313) {
                if(ep0.data[0]!=0x13) { fprintf(stderr,"Ignored invalid report ID\n");return; }
                memcpy(next.peer,ep0.data+1,6);memcpy(next.key,ep0.data+7,16);
            } else return; // Upstream acknowledges 0x0314 without changing state.
            persist(&next);return;
        } else if(r->bRequestType==0x21 && r->bRequest==10 && !len) idle_rate=value>>8;
        else if(r->bRequestType==0xa1 && r->bRequest==2 && len==1) { data[0]=idle_rate;size=1; }
        else { stall();return; }
    } else { stall();return; }
    if(transfer(in,reply,size,len)<0) perror("EP0 transfer");
}
int main(int argc,char **argv) {
    if(argc!=4) { fprintf(stderr,"Usage: %s UDC_DRIVER UDC_NAME STATE_DIRECTORY\n",argv[0]);return 2; }
    struct sigaction action={.sa_handler=interrupt_worker};
    sigemptyset(&action.sa_mask); // No SA_RESTART: interrupt blocking endpoint ioctls.
    if(sigaction(SIGUSR1,&action,NULL)) die("sigaction");
    setvbuf(stderr,NULL,_IONBF,0);umask(0077);
    if(strlen(argv[1])>=UDC_NAME_LENGTH_MAX || strlen(argv[2])>=UDC_NAME_LENGTH_MAX) return 2;
    load_state(argv[3]);
    fd=open("/dev/raw-gadget",O_RDWR);
    if(fd<0) die("open /dev/raw-gadget");
    struct usb_raw_init init={.speed=USB_SPEED_FULL};
    strcpy((char*)init.driver_name,argv[1]);strcpy((char*)init.device_name,argv[2]);
    if(ioctl(fd,USB_RAW_IOCTL_INIT,&init)) die("raw gadget init");
    if(ioctl(fd,USB_RAW_IOCTL_RUN,0)) die("raw gadget run");
    fprintf(stderr,"Pairing gadget running on %s; pairing secrets are not logged.\n",argv[2]);
    for(;;) {
        struct { struct usb_raw_event event; unsigned char data[256]; } ev={.event.length=256};
        if(ioctl(fd,USB_RAW_IOCTL_EVENT_FETCH,&ev)<0) {
            if(errno==EINTR) continue;
            die("fetch event");
        }
        if(ev.event.type==USB_RAW_EVENT_CONNECT) choose_endpoints();
        else if(ev.event.type==USB_RAW_EVENT_CONTROL && ev.event.length==sizeof(struct usb_ctrlrequest)) {
            struct usb_ctrlrequest req;memcpy(&req,ev.data,sizeof(req));control(&req);
        } else if(ev.event.type==5 || ev.event.type==6) stop_endpoints(); // Reset / disconnect.
    }
}
