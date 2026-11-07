package stt_engine_pkg;
    localparam int BANKS = 3;
    localparam int BANK_WORDS = 20480;
    localparam int AW = 15;
    localparam int LAT = 4;
    localparam int PARAM_LAT = 2;
    localparam int ROWS = 32;
    localparam int FRAMES = 16;
    localparam int DRAIN = 64;

    // Program record. 0 pointwise, 1 depthwise, 2 add
    typedef struct packed {
        logic [15:0] ci, co;
        logic [23:0] zero;
        logic [7:0] padding, dilation, stride, taps, dest, branch, source, relu, kind;
    } record_t;

    // A frame tile's output tile and first row address
    typedef struct packed {
        logic [4:0] otile;
        logic [AW - 1:0] base;
    } tile_tag_t;

    // Sequencer state
    typedef enum logic [3:0] {
        HEADER, SIZE, INPUT, RECORD, SETUP, PARAMS, WEIGHTS, FINISH, REPLY, WAIT, SEND
    } state_t;

    // The running layer
    typedef struct packed {
        logic dw, add;
        logic relu;
        logic in_signed;
        logic [1:0] source, branch, dest;
        logic [10:0] ci, co;
        logic [6:0] taps, padding;
        logic [1:0] stride, dilation;
        logic [9:0] t, valid;
        logic [9:0] tout, vout;
        logic [5:0] nt, nt_out;
        logic [5:0] n_otile;
        logic [6:0] kpt;
    } layer_t;
endpackage
