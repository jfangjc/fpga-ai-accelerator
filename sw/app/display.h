#ifndef CAP_DISPLAY_H
#define CAP_DISPLAY_H
#include <linux/fb.h>
#include <stddef.h>
#include <stdint.h>
#define CAP_PIXEL_BYTES (640u*240u*3u)
#define CAP_STATUS_ROWS 40u
#define CAP_MAX_SCALE 6u
/* cap_theme picks the colours before anything is drawn: 0 night (dark),
 * 1 butter (pastel yellow, matching the poster). Same layout as main's
 * sw/display.c, so the CPU baseline and the FPGA version look identical.
 * cap_render_styled draws the caption into the 640x240 RGB buffer
 * (cap_pixels): text before `live` as two dim history lines, the rest large
 * on a fixed live line, styles[i] 0 settled and 1 tentative.
 * cap_render_status draws the strip shown along the bottom of the screen when
 * the framebuffer was opened with status on: a speaking dot, the level meter
 * and the detail text right-aligned. cap_render_level sets the meter (0..1)
 * and returns 1 when the strip changed.
 * cap_fb_open opens /dev/fb0 once (truecolour 16/32 bpp, optional). With
 * scale < 0 the caption is written 1:1 at the top left and nothing else is
 * touched (the measured layout). With scale 0 the caption, plus the status
 * strip when it fits on the bottom edge, is drawn at the largest whole-number
 * scale that fits, centred across, and the rest of the screen is cleared to
 * the background; scale N caps it at N.
 * cap_fb_attach does the same for an already open framebuffer (tests).
 * cap_fb writes the caption rows changed since its last write (all if full)
 * and returns their count, or -1 without a framebuffer. cap_ppm writes an
 * image as a PPM via a temporary file and rename. */
void cap_theme(int butter);
void cap_render_styled(const char *s,const unsigned char *styles,size_t live);
void cap_render_status(int speaking,const char *detail);
int cap_render_level(double level);
const uint8_t *cap_pixels(void);
int cap_fb_open(int scale, int status);
int cap_fb_attach(int fd, const struct fb_var_screeninfo *var,
                  const struct fb_fix_screeninfo *fix, int scale, int status);
int cap_fb(int full);
void cap_fb_close(void);
int cap_ppm(const char *ppm, const uint8_t *image);
#endif
