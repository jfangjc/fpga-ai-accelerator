// captions on the monitor
#ifndef DISPLAY_H
#define DISPLAY_H
#include <linux/fb.h>
#include <stddef.h>

enum { DISPLAY_NIGHT, DISPLAY_BUTTER }; // dark background or pastel yellow to match the poster

// setup -> called once at startup and returns -1 without a framebuffer
// with status a strip along the bottom shows display_status and display_level
int display_open(int status, int theme);
// the same on an already open framebuffer for test_display
int display_attach(int fd, const struct fb_var_screeninfo* var, const struct fb_fix_screeninfo* fix, int status,
                   int theme);
// captions -> called by emit with earlier phrases as two dim lines then the live phrase large
// the first stable characters of live are settled and the rest are tentative
void display_show(const char* history, const char* live, size_t stable);
// strip -> a speaking dot and on the right the backend and timing
void display_status(int speaking, const char* detail);
// microphone level for the strip's meter from 0 to 1
void display_level(double level);

#endif
