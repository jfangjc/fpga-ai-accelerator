/*
    captions on the monitor through the linux framebuffer
    -> everything gets drawn into a small 640 x 280 rgb image then scaled up by a whole number to fill the screen
    -> /dev/fb0 is memory the ps displayport controller scans out so writing bytes there is all it takes to show them
    -> only the rows that changed get written again so a preview that adds one word costs a few rows
    -> two dim history lines sit above two large live lines and live lines that overflow move up into the history
*/
#define _GNU_SOURCE
#include "display.h"
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define WIDTH 640   // the layout before it gets scaled up by a whole number to fill the screen
#define HEIGHT 240  // caption area
#define STATUS 40   // status strip under the captions along the bottom of the screen
#define MAX_XRES 4096
#define LEFT 32     // left edge of all text
#define HISTORY 48  // characters per history line -> (WIDTH - 2 * LEFT) / 12
#define LIVE 32     // characters per live line -> (WIDTH - 2 * LEFT) / 18
#define METER 7     // bars in the level meter

// 5x7 font -> space then a-z then apostrophe then 0-9 . : % - / and a dot (one byte per column with bit 0 at the top)
static const uint8_t font[44][5] = {
    {0, 0, 0, 0, 0},       {126, 17, 17, 17, 126}, {127, 73, 73, 73, 54}, {62, 65, 65, 65, 34}, {127, 65, 65, 34, 28},
    {127, 73, 73, 73, 65}, {127, 9, 9, 9, 1},      {62, 65, 73, 73, 122}, {127, 8, 8, 8, 127},  {0, 65, 127, 65, 0},
    {32, 64, 65, 63, 1},   {127, 8, 20, 34, 65},   {127, 64, 64, 64, 64}, {127, 2, 12, 2, 127}, {127, 4, 8, 16, 127},
    {62, 65, 65, 65, 62},  {127, 9, 9, 9, 6},      {62, 65, 81, 33, 94},  {127, 9, 25, 41, 70}, {70, 73, 73, 73, 49},
    {1, 1, 127, 1, 1},     {63, 64, 64, 64, 63},   {31, 32, 64, 32, 31},  {63, 64, 56, 64, 63}, {99, 20, 8, 20, 99},
    {7, 8, 112, 8, 7},     {97, 81, 73, 69, 67},   {0, 5, 3, 0, 0},       {62, 81, 73, 69, 62}, {0, 66, 127, 64, 0},
    {66, 97, 81, 73, 70},  {33, 65, 69, 75, 49},   {24, 20, 18, 127, 16}, {39, 69, 69, 69, 57}, {60, 74, 73, 73, 48},
    {1, 113, 9, 5, 3},     {54, 73, 73, 73, 54},   {6, 73, 73, 41, 30},   {0, 96, 96, 0, 0},    {0, 54, 54, 0, 0},
    {35, 19, 8, 100, 98},  {8, 8, 8, 8, 8},        {32, 16, 8, 4, 2},     {28, 62, 62, 62, 28}};

// flat colours only since the framebuffer is rgb565 and gradients would band
static const struct theme {
    uint8_t background[3], text[3], tentative[3], history[3], strip[3], label[3], dim[3];
} themes[] = {
    [DISPLAY_NIGHT] = {{18, 20, 28}, {242, 240, 232}, {242, 179, 61}, {124, 128, 145},
                       {28, 31, 42}, {142, 197, 255}, {58, 63, 82}},
    [DISPLAY_BUTTER] = {{255, 244, 194}, {26, 26, 26}, {154, 74, 14}, {122, 106, 58},
                        {26, 26, 26}, {255, 220, 0}, {74, 70, 54}},
};
static const struct theme* theme = &themes[DISPLAY_NIGHT];

static uint8_t image[HEIGHT + STATUS][WIDTH][3], shown[HEIGHT + STATUS][WIDTH][3], row[MAX_XRES * 4];   // what to draw then what's on screen then one scaled row
static int fb = -1, drawn, speaking;
static unsigned scale, left, top, rows, level;   // whole number scale and where the scaled layout sits on screen
static struct fb_var_screeninfo var;             // resolution and pixel format from the kernel
static struct fb_fix_screeninfo fix;             // bytes per screen line

// which glyph in the font -> anything else is a space
static unsigned glyph(char c) {
    static const char marks[] = ".:%-/*";
    const char* mark = c ? strchr(marks, c) : NULL;

    if (c >= 'a' && c <= 'z')
        return c - 'a' + 1;

    if (c >= '0' && c <= '9')
        return c - '0' + 28;

    return c == '\'' ? 27 : mark ? 38 + (unsigned)(mark - marks) : 0;
}

