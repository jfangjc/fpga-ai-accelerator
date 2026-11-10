#include "clock.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
    struct cap_clock c; cap_clock_init(&c,12);
    double period=1.0/(16000*(1-17e-6));
    for(unsigned i=0;i<180000;i++) {
        double sample=319.0+i*160;
        /* Poll-phase jitter, with a late consumer that is deliberately NOT
         * part of the hardware-counter observations. */
        cap_clock_add(&c,sample,12+sample*period+(i%7-3.0)*.00002);
        if(i>2000) {
            double capture=c.origin+sample*c.period;
            assert(fabs(capture-(12+sample*period))<.0001);
            double display=12+sample*period+.25+i*.000001;
            assert(fabs((display-capture)-(.25+i*.000001))<.0001);
        }
    }
    assert(c.n==120);
    assert(fabs((1/c.period/16000-1)*1e6+17)<.01);
    puts("PASS clock drift, bounded fit, preserved consumer backlog");
}
