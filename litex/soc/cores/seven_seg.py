#
# This file is part of LiteX.
#
# Copyright (c) 2020-2022 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2022 Wolfgang Nagele <mail@wnagele.com>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from litex.gen import *
from litex.soc.interconnect.csr import *

class SevenSegmentDisplay(LiteXModule):
    def __init__(self, sys_clk_freq, segments_pads, anodes_pads):
        # CSR register allowing the CPU to write data (32-bit for 8 hex digits)
        self.values = CSRStorage(32, description="Hex values for the 8 digits")

        # Segment map for common anode (0 = ON, 1 = OFF)
        hex_decoder = Array([
            0xc0, 0xf9, 0xa4, 0xb0, 0x99, 0x92, 0x82, 0xf8, # 0-7
            0x80, 0x90, 0x88, 0x83, 0xc6, 0xa1, 0x86, 0x8e  # 8-F
        ])

        div_counter = Signal(max=int(sys_clk_freq / 8000) + 1)
        digit_index = Signal(3) # 0 to 7 counter

        val = Signal(4)
        anode = Signal(8)

        # Internal registers for physical pins
        self.comb += [
            Case(digit_index, {
                0: [ val.eq(self.values.storage[ 0: 4]),
                     anode.eq(0b11111110), ],
                1: [ val.eq(self.values.storage[ 4: 8]),
                     anode.eq(0b11111101), ],
                2: [ val.eq(self.values.storage[ 8:12]),
                     anode.eq(0b11111011), ],
                3: [ val.eq(self.values.storage[12:16]),
                     anode.eq(0b11110111), ],
                4: [ val.eq(self.values.storage[16:20]),
                     anode.eq(0b11101111), ],
                5: [ val.eq(self.values.storage[20:24]),
                     anode.eq(0b11011111), ],
                6: [ val.eq(self.values.storage[24:28]),
                     anode.eq(0b10111111), ],
                7: [ val.eq(self.values.storage[28:32]),
                     anode.eq(0b01111111), ],
            }),
            segments_pads.eq(hex_decoder[val]),
            anodes_pads.eq(anode)
        ]

        self.sync += [
            div_counter.eq(div_counter + 1),
            If(div_counter == int(sys_clk_freq / 8000),
                div_counter.eq(0),
                digit_index.eq(digit_index + 1)
            )
        ]
