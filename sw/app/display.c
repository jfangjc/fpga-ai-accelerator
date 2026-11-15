#define _GNU_SOURCE
#include "display.h"
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
/* Embedded 5x7 font, column-major, bit 0 at top: space, a-z (drawn as
 * capitals), apostrophe, then 0-9 . : % - / for the status strip and a dot. */
static const uint8_t font[44][5]={
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
 {35,19,8,100,98},{8,8,8,8,8},{32,16,8,4,2},{28,62,62,62,28}};
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
    case '*': return 43;
    default: return 0;
    }
}
/* Flat colours only: the framebuffer is RGB565, so gradients would band.
 * The same two themes as main's sw/display.c. */
static const struct theme {
    uint8_t background[3], text[3], tentative[3], history[3], strip[3], label[3], dim[3];
} themes[2]={
    {{18,20,28},{242,240,232},{242,179,61},{124,128,145},{28,31,42},{142,197,255},{58,63,82}},
    {{255,244,194},{26,26,26},{154,74,14},{122,106,58},{26,26,26},{255,220,0},{74,70,54}}};
static const struct theme *theme=&themes[0];
void cap_theme(int butter) { theme=&themes[butter?1:0]; }
#define LEFT 32u    /* left edge of all text */
#define HISTORY 48u /* characters per history line: (640-2*LEFT)/12 */
#define LIVE 32u    /* characters per live line: (640-2*LEFT)/18 */
#define METER 7u    /* bars in the level meter */
static uint8_t pixels[640*240*3], status[640*CAP_STATUS_ROWS*3];
static int speaking; static unsigned level;
static void fill(uint8_t *image,unsigned x,unsigned y,unsigned w,unsigned h,const uint8_t *rgb) {
    for(unsigned j=y;j<y+h;j++) for(unsigned i=x;i<x+w && i<640;i++) memcpy(image+(j*640+i)*3,rgb,3);
}
/* One character at `size` times the font: a (6*size)-pixel advance. */
static void character(uint8_t *image,char ch,unsigned x,unsigned y,unsigned size,const uint8_t *rgb) {
    const uint8_t *bits=font[glyph(ch)];
    for(unsigned i=0;i<5;i++) for(unsigned j=0;j<7;j++)
        if(bits[i]&(1u<<j)) fill(image,x+i*size,y+j*size,size,size,rgb);
}
/* Word-wrap s[0..n) at cols characters: the last `max` lines' [start,end)
 * offsets, oldest first; a word longer than a line is split. */
static unsigned wrap(const char *s,size_t n,unsigned cols,size_t *start,size_t *end,unsigned max) {
    size_t at=0, ls[64], le[64]; unsigned lines=0;
    for(;;) {
        while(at<n && s[at]==' ') at++;
        if(at>=n) break;
        size_t stop=n-at<=cols?n:at+cols, next=stop;
        if(stop<n) {
            size_t k=stop; while(k>at && s[k]!=' ') k--;
            if(k>at) stop=next=k;
        }
        ls[lines%64]=at; le[lines%64]=stop; lines++; at=next;
    }
    unsigned kept=lines<max?lines:max;
    for(unsigned i=0;i<kept;i++) { start[i]=ls[(lines-kept+i)%64]; end[i]=le[(lines-kept+i)%64]; }
    return kept;
}
/* History: two lines at twice the font, tops at rows 60 and 80. Live: two
 * lines at three times, at 116 and 148. Both keep their newest lines, and the
 * live phrase always starts on the same line, so the eye stays put. */