// paints a rectangle of the image in one colour
static void fill(unsigned x, unsigned y, unsigned w, unsigned h, const uint8_t* rgb) {
    for (unsigned j = y; j < y + h; j++)
        for (unsigned i = x; i < x + w && i < WIDTH; i++)
            memcpy(image[j][i], rgb, 3);
}

// draws one character at size times the font -> 5 x 7 pixels and a (6 * size) pixel advance
static void character(char c, unsigned x, unsigned y, unsigned size, const uint8_t* rgb) {
    const uint8_t* bits = font[glyph(c)];

    // -> each set bit becomes a size x size square
    for (unsigned i = 0; i < 5; i++)
        for (unsigned j = 0; j < 7; j++)
            if (bits[i] & 1 << j)
                fill(x + i * size, y + j * size, size, size, rgb);
}

// word wraps text at cols characters and hands back the last max lines as [start end) offsets oldest first
static unsigned wrap(const char* text, unsigned cols, size_t* start, size_t* end, unsigned max) {
    size_t length = strlen(text), at = 0, s[64], e[64];
    unsigned n = 0;
    while (1) {
        while (text[at] == ' ')
            at++;
        if (!text[at])
            break;
        size_t stop = length - at <= cols ? length : at + cols, next = stop;
        if (stop < length) { // break at the last space that fits -> a word longer than a line gets split
            size_t k = stop;
            while (k > at && text[k] != ' ')
                k--;
            if (k > at)
                stop = next = k;
        }

        // -> only the newest 64 lines are kept in a ring since the caller wants the last few
        s[n % 64] = at;
        e[n % 64] = stop;
        n++;
        at = next;
    }

    unsigned kept = n < max ? n : max;
    for (unsigned i = 0; i < kept; i++) {
        start[i] = s[(n - kept + i) % 64];
        end[i] = e[(n - kept + i) % 64];
    }

    return kept;
}

// turns an rgb pixel into the screen's format (rgb565 or 32 bit) using the offsets the kernel gave
static uint32_t pack(const uint8_t* p) {
    return (uint32_t)(p[0] >> (8 - var.red.length)) << var.red.offset |
           (uint32_t)(p[1] >> (8 - var.green.length)) << var.green.offset |
           (uint32_t)(p[2] >> (8 - var.blue.length)) << var.blue.offset;
}

// writes pixels from row into the framebuffer at (x y)
static ssize_t put(unsigned x, unsigned y, unsigned pixels) {
    unsigned bytes = var.bits_per_pixel / 8;
    return pwrite(fb, row, pixels * bytes,
                  (off_t)(y + var.yoffset) * fix.line_length + (off_t)(x + var.xoffset) * bytes);
}

// writes the changed span of each changed row to the screen scaled up
static void flush(void) {
    unsigned bytes = var.bits_per_pixel / 8;
    for (unsigned r = 0; r < rows; r++) {
        // -> find the first and last pixel that differ from what's on screen and skip rows that didn't change
        unsigned lo = 0, hi = WIDTH;
        if (drawn) {
            while (lo < WIDTH && !memcmp(image[r][lo], shown[r][lo], 3))
                lo++;
            if (lo == WIDTH)
                continue;
            while (!memcmp(image[r][hi - 1], shown[r][hi - 1], 3))
                hi--;
        }

        // -> a changed edge pixel repaints that whole margin in its colour so the strip runs the full width of the screen
        unsigned x0 = lo ? left + lo * scale : 0, x1 = hi < WIDTH ? left + hi * scale : var.xres;

        // -> build one scaled screen row then write it scale times
        for (unsigned x = x0; x < x1; x++) {
            unsigned c = x < left ? 0 : x >= left + WIDTH * scale ? WIDTH - 1 : (x - left) / scale;
            uint32_t value = pack(image[r][c]);
            memcpy(row + (x - x0) * bytes, &value, bytes);
        }

        for (unsigned k = 0; k < scale; k++)
            put(x0, top + r * scale + k, x1 - x0);
        memcpy(shown[r], image[r], sizeof image[r]);
    }
    drawn = 1;
}

