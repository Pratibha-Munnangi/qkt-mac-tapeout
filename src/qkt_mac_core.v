// qkt_mac_core.v
// Stateless command-decode 1x4 INT4 signed dot-product MAC.
// 3-stage pipeline: multiply -> adder tree -> accumulate.
// INT32 accumulator for wide-range accumulation. No FSM.

`default_nettype none

module qkt_mac_core (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  data_in,      // only [3:0] used (INT4 nibble)
    input  wire [2:0]  cmd,
    input  wire        valid_in,
    output reg  [7:0]  data_out,
    output reg         ready_out,
    output wire        busy,
    output wire        overflow,
    output wire        done
);
    localparam CMD_LOAD_Q    = 3'd1;
    localparam CMD_LOAD_K    = 3'd2;
    localparam CMD_READ_ACC  = 3'd3;
    localparam CMD_RESET_ACC = 3'd4;

    wire is_load_q = valid_in && (cmd == CMD_LOAD_Q);
    wire is_load_k = valid_in && (cmd == CMD_LOAD_K);
    wire is_read   = valid_in && (cmd == CMD_READ_ACC);
    wire is_rst_a  = valid_in && (cmd == CMD_RESET_ACC);

    // ---- Q shift-register: 4 INT4 nibbles = 16 bits, persists across K loads ----
    reg [15:0] q_shift;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)         q_shift <= 16'd0;
        else if (is_load_q) q_shift <= {q_shift[11:0], data_in[3:0]};
    end

    // ---- K shift-register + byte counter ----
    reg [15:0] k_shift;
    reg [1:0]  k_cnt;
    reg        fire;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            k_shift <= 16'd0; k_cnt <= 2'd0; fire <= 1'b0;
        end else if (is_load_k) begin
            k_shift <= {k_shift[11:0], data_in[3:0]};
            k_cnt   <= k_cnt + 2'd1;
            fire    <= (k_cnt == 2'd3);
        end else begin
            fire <= 1'b0;
        end
    end

    // ---- Stage 1: 4 signed INT4 x INT4 multiplies -> INT8 ----
    reg signed [7:0] m0, m1, m2, m3;
    reg              s1_v;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            m0 <= 0; m1 <= 0; m2 <= 0; m3 <= 0; s1_v <= 1'b0;
        end else begin
            s1_v <= fire;
            if (fire) begin
                m0 <= $signed(q_shift[15:12]) * $signed(k_shift[15:12]);
                m1 <= $signed(q_shift[11: 8]) * $signed(k_shift[11: 8]);
                m2 <= $signed(q_shift[ 7: 4]) * $signed(k_shift[ 7: 4]);
                m3 <= $signed(q_shift[ 3: 0]) * $signed(k_shift[ 3: 0]);
            end
        end
    end

    // ---- Stage 2: adder tree, 4 x INT8 -> INT10 ----
    reg signed [9:0] sum;
    reg              s2_v;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin sum <= 0; s2_v <= 1'b0; end
        else begin
            s2_v <= s1_v;
            if (s1_v)
                sum <= $signed({{2{m0[7]}}, m0}) + $signed({{2{m1[7]}}, m1}) +
                       $signed({{2{m2[7]}}, m2}) + $signed({{2{m3[7]}}, m3});
        end
    end

    // ---- Stage 3: accumulate into INT32 ----
    reg signed [31:0] acc;
    reg               overflow_r;
    reg               s3_v;
    wire signed [32:0] acc_next = $signed({acc[31], acc}) +
                                  $signed({{23{sum[9]}}, sum});
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin acc <= 0; overflow_r <= 0; s3_v <= 0; end
        else begin
            s3_v <= s2_v;
            if (is_rst_a) begin
                acc <= 0; overflow_r <= 0;
            end else if (s2_v) begin
                acc <= acc_next[31:0];
                if (acc_next[32] != acc_next[31]) overflow_r <= 1'b1;
            end
        end
    end

    // ---- Read-out: stream 4 bytes MSB-first ----
    reg [2:0] read_cnt;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            read_cnt <= 3'd0; data_out <= 8'd0; ready_out <= 1'b0;
        end else if (is_read && read_cnt == 3'd0) begin
            data_out  <= acc[31:24]; read_cnt <= 3'd1; ready_out <= 1'b1;
        end else if (read_cnt == 3'd1) begin
            data_out <= acc[23:16]; read_cnt <= 3'd2;
        end else if (read_cnt == 3'd2) begin
            data_out <= acc[15: 8]; read_cnt <= 3'd3;
        end else if (read_cnt == 3'd3) begin
            data_out <= acc[ 7: 0]; read_cnt <= 3'd4;
        end else begin
            read_cnt <= 3'd0; ready_out <= 1'b0;
        end
    end

    assign busy     = fire | s1_v | s2_v | (read_cnt != 3'd0);
    assign overflow = overflow_r;
    assign done     = s3_v;

endmodule
`default_nettype wire
