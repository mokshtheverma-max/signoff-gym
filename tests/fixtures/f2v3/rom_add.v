module m_rom (input wire clk, input wire rst, input wire [2:0] a, input wire [3:0] b, output reg [4:0] y);
  reg [2:0] ar;
  reg [3:0] br;
  reg [3:0] r;
  always @* begin
    case (ar)
      3'd0: r = 4'd0; 3'd1: r = 4'd3; 3'd2: r = 4'd6; 3'd3: r = 4'd9;
      3'd4: r = 4'd12; 3'd5: r = 4'd15; 3'd6: r = 4'd2; 3'd7: r = 4'd5;
      default: r = 4'd0;
    endcase
  end
  always @(posedge clk) begin
    if (rst) begin ar <= 0; br <= 0; y <= 0; end
    else begin ar <= a; br <= b; y <= r + br; end
  end
endmodule