// sets up on an open framebuffer -> test_display calls this directly with a fake one
int display_attach(int fd, const struct fb_var_screeninfo* v, const struct fb_fix_screeninfo* f, int status,
                   int which) {
    if ((v->bits_per_pixel != 16 && v->bits_per_pixel != 32) || v->xres < WIDTH || v->xres > MAX_XRES ||
        v->yres < HEIGHT)
        return -1;
    fb = fd;
    var = *v;
    fix = *f;
    theme = &themes[which == DISPLAY_BUTTER ? DISPLAY_BUTTER : DISPLAY_NIGHT];
    drawn = speaking = 0;
    level = 0;
    // -> the largest whole number scale centred across and the strip sits on the bottom edge when it fits
    rows = status && v->yres >= HEIGHT + STATUS ? HEIGHT + STATUS : HEIGHT;
    scale = var.xres / WIDTH < var.yres / rows ? var.xres / WIDTH : var.yres / rows;
    left = (var.xres - WIDTH * scale) / 2;
    top = rows > HEIGHT ? var.yres - rows * scale : (var.yres - rows * scale) / 2;
    // -> clear the whole screen to the background once so the margins match the theme
    unsigned bytes = var.bits_per_pixel / 8;
    uint32_t value = pack(theme->background);
    for (unsigned i = 0; i < var.xres; i++)
        memcpy(row + i * bytes, &value, bytes);
    for (unsigned y = 0; y < var.yres; y++)
        put(0, y, var.xres);
    fill(0, 0, WIDTH, HEIGHT, theme->background);
    memcpy(shown, image, sizeof shown);
    drawn = 1;
    if (rows > HEIGHT)
        display_status(0, "");
    return 0;
}

// opens /dev/fb0 and asks the kernel for its size and pixel format
int display_open(int status, int which) {
    struct fb_var_screeninfo v;
    struct fb_fix_screeninfo f;
    int fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    if (fd < 0 || ioctl(fd, FBIOGET_VSCREENINFO, &v) || ioctl(fd, FBIOGET_FSCREENINFO, &f) ||
        display_attach(fd, &v, &f, status, which)) {
        if (fd >= 0)
            close(fd);
        fb = -1;
        return -1;
    }
    return 0;
}

// draws the history and the live phrase -> called by emit on every preview and final
void display_show(const char* history, const char* live, size_t stable) {
    static char past[8192];
    size_t start[64], end[64];
    if (fb < 0)
        return;
    fill(0, 0, WIDTH, HEIGHT, theme->background);
    // -> a live phrase longer than two lines moves its earlier lines up into the history so no word leaves the screen unseen
    unsigned lines = wrap(live, LIVE, start, end, 64);
    size_t moved = lines > 2 ? start[lines - 2] : 0, h = strlen(history);
    while (moved && live[moved - 1] == ' ')
        moved--;
    if (h + moved + 2 > sizeof past) // the oldest history is off the screen anyway
        history += h + moved + 2 - sizeof past, h = strlen(history);
    snprintf(past, sizeof past, "%s%s%.*s", history, h && moved ? " " : "", (int)moved, live);
    // -> history is two lines at twice the font with tops at rows 60 and 80 and only the newest lines show
    unsigned n = wrap(past, HISTORY, start, end, 2);
    for (unsigned i = 0; i < n; i++)
        for (size_t j = start[i]; j < end[i]; j++)
            character(past[j], LEFT + 12 * (unsigned)(j - start[i]), 60 + 20 * (i + 2 - n), 2, theme->history);
    // -> live is two lines at three times the font at rows 116 and 148 so the eye stays in one place
    // -> characters before stable are settled and get the main colour while the rest get the tentative colour
    n = wrap(live, LIVE, start, end, 2);
    for (unsigned i = 0; i < n; i++)
        for (size_t j = start[i]; j < end[i]; j++)
            character(live[j], LEFT + 18 * (unsigned)(j - start[i]), 116 + 32 * i, 3,
                      j < stable ? theme->text : theme->tentative);
    flush();
}

// seven bars rising left to right and lit up to the level
static void meter(void) {
    for (unsigned i = 0; i < METER; i++) {
        unsigned height = 4 + 2 * i, x = LEFT + 20 + 7 * i;
        fill(x, HEIGHT + 11, 4, 16, theme->strip);
        fill(x, HEIGHT + 27 - height, 4, height, i < level ? theme->label : theme->dim);
    }
}

// redraws the strip -> the speaking dot then the meter then the detail text on the right
void display_status(int now_speaking, const char* detail) {
    if (fb < 0 || rows == HEIGHT)
        return;
    speaking = now_speaking;
    fill(0, HEIGHT, WIDTH, STATUS, theme->strip);
    character('*', LEFT, HEIGHT + 13, 2, speaking ? theme->label : theme->dim);
    meter();
    // -> right aligned and cut short instead of running over the meter
    size_t length = strlen(detail), room = (WIDTH - 2 * LEFT - 80) / 12;
    length = length < room ? length : room;
    unsigned x = WIDTH - LEFT - 12 * (unsigned)length + 2;
    for (size_t i = 0; i < length; i++, x += 12)
        character(detail[i], x, HEIGHT + 13, 2, theme->label);
    flush();
}

// sets the meter from a 0 to 1 level -> only redraws when the number of bars changes
void display_level(double value) {
    unsigned bars = value <= 0 ? 0 : value >= 1 ? METER : (unsigned)(value * METER + 0.5);
    if (fb < 0 || rows == HEIGHT || bars == level)
        return;
    level = bars;
    meter();
    flush();
}
