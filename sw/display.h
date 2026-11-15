// Captions on the monitor
#ifndef DISPLAY_H
#define DISPLAY_H
#include <linux/fb.h>

// -1 without a framebuffer. With status, a strip under the captions shows display_status
int display_open(int status);
// The same on an open framebuffer, for test_display
int display_attach(int fd, const struct fb_var_screeninfo* var, const struct fb_fix_screeninfo* fix, int status);
// Finished text in white, tentative text in amber
void display_show(const char* committed, const char* tentative);
// One line of state and timing in the status strip
void display_status(const char* text);

#endif
