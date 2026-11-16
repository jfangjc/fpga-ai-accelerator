// Framebuffer layouts on a file standing in for /dev/fb0 (RGB565): make -C sw test
#define _GNU_SOURCE // pread, pwrite, fileno and off_t under -std=c11
#include "display.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

// The butter theme's colours
static const uint8_t background[3] = {255, 244, 194}, text[3] = {26, 26, 26}, tentative[3] = {154, 74, 14},
                     history[3] = {122, 106, 58}, strip[3] = {26, 26, 26}, label[3] = {255, 220, 0},
                     night[3] = {18, 20, 28}, night_text[3] = {242, 240, 232};

static uint16_t rgb565(const uint8_t* p) { return (uint16_t)((p[0] >> 3) << 11 | (p[1] >> 2) << 5 | p[2] >> 3); }

static uint16_t at(int fd, unsigned width, unsigned x, unsigned y) {
    uint16_t v;
    assert(pread(fd, &v, 2, ((off_t)y * width + x) * 2) == 2);
    return v;
}

// A width x height screen filled with 0xaa bytes
static int screen(unsigned width, unsigned height, struct fb_var_screeninfo* v, struct fb_fix_screeninfo* f) {
    static uint8_t line[3440 * 2];
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

// Pixels of one colour in screen row y, between x0 and x1
static unsigned count(int fd, unsigned width, unsigned y, unsigned x0, unsigned x1, const uint8_t* rgb) {
    unsigned n = 0;
    for (unsigned x = x0; x < x1; x++)
        n += at(fd, width, x, y) == rgb565(rgb);
    return n;
}
#define ROW(fd, w, y, rgb) count(fd, w, y, 0, w, rgb)

int main(void) {
    struct fb_var_screeninfo v;
    struct fb_fix_screeninfo f;

    // 1080p, butter, with the strip: scale 3, the 280 rows on the bottom edge, everything else the background
    int fd = screen(1920, 1080, &v, &f);
    assert(!display_attach(fd, &v, &f, 1, DISPLAY_BUTTER));
    unsigned top = 1080 - 280 * 3;
    assert(at(fd, 1920, 0, 0) == rgb565(background) && at(fd, 1920, 1919, top - 1) == rgb565(background));
    unsigned strip_row = top + (240 + 5) * 3, meter_row = top + (240 + 26) * 3;
    assert(ROW(fd, 1920, strip_row, strip) + ROW(fd, 1920, strip_row, label) == 1920); // the full width
    assert(at(fd, 1920, 0, 1079) == rgb565(strip));

    // A live phrase on the first live line: settled words in the text colour, the rest tentative
    unsigned live_row = top + (116 + 10) * 3, history_row = top + (80 + 6) * 3;
    display_show("", "hello world and", 11);
    assert(ROW(fd, 1920, live_row, text) > 0 && ROW(fd, 1920, live_row, tentative) > 0);
    assert(ROW(fd, 1920, history_row, history) == 0);
    // The next phrase moves the last one up into the history
    display_show("hello world and more", "next", 0);
    assert(ROW(fd, 1920, history_row, history) > 0 && ROW(fd, 1920, live_row, text) == 0);
    // A long phrase keeps its newest two lines and still starts on the first live line; its first line moves up
    // into the history rather than off the screen
    display_show("", "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen", 90);
    assert(ROW(fd, 1920, live_row, text) > 0 && ROW(fd, 1920, top + (148 + 10) * 3, text) > 0);
    assert(ROW(fd, 1920, history_row, history) > 0);

    // The strip: speaking lights the dot, the detail is right-aligned, the meter follows the level
    unsigned dot_row = top + (240 + 20) * 3, meter_x0 = (1920 - 640 * 3) / 2 + (32 + 20) * 3,
             meter_x1 = meter_x0 + 49 * 3;
    display_status(1, "fpga engine  decode 70 ms  delay 90 ms");
    assert(count(fd, 1920, dot_row, 32 * 3, 42 * 3, label) > 0);
    assert(count(fd, 1920, dot_row, 1920 - 32 * 3 - 12 * 3, 1920 - 32 * 3, label) > 0);
    display_level(0);
    assert(count(fd, 1920, meter_row, meter_x0, meter_x1, label) == 0);
    display_level(1);
    assert(count(fd, 1920, meter_row, meter_x0, meter_x1, label) == 7 * 4 * 3);
    display_level(0.3); // two bars
    assert(count(fd, 1920, meter_row, meter_x0, meter_x1, label) == 2 * 4 * 3);
    display_status(0, "fpga engine");
    assert(count(fd, 1920, dot_row, 32 * 3, 42 * 3, label) == 0);
    // Clearing empties the captions and leaves the strip
    display_show("", "", 0);
    assert(ROW(fd, 1920, live_row, background) == 1920 && ROW(fd, 1920, history_row, background) == 1920);
    assert(ROW(fd, 1920, dot_row, label) > 0);
    close(fd);

    // 3440x1440, night: scale 5, centred across
    fd = screen(3440, 1440, &v, &f);
    assert(!display_attach(fd, &v, &f, 1, DISPLAY_NIGHT));
    assert(at(fd, 3440, 0, 0) == rgb565(night));
    static const uint8_t night_strip[3] = {28, 31, 42};
    assert(at(fd, 3440, 0, 1439) == rgb565(night_strip) && at(fd, 3440, 3439, 1439) == rgb565(night_strip));
    display_show("", "a", 1);
    assert(count(fd, 3440, 1440 - 280 * 5 + (116 + 10) * 5, (3440 - 3200) / 2 + 32 * 5,
                 (3440 - 3200) / 2 + 37 * 5, night_text) > 0);
    close(fd);

    // 640x480 has no room for scale 2: scale 1 with the strip
    fd = screen(640, 480, &v, &f);
    assert(!display_attach(fd, &v, &f, 1, DISPLAY_BUTTER));
    display_status(1, "0123456789 .:%-/");
    assert(ROW(fd, 640, 480 - 40 + 20, label) > 0);
    close(fd);

    // Without the strip: 240 rows centred, and the strip calls do nothing
    fd = screen(1920, 1080, &v, &f);
    assert(!display_attach(fd, &v, &f, 0, DISPLAY_BUTTER));
    top = (1080 - 240 * 3) / 2;
    display_show("", "a", 1);
    assert(ROW(fd, 1920, top + (116 + 10) * 3, text) > 0);
    display_status(1, "ignored");
    display_level(1);
    assert(ROW(fd, 1920, top + 240 * 3 + 20, background) == 1920);
    close(fd);

    puts("PASS display: 1080p butter with status, 3440x1440 night, 640x480, no status");
    return 0;
}
