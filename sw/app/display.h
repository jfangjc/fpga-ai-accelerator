#ifndef CAP_DISPLAY_H
#define CAP_DISPLAY_H
#include <stdint.h>
#define CAP_PIXEL_BYTES (640u*240u*3u)
/* cap_render draws the caption into the 640x240 RGB buffer (cap_pixels).
 * cap_fb_open opens /dev/fb0 once (truecolour 16/32 bpp, optional);
 * cap_fb writes the pixel rows changed since its last write (all if full)
 * and returns their count, or -1 without a framebuffer. cap_ppm writes an
 * image as a PPM via a temporary file and rename. */
void cap_render(const char *committed, const char *tentative);
void cap_render_styled(const char *,const unsigned char *);
const uint8_t *cap_pixels(void);
int cap_fb_open(void);
int cap_fb(int full);
void cap_fb_close(void);
int cap_ppm(const char *ppm, const uint8_t *image);
#endif
