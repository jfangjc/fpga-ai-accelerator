// Framebuffer layouts on a file standing in for /dev/fb0 (RGB565): make -C sw test
#include "display.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static const uint8_t background[3] = {12, 12, 12}, white[3] = {235, 255, 235}, blue[3] = {120, 200, 255};

static uint16_t rgb565(const uint8_t* p) { return (uint16_t)((p[0] >> 3) << 11 | (p[1] >> 2) << 5 | p[2] >> 3); }

static uint16_t at(int fd, unsigned width, unsigned x, unsigned y) {
    uint16_t v;
    assert(pread(fd, &v, 2, ((off_t)y * width + x) * 2) == 2);
    return v;
}

// A width x height screen filled with 0xaa bytes
static int screen(unsigned width, unsigned height, struct fb_var_screeninfo* v, struct fb_fix_screeninfo* f) {
    static uint8_t line[1920 * 2];
    FILE* file = tmpfile();
    int fd = dup(fileno(file));
    fclose(file);
    memset(line, 0xaa, sizeof line);
    for (unsigned y = 0; y < height; y++)
        assert(pwrite(fd, line, width * 2, (off_t)y * width * 2) == (ssize_t)(width * 2));
    memset(v, 0, sizeof *v);
    memset(f, 0, sizeof *f);
    v->xres = width;
    v->yres = height;
    v->bits_per_pixel = 16;
    v->red = (struct fb_bitfield){11, 5, 0};
    v->green = (struct fb_bitfield){5, 6, 0};
    v->blue = (struct fb_bitfield){0, 5, 0};
    f->line_length = width * 2;
    return fd;
}

// Pixels of one colour in screen row y
static unsigned count(int fd, unsigned width, unsigned y, const uint8_t* rgb) {
    unsigned n = 0;
    for (unsigned x = 0; x < width; x++)
        n += at(fd, width, x, y) == rgb565(rgb);
    return n;
}

int main(void) {
    struct fb_var_screeninfo v;
    struct fb_fix_screeninfo f;

    // 1080p with the status strip: 280 rows at scale 3, centred, the rest of the screen cleared
    int fd = screen(1920, 1080, &v, &f);
    assert(!display_attach(fd, &v, &f, 1));
    unsigned top = (1080 - 280 * 3) / 2;
    assert(at(fd, 1920, 0, 0) == rgb565(background) && at(fd, 1920, 1919, 1079) == rgb565(background));
    display_show("hello world", "hello world and");
    unsigned glyph_row = top + (20 + 6) * 3; // a row through the middle of the first text line
    assert(count(fd, 1920, glyph_row, white) > 0);
    assert(count(fd, 1920, top - 1, background) == 1920);
    display_status("speaking   fpga engine   decode 70 ms   delay 120 ms");
    unsigned strip_row = top + (240 + 14 + 6) * 3;
    unsigned before = count(fd, 1920, strip_row, blue);
    assert(before > 0);
    display_status("listening");
    assert(count(fd, 1920, strip_row, blue) > 0 && count(fd, 1920, strip_row, blue) < before);
    // Clearing leaves the captions empty and the strip in place
    display_show("", "");
    assert(count(fd, 1920, glyph_row, white) == 0 && count(fd, 1920, strip_row, blue) > 0);
    close(fd);

    // 640x480 has no room for scale 2: scale 1 with the strip, centred
    fd = screen(640, 480, &v, &f);
    assert(!display_attach(fd, &v, &f, 1));
    display_status("0123456789 .:%-/");
    assert(count(fd, 640, (480 - 280) / 2 + 240 + 14 + 3, blue) > 0);
    close(fd);

    // Without the strip, the original layout: 240 rows, scale 3 at 1080p
    fd = screen(1920, 1080, &v, &f);
    assert(!display_attach(fd, &v, &f, 0));
    display_show("a", "");
    assert(count(fd, 1920, (1080 - 240 * 3) / 2 + 26 * 3, white) > 0);
    display_status("ignored");
    assert(count(fd, 1920, (1080 - 240 * 3) / 2 + 240 * 3 + 20, background) == 1920);
    close(fd);

    puts("PASS display: 1080p with status, 640x480 with status, no status");
    return 0;
}
