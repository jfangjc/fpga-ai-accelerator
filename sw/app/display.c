#define _GNU_SOURCE
#include "display.h"
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
/* Embedded 5x7 font, column-major, bit 0 at top: space, a-z (drawn as
 * capitals), apostrophe, then 0-9 . : % - / for the status strip. */
static const uint8_t font[43][5]={
 {0,0,0,0,0},{126,17,17,17,126},{127,73,73,73,54},{62,65,65,65,34},
 {127,65,65,34,28},{127,73,73,73,65},{127,9,9,9,1},{62,65,73,73,122},
 {127,8,8,8,127},{0,65,127,65,0},{32,64,65,63,1},{127,8,20,34,65},
 {127,64,64,64,64},{127,2,12,2,127},{127,4,8,16,127},{62,65,65,65,62},
 {127,9,9,9,6},{62,65,81,33,94},{127,9,25,41,70},{70,73,73,73,49},
 {1,1,127,1,1},{63,64,64,64,63},{31,32,64,32,31},{63,64,56,64,63},
 {99,20,8,20,99},{7,8,112,8,7},{97,81,73,69,67},{0,5,3,0,0},
 {62,81,73,69,62},{0,66,127,64,0},{66,97,81,73,70},{33,65,69,75,49},
 {24,20,18,127,16},{39,69,69,69,57},{60,74,73,73,48},{1,113,9,5,3},
 {54,73,73,73,54},{6,73,73,41,30},{0,96,96,0,0},{0,54,54,0,0},
 {35,19,8,100,98},{8,8,8,8,8},{32,16,8,4,2}};
static unsigned glyph(char ch) {
    if(ch>='a' && ch<='z') return (unsigned)(ch-'a'+1);
    if(ch>='A' && ch<='Z') return (unsigned)(ch-'A'+1);
    if(ch>='0' && ch<='9') return (unsigned)(ch-'0'+28);
    switch(ch) {
    case '\'': return 27;
    case '.': return 38;
    case ':': return 39;
    case '%': return 40;
    case '-': return 41;
    case '/': return 42;
    default: return 0;
    }
}
static const uint8_t committed_rgb[3]={235,255,235}, tentative_rgb[3]={255,190,40},
    status_rgb[3]={120,200,255};
