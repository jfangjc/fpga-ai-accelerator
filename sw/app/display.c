#define _GNU_SOURCE
#include "display.h"
#include <fcntl.h>
#include <linux/fb.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
/* Embedded 5x7 ASCII lowercase font, column-major, bit 0 at top. */
static const uint8_t font[28][5]={
 {0,0,0,0,0},{126,17,17,17,126},{127,73,73,73,54},{62,65,65,65,34},
 {127,65,65,34,28},{127,73,73,73,65},{127,9,9,9,1},{62,65,73,73,122},
 {127,8,8,8,127},{0,65,127,65,0},{32,64,65,63,1},{127,8,20,34,65},
 {127,64,64,64,64},{127,2,12,2,127},{127,4,8,16,127},{62,65,65,65,62},
 {127,9,9,9,6},{62,65,81,33,94},{127,9,25,41,70},{70,73,73,73,49},
 {1,1,127,1,1},{63,64,64,64,63},{31,32,64,32,31},{63,64,56,64,63},
 {99,20,8,20,99},{7,8,112,8,7},{97,81,73,69,67},{0,5,3,0,0}};
static uint8_t pixels[640*240*3];
static void text(const char *s,unsigned *x,unsigned *y,int tentative) {
    for(;*s;s++) {
        unsigned c=*s=='\''?27: (*s>='a' && *s<='z'?(unsigned)(*s-'a'+1):0);
        if(*x+12>=640) { *x=8; *y+=20; }
        if(*y+14>=240) break;
        for(unsigned i=0;i<5;i++) for(unsigned j=0;j<7;j++) if(font[c][i]&(1u<<j))
            for(unsigned dx=0;dx<2;dx++) for(unsigned dy=0;dy<2;dy++) {
                unsigned p=(((*y+j*2+dy)*640)+*x+i*2+dx)*3;
                pixels[p]=tentative?255:235; pixels[p+1]=tentative?190:255; pixels[p+2]=tentative?40:235;
            }
        *x+=12;
    }
}
void cap_render(const char *committed,const char *tentative) {
    memset(pixels,12,sizeof(pixels)); unsigned x=8,y=20;
    /* Keep the newest text visible as the accumulated transcript grows. */
    size_t committed_len=strlen(committed), tentative_len=strlen(tentative);
    size_t visible=tentative_len>committed_len?tentative_len:committed_len;
    size_t skip=visible>480?visible-480:0;
    committed+=skip<committed_len?skip:committed_len;
    tentative+=skip<tentative_len?skip:tentative_len;
    text(committed,&x,&y,0);
    size_t prefix=strlen(committed);
    if(!strncmp(committed,tentative,prefix)) text(tentative+prefix,&x,&y,1);
}
void cap_render_styled(const char *s,const unsigned char *styles) {
    memset(pixels,12,sizeof(pixels)); unsigned x=8,y=20;
    size_t n=strlen(s),skip=n>480?n-480:0;
    for(size_t i=skip;i<n;i++) {
        char one[2]={s[i],0}; text(one,&x,&y,styles[i]);
    }
}
/* Write path.tmp then rename: never truncates a file whose pages may still
 * be under writeback (ext4 auto_da_alloc starts it on close after O_TRUNC). */
int cap_ppm(const char *ppm,const uint8_t *image) {
    char tmp[4096];
    if(snprintf(tmp,sizeof(tmp),"%s.tmp",ppm)>=(int)sizeof(tmp)) return -1;
    FILE *f=fopen(tmp,"wb");
    if(!f) return -1;
    fprintf(f,"P6\n640 240\n255\n");
    int ok=fwrite(image,1,CAP_PIXEL_BYTES,f)==CAP_PIXEL_BYTES;
    if(fclose(f) || !ok) { unlink(tmp); return -1; }
    return rename(tmp,ppm);
}
const uint8_t *cap_pixels(void) { return pixels; }
static int fb=-1, shown_valid;
static struct fb_var_screeninfo var;
static struct fb_fix_screeninfo fix;
static uint8_t shown[640*240*3];
int cap_fb_open(void) {
    int fd=open("/dev/fb0",O_RDWR|O_CLOEXEC);
    if(fd<0) return -1;
    /* Truecolour 16 or 32 bpp: pack each channel by its bitfield. */
    if(ioctl(fd,FBIOGET_VSCREENINFO,&var) || ioctl(fd,FBIOGET_FSCREENINFO,&fix) ||
       !(var.bits_per_pixel==32 || var.bits_per_pixel==16) || var.xres<640 || var.yres<240 ||
       !var.red.length || var.red.length>8 || !var.green.length || var.green.length>8 ||
       !var.blue.length || var.blue.length>8) { close(fd); return -1; }
    fb=fd; shown_valid=0; return 0;
}
int cap_fb(int full) {
    if(fb<0) return -1;
    unsigned bytes=var.bits_per_pixel/8, written=0;
    uint8_t row[640*4];
    for(unsigned y0=0;y0<240;y0++) {
        const uint8_t *src=pixels+y0*640*3;
        /* Incremental: only pixel rows that differ from the last write. */
        if(!full && shown_valid && !memcmp(src,shown+y0*640*3,640*3)) continue;
        for(unsigned x0=0;x0<640;x0++) {
            const uint8_t *p=src+x0*3;
            uint32_t value=(uint32_t)(p[0]>>(8-var.red.length))<<var.red.offset |
                (uint32_t)(p[1]>>(8-var.green.length))<<var.green.offset |
                (uint32_t)(p[2]>>(8-var.blue.length))<<var.blue.offset;
            memcpy(row+x0*bytes,&value,bytes); /* little-endian framebuffer */
        }
        off_t offset=(off_t)(y0+var.yoffset)*fix.line_length+(off_t)var.xoffset*bytes;
        if(pwrite(fb,row,640*bytes,offset)!=(ssize_t)(640*bytes)) { shown_valid=0; return -1; }
        memcpy(shown+y0*640*3,src,640*3); written++;
    }
    shown_valid=1; return (int)written;
}
void cap_fb_close(void) { if(fb>=0) close(fb); fb=-1; }