void cap_render_styled(const char *s,const unsigned char *styles,size_t live) {
    size_t start[2],end[2],n=strlen(s);
    if(live>n) live=n;
    fill(pixels,0,0,640,240,theme->background);
    unsigned lines=wrap(s,live,HISTORY,start,end,2);
    for(unsigned i=0;i<lines;i++) for(size_t j=start[i];j<end[i];j++)
        character(pixels,s[j],LEFT+12*(unsigned)(j-start[i]),60+20*(i+2-lines),2,theme->history);
    lines=wrap(s+live,n-live,LIVE,start,end,2);
    for(unsigned i=0;i<lines;i++) for(size_t j=start[i];j<end[i];j++)
        character(pixels,s[live+j],LEFT+18*(unsigned)(j-start[i]),116+32*i,3,
                  styles[live+j]?theme->tentative:theme->text);
}
/* Seven bars rising left to right, lit up to the level. */
static void meter(void) {
    for(unsigned i=0;i<METER;i++) {
        unsigned height=4+2*i, x=LEFT+20+7*i;
        fill(status,x,11,4,16,theme->strip);
        fill(status,x,27-height,4,height,i<level?theme->label:theme->dim);
    }
}
void cap_render_status(int now_speaking,const char *detail) {
    speaking=now_speaking;
    fill(status,0,0,640,CAP_STATUS_ROWS,theme->strip);
    character(status,'*',LEFT,13,2,speaking?theme->label:theme->dim);
    meter();
    /* Right-aligned, cut short rather than over the meter. */
    size_t n=strlen(detail), room=(640-2*LEFT-80)/12;
    if(n>room) n=room;
    unsigned x=640-LEFT-12*(unsigned)n+2;
    for(size_t i=0;i<n;i++,x+=12) character(status,detail[i],x,13,2,theme->label);
}
int cap_render_level(double value) {
    unsigned bars=value<=0?0:value>=1?METER:(unsigned)(value*METER+0.5);
    if(bars==level) return 0;
    level=bars; meter(); return 1;
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
static int fb=-1, shown_valid, margins;
static unsigned scale=1, ox, oy, rows=240;
static struct fb_var_screeninfo var;
static struct fb_fix_screeninfo fix;
static uint8_t shown[640*(240+CAP_STATUS_ROWS)*3];
static uint8_t row[4096*4];
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
/* Fill the whole visible screen with the theme's background colour. */
static int clear_screen(void) {
    unsigned bytes=var.bits_per_pixel/8;
    uint32_t value=pack(theme->background);
    for(unsigned i=0;i<var.xres;i++) memcpy(row+i*bytes,&value,bytes);
    for(unsigned y=0;y<var.yres;y++) if(put(0,y,var.xres)) return -1;
    return 0;
}
int cap_fb_attach(int fd,const struct fb_var_screeninfo *v,const struct fb_fix_screeninfo *f,int want,int with_status) {
    if(!(v->bits_per_pixel==32 || v->bits_per_pixel==16) || v->xres<640 || v->xres>4096 || v->yres<240 ||
       !v->red.length || v->red.length>8 || !v->green.length || v->green.length>8 ||
       !v->blue.length || v->blue.length>8) return -1;
    var=*v; fix=*f; fb=fd; shown_valid=0; scale=1; ox=oy=0; rows=240; margins=0;
    level=0; speaking=0;
    fill(pixels,0,0,640,240,theme->background);
    if(want<0) return 0;
    /* Demo layout: whole-number scale centred across; the status strip, when
     * it fits, sits on the bottom edge and runs the full width. */
    if(with_status && v->yres>=240+CAP_STATUS_ROWS) { rows=240+CAP_STATUS_ROWS; cap_render_status(0,""); }
    unsigned fit=v->xres/640<v->yres/rows?v->xres/640:v->yres/rows;
    scale=want>0 && (unsigned)want<fit?(unsigned)want:fit;
    ox=(v->xres-640*scale)/2;
    oy=rows>240?v->yres-rows*scale:(v->yres-rows*scale)/2;
    margins=1;
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
        const uint8_t *src=y0<240?pixels+y0*640*3:status+(y0-240)*640*3, *old=shown+y0*640*3;
        /* Incremental: only the changed span of rows that differ from the last
         * write. A changed edge pixel repaints that margin in its colour. */
        unsigned lo=0, hi=640;
        if(!full && shown_valid) {
            while(lo<640 && !memcmp(src+lo*3,old+lo*3,3)) lo++;
            if(lo==640) continue;
            while(!memcmp(src+(hi-1)*3,old+(hi-1)*3,3)) hi--;
        }
        unsigned x0=ox+lo*scale, x1=ox+hi*scale;
        if(margins && !lo) x0=0;
        if(margins && hi==640) x1=var.xres;
        for(unsigned x=x0;x<x1;x++) {
            unsigned c=x<ox?0:x>=ox+640*scale?639:(x-ox)/scale;
            uint32_t value=pack(src+c*3);
            memcpy(row+(x-x0)*bytes,&value,bytes); /* little-endian */
        }
        for(unsigned k=0;k<scale;k++)
            if(put(x0,oy+y0*scale+k,x1-x0)) { shown_valid=0; return -1; }
        memcpy(shown+y0*640*3,src,640*3); written++;
    }
    shown_valid=1; return (int)written;
}
void cap_fb_close(void) { if(fb>=0) close(fb); fb=-1; }
