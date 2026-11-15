/* Framebuffer layouts on a file standing in for /dev/fb0 (RGB565). */
#include "display.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
static uint16_t rgb565(const uint8_t *p) { return (uint16_t)((p[0]>>3)<<11 | (p[1]>>2)<<5 | p[2]>>3); }
static uint16_t at(int fd,unsigned stride,unsigned x,unsigned y) {
    uint16_t v; assert(pread(fd,&v,2,(off_t)y*stride+x*2)==2); return v;
}
static int screen(unsigned w,unsigned h,int fill,struct fb_var_screeninfo *v,struct fb_fix_screeninfo *f) {
    FILE *file=tmpfile(); assert(file);
    int fd=dup(fileno(file)); fclose(file);
    static uint8_t line[1920*2]; memset(line,fill,sizeof(line));
    for(unsigned y=0;y<h;y++) assert(pwrite(fd,line,w*2,(off_t)y*w*2)==(ssize_t)(w*2));
    memset(v,0,sizeof(*v)); memset(f,0,sizeof(*f));
    v->xres=w; v->yres=h; v->bits_per_pixel=16;
    v->red=(struct fb_bitfield){11,5,0}; v->green=(struct fb_bitfield){5,6,0}; v->blue=(struct fb_bitfield){0,5,0};
    f->line_length=w*2; return fd;
}
int main(void) {
    struct fb_var_screeninfo v; struct fb_fix_screeninfo f;
    const unsigned char styles[64]={0};
    const uint8_t background[3]={255,244,194}, strip[3]={26,26,26}, label[3]={255,220,0}, text[3]={26,26,26},
                  tentative[3]={154,74,14}, history[3]={122,106,58};
    cap_theme(1);
    /* Measured layout: 1:1 at the top left; nothing else on screen is touched. */
    int fd=screen(640,480,0xaa,&v,&f);
    assert(!cap_fb_attach(fd,&v,&f,-1,1));
    cap_render_styled("hello world",styles,0);
    assert(cap_fb(1)==240);
    for(unsigned y=0;y<240;y+=7) for(unsigned x=0;x<640;x+=5)
        assert(at(fd,f.line_length,x,y)==rgb565(cap_pixels()+(y*640+x)*3));
    assert(at(fd,f.line_length,0,240)==0xaaaa && at(fd,f.line_length,639,479)==0xaaaa);
    assert(cap_fb(0)==0);
    cap_fb_close();
    /* Demo layout at 1080p: scale 3, the strip on the bottom edge, rest cleared. */
    fd=screen(1920,1080,0xaa,&v,&f);
    assert(!cap_fb_attach(fd,&v,&f,0,1));
    assert(at(fd,f.line_length,0,0)==rgb565(background) && at(fd,f.line_length,1919,1079)==rgb565(background));
    cap_render_styled("hello world",styles,0);
    cap_render_status(1,"cpu int8  decode 219 ms  delay 260 ms");
    assert(cap_fb(0)==280);
    assert(at(fd,f.line_length,0,1079)==rgb565(strip) && at(fd,f.line_length,1919,1079)==rgb565(strip));
    unsigned oy=1080-280*3, lit=0;
    for(unsigned y=0;y<240;y+=3) for(unsigned x=0;x<640;x+=3)
        for(unsigned k=0;k<9;k++)
            assert(at(fd,f.line_length,x*3+k%3,oy+y*3+k/3)==rgb565(cap_pixels()+(y*640+x)*3));
    for(unsigned x=0;x<1920;x++) lit+=at(fd,f.line_length,x,oy+(240+20)*3)==rgb565(label);
    assert(lit>0);
    assert(at(fd,f.line_length,5,oy-1)==rgb565(background));
    assert(cap_fb(0)==0);
    /* History dim, then the live phrase: settled in the text colour, tentative after it. */
    unsigned char mixed[64]={0}; memset(mixed+17,1,4);
    cap_render_styled("earlier one more and",mixed,9);
    unsigned live_row=(116+10)*640*3, text_lit=0, tentative_lit=0, history_lit=0;
    for(unsigned x=0;x<640;x++) {
        text_lit+=!memcmp(cap_pixels()+live_row+x*3,text,3);
        tentative_lit+=!memcmp(cap_pixels()+live_row+x*3,tentative,3);
        history_lit+=!memcmp(cap_pixels()+(80+6)*640*3+x*3,history,3);
    }
    assert(text_lit>0 && tentative_lit>0 && history_lit>0);
    /* The meter: two bars at 0.3, redrawn only when it changes. */
    assert(cap_render_level(0.3)==1 && cap_render_level(0.3)==0);
    unsigned bars=0;
    for(unsigned x=0;x<1920;x++) bars+=at(fd,f.line_length,x,oy+(240+26)*3)==rgb565(label);
    int changed=cap_fb(0); assert(changed>0 && changed<=(int)CAP_STATUS_ROWS+240);
    unsigned after=0;
    for(unsigned x=0;x<1920;x++) after+=at(fd,f.line_length,x,oy+(240+26)*3)==rgb565(label);
    assert(after==bars+2*4*3);
    cap_render_status(0,"cpu int8");
    changed=cap_fb(0); assert(changed>0 && changed<=(int)CAP_STATUS_ROWS);
    cap_fb_close();
    /* A requested scale caps the fit and stays centred. */
    fd=screen(1920,1080,0,&v,&f);
    assert(!cap_fb_attach(fd,&v,&f,2,0));
    cap_render_styled("a",styles,0);
    assert(cap_fb(1)==240);
    assert(at(fd,f.line_length,(1920-1280)/2+2*40,(1080-480)/2+2*126)==rgb565(cap_pixels()+(126*640+40)*3));
    cap_fb_close();
    puts("PASS framebuffer layouts: measured 1:1, 1080p scale 3 with strip, live line, meter, capped scale 2");
    return 0;
}
