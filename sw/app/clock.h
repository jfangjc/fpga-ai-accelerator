#ifndef CAP_CLOCK_H
#define CAP_CLOCK_H
#include <math.h>
/* Fixed 120-second window. Observations name hardware packet edges, never
 * application dequeue times; consumer queueing must remain in update age. */
struct cap_clock {
    double x[120], y[120], sx, sy, origin, period;
    unsigned n, head, bucket, pending;
};
static inline void cap_clock_init(struct cap_clock *c, double origin) {
    *c=(struct cap_clock){.origin=origin,.period=1.0/16000};
}
static inline int cap_clock_add(struct cap_clock *c, double sample, double time) {
    unsigned bucket=(unsigned)(sample/16000);
    if(c->pending && bucket!=c->bucket) {
        c->x[c->head]=c->sx/c->pending;
        c->y[c->head]=c->sy/c->pending;
        c->head=(c->head+1)%120; if(c->n<120) c->n++;
        c->sx=c->sy=0; c->pending=0;
        if(c->n>=10) {
            double mx=0,my=0,xx=0,xy=0;
            for(unsigned i=0;i<c->n;i++) { mx+=c->x[i]; my+=c->y[i]; }
            mx/=c->n; my/=c->n;
            for(unsigned i=0;i<c->n;i++) {
                double dx=c->x[i]-mx; xx+=dx*dx; xy+=dx*(c->y[i]-my);
            }
            double p=xy/xx;
            if(isfinite(p) && fabs(p*16000-1)<.001) {
                c->period=p; c->origin=my-p*mx;
            }
        }
    }
    c->bucket=bucket; c->sx+=sample; c->sy+=time; c->pending++;
    return c->n>=10;
}
#endif
