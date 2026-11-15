// Engine sizes and the program record
package stt_engine_pkg;
    // Activations: one word per channel and 16-frame tile; a bank holds 1024 channels of 320 frames
    localparam int BANKS = 3;
    localparam int BANK_WORDS = 20480;
    localparam int AW = 15;
    localparam int LAT = 4;        // bank and weight read latency
    localparam int PARAM_LAT = 2;  // parameter read latency
    localparam int ROWS = 32;      // MAC array: 32 channels by 16 frames
    localparam int FRAMES = 16;
    localparam int DRAIN = 64;     // cycles for the pipelines to empty

    // Program record. kind: 0 pointwise, 1 depthwise, 2 add
    typedef struct packed {
        logic [15:0] ci, co;
        logic [23:0] zero;
        logic [7:0] padding, dilation, stride, taps, dest, branch, source, relu, kind;
    } record_t;

    // A frame tile's output tile and first row address
    typedef struct packed {
        logic [4:0] otile;
        logic [AW-1:0] base;
    } tile_tag_t;
endpackage
