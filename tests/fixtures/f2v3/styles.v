module t (input wire clk, input wire rst, input wire en, input wire [7:0] d, output reg [7:0] y);
  reg [7:0] ok_ifrst, ok_ternary, ok_and, ok_enable_outer, bad_enable_inner, bad_nonzero, bad_none, ok_case, ok_notrst, partial, q1, q2;
  reg [7:0] ok_async, bad_async1;
  always @(posedge clk) begin
    if (rst) ok_ifrst <= 0; else ok_ifrst <= d;
    ok_ternary <= rst ? 8'd0 : d + 8'd1;
    ok_and <= (d ^ 8'h5a) & {8{~rst}};
    if (rst) ok_enable_outer <= 0; else if (en) ok_enable_outer <= d;
    if (en) begin if (rst) bad_enable_inner <= 0; else bad_enable_inner <= d; end
    if (rst) bad_nonzero <= 8'd5; else bad_nonzero <= d;
    bad_none <= d - 8'd3;
    case (rst) 1'b1: ok_case <= 0; default: ok_case <= d; endcase
    if (!rst) ok_notrst <= d; else ok_notrst <= 0;
    if (rst) partial[3:0] <= 0; else partial <= d;
    q1 <= d;
    q2 <= rst ? 8'd0 : q1;
  end
  always @(posedge clk or posedge rst) if (rst) ok_async <= 0; else ok_async <= d;
  always @(posedge clk or posedge rst) if (rst) bad_async1 <= 8'hff; else bad_async1 <= d;
  always @(posedge clk) begin
    if (rst) y <= 0;
    else y <= ok_ifrst ^ ok_ternary ^ ok_and ^ ok_enable_outer ^ bad_enable_inner ^ bad_nonzero ^ bad_none ^ ok_case ^ ok_notrst ^ partial ^ q2 ^ ok_async ^ bad_async1;
  end
endmodule
