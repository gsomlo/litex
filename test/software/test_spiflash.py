#
# This file is part of LiteX.
#
# Copyright (c) 2026 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import os
import subprocess
import textwrap


def _write(path, contents=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(contents))


def test_is25lp128_quad_enable_preserves_status(tmp_path):
    repo        = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    include_dir = tmp_path / "include"
    source      = tmp_path / "spiflash_harness.c"
    binary      = tmp_path / "spiflash_harness"

    _write(include_dir / "generated" / "csr.h", """
        #ifndef __GENERATED_CSR_H
        #define __GENERATED_CSR_H

        #define CSR_SPIFLASH_BASE                             1
        #define CSR_SPIFLASH_MASTER_CS_ADDR                   1
        #define CSR_SPIFLASH_MASTER_PHYCONFIG_LEN_SIZE        8
        #define CSR_SPIFLASH_MASTER_PHYCONFIG_LEN_OFFSET      0
        #define CSR_SPIFLASH_MASTER_PHYCONFIG_WIDTH_SIZE      4
        #define CSR_SPIFLASH_MASTER_PHYCONFIG_WIDTH_OFFSET    8
        #define CSR_SPIFLASH_MASTER_PHYCONFIG_MASK_SIZE       8
        #define CSR_SPIFLASH_MASTER_PHYCONFIG_MASK_OFFSET    16
        #define CSR_SPIFLASH_MASTER_STATUS_TX_READY_OFFSET    0
        #define CSR_SPIFLASH_MASTER_STATUS_RX_READY_OFFSET    1

        uint32_t test_spiflash_master_status_read(void);
        uint32_t test_spiflash_master_rxtx_read(void);
        void test_spiflash_master_rxtx_write(uint32_t value);
        void test_spiflash_master_phyconfig_write(uint32_t value);
        void test_spiflash_master_cs_write(uint32_t value);

        #define spiflash_master_status_read      test_spiflash_master_status_read
        #define spiflash_master_rxtx_read        test_spiflash_master_rxtx_read
        #define spiflash_master_rxtx_write       test_spiflash_master_rxtx_write
        #define spiflash_master_phyconfig_write  test_spiflash_master_phyconfig_write
        #define spiflash_master_cs_write         test_spiflash_master_cs_write

        #endif
    """)
    _write(include_dir / "generated" / "mem.h")
    _write(include_dir / "libbase" / "crc.h")
    _write(include_dir / "libbase" / "memtest.h")
    _write(include_dir / "system.h", """
        #ifndef __SYSTEM_H
        #define __SYSTEM_H

        #include <stdbool.h>

        #define CONFIG_CLOCK_FREQUENCY                         1000000
        #define SPIFLASH_PHY_FREQUENCY                         1000000
        #define SPIFLASH_MODULE_NAME                         "is25lp128"
        #define SPIFLASH_MODULE_QUAD_CAPABLE
        #define SPIFLASH_MODULE_QUAD_ENABLE_WRSR_SR1_BIT6

        static inline void cdelay(int cycles) { (void)cycles; }

        #endif
    """)
    _write(source, f"""
        #include <stdbool.h>
        #include <stdint.h>
        #include <stdio.h>

        static uint8_t  flash_status;
        static uint8_t  transaction_command;
        static unsigned int transaction_index;
        static uint32_t phyconfig;
        static uint32_t rx_value;
        static uint32_t wrsr_value;
        static unsigned int wrsr_bits;
        static unsigned int write_enable_count;
        static bool rx_ready;
        static bool ignore_wrsr;

        uint32_t test_spiflash_master_status_read(void)
        {{
            return 1 | (rx_ready << 1);
        }}

        uint32_t test_spiflash_master_rxtx_read(void)
        {{
            rx_ready = false;
            return rx_value;
        }}

        void test_spiflash_master_phyconfig_write(uint32_t value)
        {{
            phyconfig = value;
        }}

        void test_spiflash_master_cs_write(uint32_t value)
        {{
            if (value) {{
                transaction_command = 0;
                transaction_index   = 0;
            }} else if (transaction_command == 0x06) {{
                flash_status |= 0x02;
                write_enable_count++;
            }}
        }}

        void test_spiflash_master_rxtx_write(uint32_t value)
        {{
            unsigned int bits = phyconfig & 0xff;

            rx_value = 0;
            if (bits > 8) {{
                transaction_command = (value >> (bits - 8)) & 0xff;
                if (transaction_command == 0x01) {{
                    wrsr_value = value;
                    wrsr_bits  = bits;
                    if ((flash_status & 0x02) && !ignore_wrsr)
                        flash_status = value & 0xfc;
                    else
                        flash_status &= ~0x02;
                }}
            }} else {{
                uint8_t byte = value;

                if (transaction_index++ == 0)
                    transaction_command = byte;
                else if (transaction_command == 0x05)
                    rx_value = flash_status;
            }}
            rx_ready = true;
        }}

        #include "{repo}/litex/soc/software/liblitespi/spiflash.c"

        #define REQUIRE(cond) do {{ \\
            if (!(cond)) {{ \\
                fprintf(stderr, "requirement failed at %s:%d: %s\\n", __FILE__, __LINE__, #cond); \\
                return 1; \\
            }} \\
        }} while (0)

        int main(void)
        {{
            flash_status = 0x9c;
            REQUIRE(spiflash_enable_quad_mode());
            REQUIRE(write_enable_count == 1);
            REQUIRE(wrsr_bits == 16);
            REQUIRE(wrsr_value == 0x01dc);
            REQUIRE(flash_status == 0xdc);

            REQUIRE(spiflash_enable_quad_mode());
            REQUIRE(write_enable_count == 1);

            flash_status       = 0x04;
            wrsr_value         = 0;
            wrsr_bits          = 0;
            write_enable_count = 0;
            ignore_wrsr        = true;
            REQUIRE(!spiflash_enable_quad_mode());
            REQUIRE(write_enable_count == 1);
            REQUIRE(wrsr_bits == 16);
            REQUIRE(wrsr_value == 0x0144);
            REQUIRE(flash_status == 0x04);
            return 0;
        }}
    """)

    subprocess.check_call([
        "gcc",
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-format",
        "-I", str(include_dir),
        str(source),
        "-o", str(binary),
    ])
    subprocess.check_call([str(binary)])
