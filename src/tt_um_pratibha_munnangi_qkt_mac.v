// tt_um_pratibha_munnangi_qkt_mac.v
// Tiny Tapeout wrapper: maps chip GPIO to the qkt_mac_core.
// Pin map:
//   ui_in[7:0]   = data_in (streaming signed byte)
//   uo_out[7:0]  = data_out (streaming result byte)
//   uio_in[2:0]  = cmd
//   uio_in[3]    = valid_in
//   uio_out[4]   = ready_out
//   uio_out[5]   = busy
//   uio_out[6]   = overflow
//   uio_out[7]   = done
//   uio_oe       = 8'b1111_0000 (upper 4 are outputs, lower 4 are inputs)

`default_nettype none

module tt_um_pratibha_munnangi_qkt_mac (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

    // Silence ena warning (unused but must be listed)
    wire _unused_ena = ena;

    wire [7:0] data_out_w;
    wire       ready_out_w;
    wire       busy_w, overflow_w, done_w;

    qkt_mac_core u_core (
        .clk       (clk),
        .rst_n     (rst_n),
        .data_in   (ui_in),
        .cmd       (uio_in[2:0]),
        .valid_in  (uio_in[3]),
        .data_out  (data_out_w),
        .ready_out (ready_out_w),
        .busy      (busy_w),
        .overflow  (overflow_w),
        .done      (done_w)
    );

    assign uo_out       = data_out_w;
    assign uio_out[3:0] = 4'b0000;
    assign uio_out[4]   = ready_out_w;
    assign uio_out[5]   = busy_w;
    assign uio_out[6]   = overflow_w;
    assign uio_out[7]   = done_w;

    assign uio_oe = 8'b1111_0000;

endmodule

`default_nettype wire
