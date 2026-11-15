// Captions on the monitor
#ifndef DISPLAY_H
#define DISPLAY_H

// -1 without a framebuffer
int display_open(void);
// Finished text in white, tentative text in amber
void display_show(const char* committed, const char* tentative);

#endif
