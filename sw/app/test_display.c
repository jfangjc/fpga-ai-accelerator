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
    const uint8_t background[3]={12,12,12};
    /* Measured layout: 1:1 at the top left; nothing else on screen is touched. */
    int fd=screen(640,480,0xaa,&v,&f);
    assert(!cap_fb_attach(fd,&v,&f,-1,1));
    cap_render_styled("hello world",styles);
    assert(cap_fb(1)==240);
    for(unsigned y=0;y<240;y+=7) for(unsigned x=0;x<640;x+=5)
        assert(at(fd,f.line_length,x,y)==rgb565(cap_pixels()+(y*640+x)*3));
    assert(at(fd,f.line_length,0,240)==0xaaaa && at(fd,f.line_length,639,479)==0xaaaa);
    assert(cap_fb(0)==0);
    cap_fb_close();
    /* Demo layout at 1080p: scale 3 with the status strip, centred, rest cleared. */
    fd=screen(1920,1080,0xaa,&v,&f);
    assert(!cap_fb_attach(fd,&v,&f,0,1));
    assert(at(fd,f.line_length,0,0)==rgb565(background) && at(fd,f.line_length,1919,1079)==rgb565(background));
    cap_render_styled("hello world",styles);
    cap_render_status("speaking   pl engine   70 ms/window");
    assert(cap_fb(0)==280);
    unsigned oy=(1080-280*3)/2, lit=0;
    for(unsigned y=0;y<240;y+=3) for(unsigned x=0;x<640;x+=3)
        for(unsigned k=0;k<9;k++)
            assert(at(fd,f.line_length,x*3+k%3,oy+y*3+k/3)==rgb565(cap_pixels()+(y*640+x)*3));
    for(unsigned x=0;x<1920;x++) lit+=at(fd,f.line_length,x,oy+(240+17)*3)!=at(fd,f.line_length,0,oy+(240+17)*3);
    assert(lit>0);
    assert(at(fd,f.line_length,5,oy-1)==rgb565(background));
    assert(cap_fb(0)==0);
    cap_render_status("listening   pl engine   70 ms/window");
    int changed=cap_fb(0); assert(changed>0 && changed<=(int)CAP_STATUS_ROWS);
    cap_fb_close();
    /* A requested scale caps the fit and stays centred. */
    fd=screen(1920,1080,0,&v,&f);
    assert(!cap_fb_attach(fd,&v,&f,2,0));
    cap_render_styled("a",styles);
    assert(cap_fb(1)==240);
    assert(at(fd,f.line_length,(1920-1280)/2+16,(1080-480)/2+40)==rgb565(cap_pixels()+(20*640+8)*3));
    cap_fb_close();
    puts("PASS framebuffer layouts: measured 1:1, 1080p scale 3 with status, capped scale 2");
    return 0;
}
