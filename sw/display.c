#define _GNU_SOURCE
#include "display.h"
#include <fcntl.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define WIDTH 640
#define HEIGHT 240 // captions
#define STATUS 40  // status strip under them
#define MAX_SCALE 6
#define VISIBLE 440 // newest characters kept on screen: 11 lines of 52, less the space word wrapping leaves

// 5x7 font: space, a-z, apostrophe, then 0-9 . : % - / for the status strip
static const uint8_t font[43][5] = {
    {0, 0, 0, 0, 0},       {126, 17, 17, 17, 126}, {127, 73, 73, 73, 54}, {62, 65, 65, 65, 34}, {127, 65, 65, 34, 28},
    {127, 73, 73, 73, 65}, {127, 9, 9, 9, 1},      {62, 65, 73, 73, 122}, {127, 8, 8, 8, 127},  {0, 65, 127, 65, 0},
    {32, 64, 65, 63, 1},   {127, 8, 20, 34, 65},   {127, 64, 64, 64, 64}, {127, 2, 12, 2, 127}, {127, 4, 8, 16, 127},
    {62, 65, 65, 65, 62},  {127, 9, 9, 9, 6},      {62, 65, 81, 33, 94},  {127, 9, 25, 41, 70}, {70, 73, 73, 73, 49},
    {1, 1, 127, 1, 1},     {63, 64, 64, 64, 63},   {31, 32, 64, 32, 31},  {63, 64, 56, 64, 63}, {99, 20, 8, 20, 99},
    {7, 8, 112, 8, 7},     {97, 81, 73, 69, 67},   {0, 5, 3, 0, 0},       {62, 81, 73, 69, 62}, {0, 66, 127, 64, 0},
    {66, 97, 81, 73, 70},  {33, 65, 69, 75, 49},   {24, 20, 18, 127, 16}, {39, 69, 69, 69, 57}, {60, 74, 73, 73, 48},
    {1, 113, 9, 5, 3},     {54, 73, 73, 73, 54},   {6, 73, 73, 41, 30},   {0, 96, 96, 0, 0},    {0, 54, 54, 0, 0},
    {35, 19, 8, 100, 98},  {8, 8, 8, 8, 8},        {32, 16, 8, 4, 2}};
static const uint8_t white[3] = {235, 255, 235}, amber[3] = {255, 190, 40}, blue[3] = {120, 200, 255},
                     background[3] = {12, 12, 12};

static uint8_t image[HEIGHT + STATUS][WIDTH][3], shown[HEIGHT + STATUS][WIDTH][3], row[WIDTH * MAX_SCALE * 4];
static int fb = -1, drawn;
static unsigned scale, left, top, rows;
static struct fb_var_screeninfo var;
static struct fb_fix_screeninfo fix;

// Font index; anything else is a space
static unsigned glyph(char c) {
    static const char marks[] = ".:%-/";
    const char* mark = c ? strchr(marks, c) : NULL;
    if (c >= 'a' && c <= 'z')
        return c - 'a' + 1;
    if (c >= '0' && c <= '9')
        return c - '0' + 28;
    return c == '\'' ? 27 : mark ? 38 + (unsigned)(mark - marks) : 0;
}

// text from `start` at twice the font size, word-wrapped within WIDTH and stopping at row `bottom`
static void draw(const char* text, size_t start, unsigned* x, unsigned* y, unsigned bottom, const uint8_t* rgb) {
    for (const char* s = text + start; *s; s++) {
        unsigned c = glyph(*s);
        // A word that does not fit starts the next line; a longer word than a line is split
        size_t word = s == text || s[-1] == ' ' ? strcspn(s, " ") : 0;
        if (*x + 12 >= WIDTH || (*x > 8 && *x + 12 * word >= WIDTH)) {
            *x = 8;
            *y += 20;
        }
        if (*s == ' ' && *x == 8) // no space at the start of a line
            continue;
        if (*y + 14 >= bottom)
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

// Write the image rows that changed, scaled
static void flush(void) {
    unsigned bytes = var.bits_per_pixel / 8;
    for (unsigned r = 0; r < rows; r++) {
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

int display_attach(int fd, const struct fb_var_screeninfo* v, const struct fb_fix_screeninfo* f, int status) {
    if ((v->bits_per_pixel != 16 && v->bits_per_pixel != 32) || v->xres < WIDTH || v->yres < HEIGHT)
        return -1;
    fb = fd;
    var = *v;
    fix = *f;
    drawn = 0;
    // The strip only when it fits; the largest whole-number scale, centred
    rows = status && v->yres >= HEIGHT + STATUS ? HEIGHT + STATUS : HEIGHT;
    scale = var.xres / WIDTH < var.yres / rows ? var.xres / WIDTH : var.yres / rows;
    scale = scale > MAX_SCALE ? MAX_SCALE : scale;
    left = (var.xres - WIDTH * scale) / 2;
    top = (var.yres - rows * scale) / 2;
    unsigned bytes = var.bits_per_pixel / 8, chunk = sizeof row / bytes;
    uint32_t value = pack(background);
    for (unsigned i = 0; i < chunk; i++)
        memcpy(row + i * bytes, &value, bytes);
    for (unsigned y = 0; y < var.yres; y++)
        for (unsigned x = 0; x < var.xres; x += chunk)
            put(x, y, var.xres - x < chunk ? var.xres - x : chunk);
    memset(image, background[0], sizeof image);
    if (rows > HEIGHT)
        display_status("");
    return 0;
}

int display_open(int status) {
    struct fb_var_screeninfo v;
    struct fb_fix_screeninfo f;
    int fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    if (fd < 0 || ioctl(fd, FBIOGET_VSCREENINFO, &v) || ioctl(fd, FBIOGET_FSCREENINFO, &f) ||
        display_attach(fd, &v, &f, status)) {
        if (fd >= 0)
            close(fd);
        fb = -1;
        return -1;
    }
    return 0;
}

void display_show(const char* committed, const char* tentative) {
    if (fb < 0)
        return;
    memset(image, background[0], sizeof image[0] * HEIGHT);
    unsigned x = 8, y = 20;
    size_t committed_length = strlen(committed), tentative_length = strlen(tentative);
    const char* longer = tentative_length > committed_length ? tentative : committed;
    size_t length = strlen(longer), skip = length > VISIBLE ? length - VISIBLE : 0;
    while (skip && longer[skip] && longer[skip - 1] != ' ') // start at a whole word
        skip++;
    committed += skip < committed_length ? skip : committed_length;
    tentative += skip < tentative_length ? skip : tentative_length;
    draw(committed, 0, &x, &y, HEIGHT, white);
    size_t prefix = strlen(committed);
    if (!strncmp(committed, tentative, prefix))
        draw(tentative, prefix, &x, &y, HEIGHT, amber);
    flush();
}

// A lighter strip under a thin separator line
void display_status(const char* text) {
    if (fb < 0 || rows == HEIGHT)
        return;
    memset(image[HEIGHT], 24, sizeof image[0] * STATUS);
    memset(image[HEIGHT], 60, sizeof image[0] * 2);
    unsigned x = 8, y = HEIGHT + 14;
    draw(text, 0, &x, &y, HEIGHT + STATUS, blue);
    flush();
}
