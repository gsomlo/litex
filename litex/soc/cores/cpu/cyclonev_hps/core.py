#
# This file is part of LiteX.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *

from litex.gen import *

from litex.soc.interconnect import axi

from litex.soc.cores.cpu import CPU

# Constants ----------------------------------------------------------------------------------------

# H2F/F2H Bridges Port Size Config.
PORT_SIZE_CONFIG = {
    32   : 0b00,
    64   : 0b01,
    128  : 0b10,
    None : 0b11, # Unused.
}

# Cyclone V HPS ------------------------------------------------------------------------------------

class CycloneVHPS(CPU):
    variants                 = ["standard"]
    category                 = "hardcore"
    family                   = "arm"
    name                     = "cyclonev_hps"
    human_name               = "Cyclone V HPS (Dual Cortex-A9)"
    data_width               = 32
    endianness               = "little"
    reset_address            = 0x0100_0000
    reset_address_check      = False # HPS boots from its own BootROM/SD-Card; reset_address is only
                                     # used to link the (optional) BIOS in HPS DDR3.
    gcc_triple               = "arm-none-eabi"
    gcc_flags                = "-mcpu=cortex-a9 -mfpu=vfpv3 -mfloat-abi=hard"
    linker_output_format     = "elf32-littlearm"
    nop                      = "nop"
    io_regions               = {0xff20_0000: 0x0020_0000} # Origin, Length: H2F LW bridge window.
    csr_decode               = True # AXI address is decoded in AXI2Wishbone, offset needs to be added in Software.
    integrated_rom_supported = False

    # Memory Mapping (== ARM physical addresses).
    @property
    def mem_map(self):
        return {
            "sram"     : 0x0010_0000, # HPS DDR3 in fact (skips first 1MB: ARM vectors/Preloader).
            "rom"      : 0x0100_0000, # HPS DDR3 in fact (Linker-only region, added by target).
            "main_ram" : 0xc000_0000, # Fabric RAM seen by the ARM through the H2F bridge window.
            "csr"      : 0xff20_0000, # H2F LW bridge window.
        }

    def __init__(self, platform, variant, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.platform      = platform
        self.reset         = Signal() # Not used (HPS is not reset from the fabric).
        self.interrupt     = Signal(32) # F2H IRQs 0-31 (GIC IDs 72-103 / Linux GIC_SPI 40-71).
        self.periph_buses  = []       # Peripheral buses (Connected to main SoC's bus).
        self.memory_buses  = []       # Memory buses (Connected directly to LiteDRAM).

        self.h2f_lw_master = None     # H2F LW AXI3 Master (HPS -> Fabric, CSRs access).
        self.h2f_master    = None     # H2F    AXI3 Master (HPS -> Fabric).
        self.f2h_slave     = None     # F2H    AXI3 Slave  (Fabric -> HPS DDR3/Peripherals).

        self.h2f_rst_n     = Signal() # HPS -> Fabric reset.

        # # #

        # H2F LW as Bus Master ---------------------------------------------------------------------
        self.pbus = self.add_axi_h2f_lw_master()
        self.periph_buses.append(self.pbus)

    # H2F LW AXI3 Master (HPS -> Fabric) -----------------------------------------------------------
    def add_axi_h2f_lw_master(self, clock_domain="sys"):
        assert self.h2f_lw_master is None
        base       = self.mem_map["csr"]
        axi_h2f_lw = axi.AXIInterface(
            data_width    = 32,
            address_width = 32,
            id_width      = 12,
            version       = "axi3",
            clock_domain  = clock_domain,
        )
        self.h2f_lw_master = axi_h2f_lw

        # Extend the bridge's window-relative addresses to ARM physical addresses, so that the
        # LiteX memory map matches the ARM's view (CSRs seen at mem_map["csr"] on both sides).
        awaddr = Signal(21)
        araddr = Signal(21)
        self.comb += [
            axi_h2f_lw.aw.addr.eq(Cat(awaddr, Constant(base >> 21, 11))),
            axi_h2f_lw.ar.addr.eq(Cat(araddr, Constant(base >> 21, 11))),
        ]

        self.specials += Instance("cyclonev_hps_interface_hps2fpga_light_weight",
            # Clk.
            i_clk     = ClockSignal(clock_domain),

            # AW.
            o_awid    = axi_h2f_lw.aw.id,
            o_awaddr  = awaddr,
            o_awlen   = axi_h2f_lw.aw.len,
            o_awsize  = axi_h2f_lw.aw.size,
            o_awburst = axi_h2f_lw.aw.burst,
            o_awlock  = axi_h2f_lw.aw.lock,
            o_awcache = axi_h2f_lw.aw.cache,
            o_awprot  = axi_h2f_lw.aw.prot,
            o_awvalid = axi_h2f_lw.aw.valid,
            i_awready = axi_h2f_lw.aw.ready,

            # W.
            o_wid     = axi_h2f_lw.w.id,
            o_wdata   = axi_h2f_lw.w.data,
            o_wstrb   = axi_h2f_lw.w.strb,
            o_wlast   = axi_h2f_lw.w.last,
            o_wvalid  = axi_h2f_lw.w.valid,
            i_wready  = axi_h2f_lw.w.ready,

            # B.
            i_bid     = axi_h2f_lw.b.id,
            i_bresp   = axi_h2f_lw.b.resp,
            i_bvalid  = axi_h2f_lw.b.valid,
            o_bready  = axi_h2f_lw.b.ready,

            # AR.
            o_arid    = axi_h2f_lw.ar.id,
            o_araddr  = araddr,
            o_arlen   = axi_h2f_lw.ar.len,
            o_arsize  = axi_h2f_lw.ar.size,
            o_arburst = axi_h2f_lw.ar.burst,
            o_arlock  = axi_h2f_lw.ar.lock,
            o_arcache = axi_h2f_lw.ar.cache,
            o_arprot  = axi_h2f_lw.ar.prot,
            o_arvalid = axi_h2f_lw.ar.valid,
            i_arready = axi_h2f_lw.ar.ready,

            # R.
            i_rid     = axi_h2f_lw.r.id,
            i_rdata   = axi_h2f_lw.r.data,
            i_rresp   = axi_h2f_lw.r.resp,
            i_rlast   = axi_h2f_lw.r.last,
            i_rvalid  = axi_h2f_lw.r.valid,
            o_rready  = axi_h2f_lw.r.ready,
        )
        return axi_h2f_lw

    # H2F AXI3 Master (HPS -> Fabric) --------------------------------------------------------------
    def add_axi_h2f_master(self, data_width=32, clock_domain="sys"):
        assert self.h2f_master is None
        assert data_width in [32, 64, 128]
        base    = self.mem_map["main_ram"]
        axi_h2f = axi.AXIInterface(
            data_width    = data_width,
            address_width = 32,
            id_width      = 12,
            version       = "axi3",
            clock_domain  = clock_domain,
        )
        self.h2f_master = axi_h2f

        # Extend the bridge's window-relative addresses to ARM physical addresses (window at
        # 0xc000_0000, 960MB); bridge data/strb ports are 128-bit, LSB-aligned for 32/64-bit.
        awaddr = Signal(30)
        araddr = Signal(30)
        wdata  = Signal(128)
        wstrb  = Signal(16)
        rdata  = Signal(128)
        self.comb += [
            axi_h2f.aw.addr.eq(Cat(awaddr, Constant(base >> 30, 2))),
            axi_h2f.ar.addr.eq(Cat(araddr, Constant(base >> 30, 2))),
            axi_h2f.w.data.eq(wdata),
            axi_h2f.w.strb.eq(wstrb),
            rdata.eq(axi_h2f.r.data),
        ]

        self.specials += Instance("cyclonev_hps_interface_hps2fpga",
            # Config/Clk.
            i_port_size_config = PORT_SIZE_CONFIG[data_width],
            i_clk              = ClockSignal(clock_domain),

            # AW.
            o_awid    = axi_h2f.aw.id,
            o_awaddr  = awaddr,
            o_awlen   = axi_h2f.aw.len,
            o_awsize  = axi_h2f.aw.size,
            o_awburst = axi_h2f.aw.burst,
            o_awlock  = axi_h2f.aw.lock,
            o_awcache = axi_h2f.aw.cache,
            o_awprot  = axi_h2f.aw.prot,
            o_awvalid = axi_h2f.aw.valid,
            i_awready = axi_h2f.aw.ready,

            # W.
            o_wid     = axi_h2f.w.id,
            o_wdata   = wdata,
            o_wstrb   = wstrb,
            o_wlast   = axi_h2f.w.last,
            o_wvalid  = axi_h2f.w.valid,
            i_wready  = axi_h2f.w.ready,

            # B.
            i_bid     = axi_h2f.b.id,
            i_bresp   = axi_h2f.b.resp,
            i_bvalid  = axi_h2f.b.valid,
            o_bready  = axi_h2f.b.ready,

            # AR.
            o_arid    = axi_h2f.ar.id,
            o_araddr  = araddr,
            o_arlen   = axi_h2f.ar.len,
            o_arsize  = axi_h2f.ar.size,
            o_arburst = axi_h2f.ar.burst,
            o_arlock  = axi_h2f.ar.lock,
            o_arcache = axi_h2f.ar.cache,
            o_arprot  = axi_h2f.ar.prot,
            o_arvalid = axi_h2f.ar.valid,
            i_arready = axi_h2f.ar.ready,

            # R.
            i_rid     = axi_h2f.r.id,
            i_rdata   = rdata,
            i_rresp   = axi_h2f.r.resp,
            i_rlast   = axi_h2f.r.last,
            i_rvalid  = axi_h2f.r.valid,
            o_rready  = axi_h2f.r.ready,
        )
        return axi_h2f

    # F2H AXI3 Slave (Fabric -> HPS) ---------------------------------------------------------------
    def add_axi_f2h_slave(self, data_width=32, clock_domain="sys"):
        # Gives Fabric masters access to the full HPS map: addresses < 0x8000_0000 reach HPS DDR3
        # non-coherently (ARM caches must be flushed/bypassed), ACP window at 0x8000_0000+ gives
        # L2-coherent access. Returned interface is not connected to the SoC's shared bus (a H2F
        # -> F2H loopback would deadlock); connect user DMAs directly to it.
        assert self.f2h_slave is None
        assert data_width in [32, 64, 128]
        axi_f2h = axi.AXIInterface(
            data_width    = data_width,
            address_width = 32,
            id_width      = 8,
            version       = "axi3",
            clock_domain  = clock_domain,
        )
        self.f2h_slave = axi_f2h

        # Bridge data/strb ports are 128-bit, LSB-aligned for 32/64-bit.
        wdata = Signal(128)
        wstrb = Signal(16)
        rdata = Signal(128)
        self.comb += [
            wdata.eq(axi_f2h.w.data),
            wstrb.eq(axi_f2h.w.strb),
            axi_f2h.r.data.eq(rdata),
        ]

        self.specials += Instance("cyclonev_hps_interface_fpga2hps",
            # Config/Clk.
            i_port_size_config = PORT_SIZE_CONFIG[data_width],
            i_clk              = ClockSignal(clock_domain),

            # AW.
            i_awid    = axi_f2h.aw.id,
            i_awaddr  = axi_f2h.aw.addr,
            i_awlen   = axi_f2h.aw.len,
            i_awsize  = axi_f2h.aw.size,
            i_awburst = axi_f2h.aw.burst,
            i_awlock  = axi_f2h.aw.lock,
            i_awcache = axi_f2h.aw.cache,
            i_awprot  = axi_f2h.aw.prot,
            i_awuser  = Constant(0b00001, 5), # Shareable (only relevant for ACP window).
            i_awvalid = axi_f2h.aw.valid,
            o_awready = axi_f2h.aw.ready,

            # W.
            i_wid     = axi_f2h.w.id,
            i_wdata   = wdata,
            i_wstrb   = wstrb,
            i_wlast   = axi_f2h.w.last,
            i_wvalid  = axi_f2h.w.valid,
            o_wready  = axi_f2h.w.ready,

            # B.
            o_bid     = axi_f2h.b.id,
            o_bresp   = axi_f2h.b.resp,
            o_bvalid  = axi_f2h.b.valid,
            i_bready  = axi_f2h.b.ready,

            # AR.
            i_arid    = axi_f2h.ar.id,
            i_araddr  = axi_f2h.ar.addr,
            i_arlen   = axi_f2h.ar.len,
            i_arsize  = axi_f2h.ar.size,
            i_arburst = axi_f2h.ar.burst,
            i_arlock  = axi_f2h.ar.lock,
            i_arcache = axi_f2h.ar.cache,
            i_arprot  = axi_f2h.ar.prot,
            i_aruser  = Constant(0b00001, 5), # Shareable (only relevant for ACP window).
            i_arvalid = axi_f2h.ar.valid,
            o_arready = axi_f2h.ar.ready,

            # R.
            o_rid     = axi_f2h.r.id,
            o_rdata   = rdata,
            o_rresp   = axi_f2h.r.resp,
            o_rlast   = axi_f2h.r.last,
            o_rvalid  = axi_f2h.r.valid,
            i_rready  = axi_f2h.r.ready,
        )
        return axi_f2h

    def do_finalize(self):
        # Clocks/Resets (No Fabric -> HPS reset requests).
        self.specials += Instance("cyclonev_hps_interface_clocks_resets",
            i_f2h_cold_rst_req_n  = 1,
            i_f2h_dbg_rst_req_n   = 1,
            i_f2h_warm_rst_req_n  = 1,
            i_f2h_pending_rst_ack = 1,
            o_h2f_rst_n           = self.h2f_rst_n,
        )

        # Interrupts (F2H IRQs 0-31 used, 32-63 tied to 0).
        self.specials += Instance("cyclonev_hps_interface_interrupts",
            i_irq = Cat(self.interrupt, Constant(0, 32)),
        )

        # Trace (Disabled).
        self.specials += Instance("cyclonev_hps_interface_tpiu_trace",
            i_traceclk_ctl = 1,
        )

        # Boot from FPGA (Disabled: bsel_en/csel_en=0, csel/bsel values are then ignored).
        self.specials += Instance("cyclonev_hps_interface_boot_from_fpga",
            i_boot_from_fpga_ready      = 0,
            i_boot_from_fpga_on_failure = 0,
            i_bsel_en                   = 0,
            i_csel_en                   = 0,
            i_csel                      = Constant(0b01,  2),
            i_bsel                      = Constant(0b001, 3),
        )

        # Unused H2F/F2H Bridges (explicitly configured as unused).
        if self.h2f_master is None:
            self.specials += Instance("cyclonev_hps_interface_hps2fpga",
                i_port_size_config = PORT_SIZE_CONFIG[None],
            )
        if self.f2h_slave is None:
            self.specials += Instance("cyclonev_hps_interface_fpga2hps",
                i_port_size_config = PORT_SIZE_CONFIG[None],
            )
