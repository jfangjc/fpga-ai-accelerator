// Model runners with one interface, hybrid on the PL engine and cpu on the A53s, both bit-exact with golden_int8.py
#ifndef RUNNER_H
#define RUNNER_H
#include "kv260.h"
#include <string.h>

typedef struct {
    // Load data/export/quartznet_engine.bin for hybrid or quartznet_int8.bin for cpu
    int (*load)(const char* path);
    // Features [64][frames] with frames a multiple of 16 to logits [29][out], returning out or -1 on error
    int (*run)(const int8_t* features, unsigned frames, unsigned valid, int8_t* logits, unsigned* out_valid);
} runner;

extern const runner hybrid_runner, cpu_runner;

// The runner called hybrid or cpu, NULL for any other name
static inline const runner* runner_find(const char* name) {
    return !strcmp(name, "hybrid") ? &hybrid_runner : !strcmp(name, "cpu") ? &cpu_runner : NULL;
}

#endif
