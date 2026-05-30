// =============================================================================
//  AXIUARTPrinter.v — verilog-blackbox example DUT
//
//  Sequentially reads DATA_W-bit words from FASED memory, starting at address
//  0x0 and advancing one word per beat, and serialises each word's bytes over
//  UART 8N1. It never stops on its own — bound the run with the simulator's
//  +max-cycles to print as much of the pre-loaded payload as you need.
//
//  Pre-load FASED memory at address 0 with your payload.
//  Beyond the loaded region FASED returns its default uninitialised fill,
//  which is handy for smoke-testing that the AXI4 → FASED → UART pipeline works.
//
//  AXI4 widths that map to FASED bridge parameters are parameterised
//  (ADDR_W↔addr_bits, DATA_W↔data_bits, ID_W↔id_bits, USER_W↔user_bits). The
//  AXI4 qualifier widths (lock/cache/prot/qos/region) are fixed by the protocol
//  and hard-coded by the bridge, so they mirror those fixed widths here.
// =============================================================================
`timescale 1ns / 1ps

module AXIUARTPrinter #(
  parameter ADDR_W  = 32,
  parameter DATA_W  = 64,
  parameter ID_W    = 4,
  parameter USER_W  = 1,
  parameter CLK_HZ  = 100_000_000,
  parameter BAUD    = 115_200
)(
  input  wire             clk,
  input  wire             rst,

  // ── AXI4 write (tied off — read-only master) ────────────────────────────
  output wire [ID_W-1:0]      m_axi_awid,
  output wire [ADDR_W-1:0]    m_axi_awaddr,
  output wire [7:0]           m_axi_awlen,
  output wire [2:0]           m_axi_awsize,
  output wire [1:0]           m_axi_awburst,
  output wire                 m_axi_awlock,
  output wire [3:0]           m_axi_awcache,
  output wire [2:0]           m_axi_awprot,
  output wire [3:0]           m_axi_awqos,
  output wire [USER_W-1:0]    m_axi_awuser,
  output wire [3:0]           m_axi_awregion,
  output wire                 m_axi_awvalid,
  input  wire                 m_axi_awready,
  output wire [DATA_W-1:0]    m_axi_wdata,
  output wire [DATA_W/8-1:0]  m_axi_wstrb,
  output wire                 m_axi_wlast,
  output wire [USER_W-1:0]    m_axi_wuser,
  output wire                 m_axi_wvalid,
  input  wire                 m_axi_wready,
  input  wire [ID_W-1:0]      m_axi_bid,
  input  wire [1:0]           m_axi_bresp,
  input  wire [USER_W-1:0]    m_axi_buser,
  input  wire                 m_axi_bvalid,
  output wire                 m_axi_bready,

  // ── AXI4 read ────────────────────────────────────────────────────────────
  output reg  [ID_W-1:0]      m_axi_arid,
  output reg  [ADDR_W-1:0]    m_axi_araddr,
  output reg  [7:0]           m_axi_arlen,
  output reg  [2:0]           m_axi_arsize,
  output reg  [1:0]           m_axi_arburst,
  output wire                 m_axi_arlock,
  output wire [3:0]           m_axi_arcache,
  output wire [2:0]           m_axi_arprot,
  output wire [3:0]           m_axi_arqos,
  output wire [USER_W-1:0]    m_axi_aruser,
  output wire [3:0]           m_axi_arregion,
  output reg                  m_axi_arvalid,
  input  wire                 m_axi_arready,
  input  wire [ID_W-1:0]      m_axi_rid,
  input  wire [DATA_W-1:0]    m_axi_rdata,
  input  wire [1:0]           m_axi_rresp,
  input  wire                 m_axi_rlast,
  input  wire [USER_W-1:0]    m_axi_ruser,
  input  wire                 m_axi_rvalid,
  output reg                  m_axi_rready,

  // ── UART ─────────────────────────────────────────────────────────────────
  output wire                 uart_txd,
  input  wire                 uart_rxd
);

  localparam integer NBYTES     = DATA_W / 8;            // bytes per beat
  localparam integer BYTE_IDX_W = $clog2(NBYTES + 1);    // holds 0 .. NBYTES
  localparam [2:0]   AR_SIZE    = 3'($clog2(NBYTES));    // AXI4 size = log2(bytes)

  // ── Write channels — permanently tied off ────────────────────────────────
  assign m_axi_awid     = {ID_W{1'b0}};
  assign m_axi_awaddr   = {ADDR_W{1'b0}};
  assign m_axi_awlen    = 8'd0;
  assign m_axi_awsize   = 3'd0;
  assign m_axi_awburst  = 2'd1;
  assign m_axi_awlock   = 1'b0;
  assign m_axi_awcache  = 4'd0;
  assign m_axi_awprot   = 3'd0;
  assign m_axi_awqos    = 4'd0;
  assign m_axi_awuser   = {USER_W{1'b0}};
  assign m_axi_awregion = 4'd0;
  assign m_axi_awvalid  = 1'b0;
  assign m_axi_wdata    = {DATA_W{1'b0}};
  assign m_axi_wstrb    = {(DATA_W/8){1'b0}};
  assign m_axi_wlast    = 1'b0;
  assign m_axi_wuser    = {USER_W{1'b0}};
  assign m_axi_wvalid   = 1'b0;
  assign m_axi_bready   = 1'b1;

  // ── Read address qualifiers — constant for a simple sequential reader ─────
  assign m_axi_arlock   = 1'b0;
  assign m_axi_arcache  = 4'd0;
  assign m_axi_arprot   = 3'd0;
  assign m_axi_arqos    = 4'd0;
  assign m_axi_aruser   = {USER_W{1'b0}};
  assign m_axi_arregion = 4'd0;

  // ── Main state machine ────────────────────────────────────────────────────
  localparam S_IDLE    = 3'd0;
  localparam S_AR      = 3'd1;
  localparam S_R       = 3'd2;
  localparam S_TX_LOAD = 3'd3;
  localparam S_TX_WAIT = 3'd4;

  reg [2:0]            state;
  reg [DATA_W-1:0]     data_buf;
  reg [BYTE_IDX_W-1:0] byte_idx;

  reg              tx_start;
  reg  [7:0]       tx_byte;
  wire             tx_done;

  always @(posedge clk or posedge rst) begin
    if (rst) begin
      state         <= S_IDLE;
      m_axi_arvalid <= 1'b0;
      m_axi_arid    <= {ID_W{1'b0}};
      m_axi_araddr  <= {ADDR_W{1'b0}};
      m_axi_arlen   <= 8'd0;
      m_axi_arsize  <= AR_SIZE;       // one DATA_W-wide beat per read
      m_axi_arburst <= 2'd1;          // INCR
      m_axi_rready  <= 1'b0;
      data_buf      <= {DATA_W{1'b0}};
      byte_idx      <= {BYTE_IDX_W{1'b0}};
      tx_start      <= 1'b0;
      tx_byte       <= 8'd0;
    end else begin
      tx_start <= 1'b0;

      case (state)
        S_IDLE: begin
          m_axi_arvalid <= 1'b1;
          state         <= S_AR;
        end
        S_AR: begin
          if (m_axi_arvalid && m_axi_arready) begin
            m_axi_arvalid <= 1'b0;
            m_axi_rready  <= 1'b1;
            state         <= S_R;
          end
        end
        S_R: begin
          if (m_axi_rvalid && m_axi_rready) begin
            data_buf      <= m_axi_rdata;
            m_axi_rready  <= 1'b0;
            m_axi_araddr  <= m_axi_araddr + NBYTES;  // advance to next word
            byte_idx      <= {BYTE_IDX_W{1'b0}};
            state         <= S_TX_LOAD;
          end
        end
        S_TX_LOAD: begin
          if (byte_idx < BYTE_IDX_W'(NBYTES)) begin
            tx_byte  <= data_buf[byte_idx*8 +: 8];
            tx_start <= 1'b1;
            state    <= S_TX_WAIT;
          end else begin
            // Whole word sent — fetch the next one. The reader never stops on
            // its own; bound the run with the simulator's +max-cycles.
            state <= S_IDLE;
          end
        end
        S_TX_WAIT: begin
          if (tx_done) begin
            byte_idx <= byte_idx + 1'b1;
            state    <= S_TX_LOAD;
          end
        end
        default: state <= S_IDLE;
      endcase
    end
  end

  // ── UART 8N1 TX ──────────────────────────────────────────────────────────
  uart_tx_8n1 #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
    .clk     (clk),
    .rst     (rst),
    .tx_byte (tx_byte),
    .start   (tx_start),
    .done    (tx_done),
    .tx      (uart_txd)
  );

endmodule


// =============================================================================
//  uart_tx_8n1 — minimal 8N1 UART transmitter
// =============================================================================
module uart_tx_8n1 #(
  parameter CLK_HZ = 100_000_000,
  parameter BAUD   = 115_200
)(
  input  wire       clk,
  input  wire       rst,
  input  wire [7:0] tx_byte,
  input  wire       start,
  output reg        done,
  output reg        tx
);
  localparam integer CLKS = CLK_HZ / BAUD;

  localparam ST_IDLE  = 2'd0;
  localparam ST_START = 2'd1;
  localparam ST_DATA  = 2'd2;
  localparam ST_STOP  = 2'd3;

  reg [1:0]  st;
  reg [31:0] cnt;
  reg [2:0]  bitn;
  reg [7:0]  sh;

  always @(posedge clk or posedge rst) begin
    if (rst) begin
      st <= ST_IDLE; tx <= 1'b1; done <= 1'b0;
      cnt <= 0; bitn <= 0; sh <= 0;
    end else begin
      done <= 1'b0;
      case (st)
        ST_IDLE:  begin tx <= 1'b1; if (start) begin sh <= tx_byte; cnt <= 0; st <= ST_START; end end
        ST_START: begin tx <= 1'b0; if (cnt == CLKS-1) begin cnt <= 0; bitn <= 0; st <= ST_DATA; end else cnt <= cnt+1; end
        ST_DATA:  begin tx <= sh[bitn]; if (cnt == CLKS-1) begin cnt <= 0; if (bitn==7) st <= ST_STOP; else bitn <= bitn+1; end else cnt <= cnt+1; end
        ST_STOP:  begin tx <= 1'b1; if (cnt == CLKS-1) begin done <= 1'b1; cnt <= 0; st <= ST_IDLE; end else cnt <= cnt+1; end
      endcase
    end
  end
endmodule
