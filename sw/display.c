#define _GNU_SOURCE
#include "display.h"
#include <fcntl.h>
#include <linux/fb.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define WIDTH 640
#define HEIGHT 240
#define MAX_SCALE 6

// 5x7 font: space, a-z, apostrophe
static const uint8_t font[28][5] = {
    {0, 0, 0, 0, 0},       {126, 17, 17, 17, 126}, {127, 73, 73, 73, 54}, {62, 65, 65, 65, 34}, {127, 65, 65, 34, 28},
    {127, 73, 73, 73, 65}, {127, 9, 9, 9, 1},      {62, 65, 73, 73, 122}, {127, 8, 8, 8, 127},  {0, 65, 127, 65, 0},
    {32, 64, 65, 63, 1},   {127, 8, 20, 34, 65},   {127, 64, 64, 64, 64}, {127, 2, 12, 2, 127}, {127, 4, 8, 16, 127},
    {62, 65, 65, 65, 62},  {127, 9, 9, 9, 6},      {62, 65, 81, 33, 94},  {127, 9, 25, 41, 70}, {70, 73, 73, 73, 49},
    {1, 1, 127, 1, 1},     {63, 64, 64, 64, 63},   {31, 32, 64, 32, 31},  {63, 64, 56, 64, 63}, {99, 20, 8, 20, 99},
    {7, 8, 112, 8, 7},     {97, 81, 73, 69, 67},   {0, 5, 3, 0, 0}};
static const uint8_t white[3] = {235, 255, 235}, amber[3] = {255, 190, 40}, background[3] = {12, 12, 12};

static uint8_t image[HEIGHT][WIDTH][3], shown[HEIGHT][WIDTH][3], row[WIDTH * MAX_SCALE * 4];
static int fb = -1, drawn;
static unsigned scale, left, top;
static struct fb_var_screeninfo var;
static struct fb_fix_screeninfo fix;

static void draw(const char* s, unsigned* x, unsigned* y, const uint8_t* rgb) {
    for (; *s; s++) {
        unsigned c = *s >= 'a' && *s <= 'z' ? *s - 'a' + 1 : *s == '\'' ? 27 : 0;
        if (*x + 12 >= WIDTH) {
            *x = 8;
            *y += 20;
        }
        if (*y + 14 >= HEIGHT)
            break;
        for (unsigned i = 0; i < 10; i++)
            for (unsigned j = 0; j < 14; j++)
                if (font[c][i / 2] & (1 << j / 2))
                    memcpy(image[*y + j][*x + i], rgb, 3);
        *x += 12;
    }
}

static uint32_t pack(const uint8_t* p) {
    return (uint32_t)(p[0] >> (8 - var.red.length)) << var.red.offset |
           (uint32_t)(p[1] >> (8 - var.green.length)) << var.green.offset |
           (uint32_t)(p[2] >> (8 - var.blue.length)) << var.blue.offset;
}

static ssize_t put(unsigned x, unsigned y, unsigned pixels) {
    unsigned bytes = var.bits_per_pixel / 8;
    return pwrite(fb, row, pixels * bytes,
                  (off_t)(y + var.yoffset) * fix.line_length + (off_t)(x + var.xoffset) * bytes);
}

int display_open(void) {
    fb = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    if (fb < 0 || ioctl(fb, FBIOGET_VSCREENINFO, &var) || ioctl(fb, FBIOGET_FSCREENINFO, &fix) ||
        (var.bits_per_pixel != 16 && var.bits_per_pixel != 32) || var.xres < WIDTH || var.yres < HEIGHT) {
        if (fb >= 0)
            close(fb);
        fb = -1;
        return -1;
    }
    scale = var.xres / WIDTH < var.yres / HEIGHT ? var.xres / WIDTH : var.yres / HEIGHT;
    scale = scale > MAX_SCALE ? MAX_SCALE : scale;
    left = (var.xres - WIDTH * scale) / 2;
    top = (var.yres - HEIGHT * scale) / 2;
    unsigned bytes = var.bits_per_pixel / 8, chunk = sizeof row / bytes;
    uint32_t value = pack(background);
    for (unsigned i = 0; i < chunk; i++)
        memcpy(row + i * bytes, &value, bytes);
    for (unsigned y = 0; y < var.yres; y++)
        for (unsigned x = 0; x < var.xres; x += chunk)
            put(x, y, var.xres - x < chunk ? var.xres - x : chunk);
    return 0;
}

void display_show(const char* committed, const char* tentative) {
    if (fb < 0)
        return;
    memset(image, background[0], sizeof image);
    unsigned x = 8, y = 20;
    size_t committed_length = strlen(committed), tentative_length = strlen(tentative);
    size_t length = tentative_length > committed_length ? tentative_length : committed_length;
    size_t skip = length > 480 ? length - 480 : 0;
    committed += skip < committed_length ? skip : committed_length;
    tentative += skip < tentative_length ? skip : tentative_length;
    draw(committed, &x, &y, white);
    size_t prefix = strlen(committed);
    if (!strncmp(committed, tentative, prefix))
        draw(tentative + prefix, &x, &y, amber);
    // Changed rows only
    unsigned bytes = var.bits_per_pixel / 8;
    for (unsigned r = 0; r < HEIGHT; r++) {
        if (drawn && !memcmp(image[r], shown[r], sizeof image[r]))
            continue;
        for (unsigned c = 0; c < WIDTH; c++) {
            uint32_t value = pack(image[r][c]);
            for (unsigned k = 0; k < scale; k++)
                memcpy(row + (c * scale + k) * bytes, &value, bytes);
        }
        for (unsigned k = 0; k < scale; k++)
            put(left, top + r * scale + k, WIDTH * scale);
        memcpy(shown[r], image[r], sizeof image[r]);
    }
    drawn = 1;
}
