#ifndef CAP_DISPLAY_H
#define CAP_DISPLAY_H
#include <linux/fb.h>
#include <stdint.h>
#define CAP_PIXEL_BYTES (640u*240u*3u)
#define CAP_STATUS_ROWS 40u
#define CAP_MAX_SCALE 6u
/* cap_render draws the caption into the 640x240 RGB buffer (cap_pixels).
 * cap_render_status draws one line into the 640x40 status strip shown below
 * the caption when the framebuffer was opened with status on.
 * cap_fb_open opens /dev/fb0 once (truecolour 16/32 bpp, optional). With
 * scale < 0 the caption is written 1:1 at the top left and nothing else is
 * touched (the measured layout). With scale 0 the caption, plus the status
 * strip when it fits, is drawn at the largest whole-number scale that fits
 * and centred, and the rest of the screen is cleared; scale N caps it at N.
 * cap_fb_attach does the same for an already open framebuffer (tests).
 * cap_fb writes the caption rows changed since its last write (all if full)
 * and returns their count, or -1 without a framebuffer. cap_ppm writes an
 * image as a PPM via a temporary file and rename. */
void cap_render(const char *committed, const char *tentative);
void cap_render_styled(const char *,const unsigned char *);
void cap_render_status(const char *text);
const uint8_t *cap_pixels(void);
int cap_fb_open(int scale, int status);
int cap_fb_attach(int fd, const struct fb_var_screeninfo *var,
                  const struct fb_fix_screeninfo *fix, int scale, int status);
int cap_fb(int full);
void cap_fb_close(void);
int cap_ppm(const char *ppm, const uint8_t *image);
#endif