static uint8_t pixels[640*240*3], status[640*CAP_STATUS_ROWS*3];
/* Draw text 2x scaled into a 640-wide RGB image of the given height. */
static void draw(uint8_t *image,unsigned height,const char *s,unsigned *x,unsigned *y,const uint8_t *rgb) {
    for(;*s;s++) {
        unsigned c=glyph(*s);
        if(*x+12>=640) { *x=8; *y+=20; }
        if(*y+14>=height) break;
        for(unsigned i=0;i<5;i++) for(unsigned j=0;j<7;j++) if(font[c][i]&(1u<<j))
            for(unsigned dx=0;dx<2;dx++) for(unsigned dy=0;dy<2;dy++)
                memcpy(image+(((*y+j*2+dy)*640)+*x+i*2+dx)*3,rgb,3);
        *x+=12;
    }
}
static void text(const char *s,unsigned *x,unsigned *y,int tentative) {
    draw(pixels,240,s,x,y,tentative?tentative_rgb:committed_rgb);
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
/* One line on a slightly lighter strip, under a thin separator. */
void cap_render_status(const char *s) {
    memset(status,24,sizeof(status)); memset(status,60,640*2*3);
    unsigned x=8,y=14;
    draw(status,CAP_STATUS_ROWS,s,&x,&y,status_rgb);
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
static unsigned scale=1, ox, oy, rows=240;
static struct fb_var_screeninfo var;
static struct fb_fix_screeninfo fix;
static uint8_t shown[640*(240+CAP_STATUS_ROWS)*3];
static uint8_t row[640*CAP_MAX_SCALE*4];
/* Truecolour 16 or 32 bpp: pack each channel by its bitfield. */
static uint32_t pack(const uint8_t *p) {
    return (uint32_t)(p[0]>>(8-var.red.length))<<var.red.offset |
        (uint32_t)(p[1]>>(8-var.green.length))<<var.green.offset |
        (uint32_t)(p[2]>>(8-var.blue.length))<<var.blue.offset;
}
static int put(unsigned x,unsigned y,unsigned pixels_in_row) {
    unsigned bytes=var.bits_per_pixel/8;
    off_t offset=(off_t)(y+var.yoffset)*fix.line_length+(off_t)(x+var.xoffset)*bytes;
    return pwrite(fb,row,pixels_in_row*bytes,offset)==(ssize_t)(pixels_in_row*bytes)?0:-1;
}
/* Fill the whole visible screen with the caption background colour. */
static int clear_screen(void) {
    static const uint8_t background[3]={12,12,12};
    unsigned bytes=var.bits_per_pixel/8, chunk=sizeof(row)/bytes;
    uint32_t value=pack(background);
    for(unsigned i=0;i<chunk;i++) memcpy(row+i*bytes,&value,bytes);
    for(unsigned y=0;y<var.yres;y++)
        for(unsigned x=0;x<var.xres;x+=chunk)
            if(put(x,y,var.xres-x<chunk?var.xres-x:chunk)) return -1;
    return 0;
}
int cap_fb_attach(int fd,const struct fb_var_screeninfo *v,const struct fb_fix_screeninfo *f,int want,int with_status) {
    if(!(v->bits_per_pixel==32 || v->bits_per_pixel==16) || v->xres<640 || v->yres<240 ||
       !v->red.length || v->red.length>8 || !v->green.length || v->green.length>8 ||
       !v->blue.length || v->blue.length>8) return -1;
    var=*v; fix=*f; fb=fd; shown_valid=0; scale=1; ox=oy=0; rows=240;
    if(want<0) return 0;
    /* Demo layout: centred, whole-number scale, status strip if it fits. */
    if(with_status && v->yres>=240+CAP_STATUS_ROWS) { rows=240+CAP_STATUS_ROWS; cap_render_status(""); }
    unsigned fit=v->xres/640<v->yres/rows?v->xres/640:v->yres/rows;
    if(fit>CAP_MAX_SCALE) fit=CAP_MAX_SCALE;
    scale=want>0 && (unsigned)want<fit?(unsigned)want:fit;
    ox=(v->xres-640*scale)/2; oy=(v->yres-rows*scale)/2;
    if(clear_screen()) { fb=-1; return -1; }
    return 0;
}
int cap_fb_open(int want,int with_status) {
    int fd=open("/dev/fb0",O_RDWR|O_CLOEXEC);
    if(fd<0) return -1;
    struct fb_var_screeninfo v; struct fb_fix_screeninfo f;
    if(ioctl(fd,FBIOGET_VSCREENINFO,&v) || ioctl(fd,FBIOGET_FSCREENINFO,&f) ||
       cap_fb_attach(fd,&v,&f,want,with_status)) { close(fd); return -1; }
    return 0;
}
int cap_fb(int full) {
    if(fb<0) return -1;
    unsigned bytes=var.bits_per_pixel/8, written=0;
    for(unsigned y0=0;y0<rows;y0++) {
        const uint8_t *src=y0<240?pixels+y0*640*3:status+(y0-240)*640*3;
        /* Incremental: only pixel rows that differ from the last write. */
        if(!full && shown_valid && !memcmp(src,shown+y0*640*3,640*3)) continue;
        for(unsigned x0=0;x0<640;x0++) {
            uint32_t value=pack(src+x0*3);
            for(unsigned k=0;k<scale;k++) memcpy(row+(x0*scale+k)*bytes,&value,bytes); /* little-endian */
        }
        for(unsigned k=0;k<scale;k++)
            if(put(ox,oy+y0*scale+k,640*scale)) { shown_valid=0; return -1; }
        memcpy(shown+y0*640*3,src,640*3); written++;
    }
    shown_valid=1; return (int)written;
}
void cap_fb_close(void) { if(fb>=0) close(fb); fb=-1; }
