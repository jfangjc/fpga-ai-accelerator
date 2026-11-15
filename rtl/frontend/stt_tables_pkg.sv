// Front end ROM tables
package stt_tables_pkg;
    localparam real PI = 3.14159265358979323846;

    typedef logic signed [19:0] hann_t[320];
    typedef logic signed [23:0] twiddle_t[256];
    typedef logic [61:0] mel_t[257];

    function automatic longint round(real x);
        return longint'($floor(x + 0.5));
    endfunction

    // Hann window, Q18
    function automatic hann_t hann_table();
        for (int n = 0; n < 320; n++)
            hann_table[n] = 20'(round((0.5 - 0.5 * $cos(2 * PI * (n < 160 ? n : 319 - n) / 319)) * 2.0 ** 18));
    endfunction

    // Twiddles, Q22
    function automatic twiddle_t cos_table();
        for (int k = 0; k < 256; k++)
            cos_table[k] = 24'(round($cos(2 * PI * k / 512) * 2.0 ** 22));
    endfunction

    function automatic twiddle_t sin_table();
        for (int k = 0; k < 256; k++)
            sin_table[k] = 24'(round(-$sin(2 * PI * k / 512) * 2.0 ** 22));
    endfunction

    // Slaney mel scale, as librosa
    function automatic real mel_to_hz(real m);
        return m < 15 ? m * (200.0 / 3) : 1000 * $exp((m - 15) * $ln(6.4) / 27);
    endfunction

    // Mel filterbank, Q28
    function automatic mel_t mel_table();
        real top = 15 + $ln(8.0) / ($ln(6.4) / 27), edges[66];
        longint weight[64];
        for (int i = 0; i < 66; i++)
            edges[i] = mel_to_hz(i == 65 ? top : i * (top / 65));
        for (int k = 0; k < 257; k++) begin
            real f = k * 16000.0 / 512;
            int first = -1;
            for (int b = 0; b < 64; b++) begin
                real rising = (f - edges[b]) / (edges[b + 1] - edges[b]);
                real falling = (edges[b + 2] - f) / (edges[b + 2] - edges[b + 1]);
                real w = rising < falling ? rising : falling;
                weight[b] = round((w > 0 ? w : 0) * (2 / (edges[b + 2] - edges[b])) * 2.0 ** 28);
                if (weight[b] != 0 && first < 0) begin
                    first = b;
                end
            end
            if (first < 0) begin
                first = 0;
            end
            mel_table[k] = {6'(first), 28'(first < 63 ? weight[first + 1] : 0), 28'(weight[first])};
        end
    endfunction
endpackage
