#!/usr/bin/env python3

# This file is Copyright (c) 2020 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import logging
import time
import datetime

from migen import *

from litex.soc.interconnect import wishbone

# TODO:
# - replace raise with exit on logging error.
# - use common module for SoCCSR/SoCIRQ.
# - add configurable CSR paging.
# - manage IO/Linker regions.

logging.basicConfig(level=logging.INFO)

# Helpers ------------------------------------------------------------------------------------------
def colorer(s, color="bright"):
    header  = {
        "bright": "\x1b[1m",
        "green":  "\x1b[32m",
        "cyan":   "\x1b[36m",
        "red":    "\x1b[31m",
        "yellow": "\x1b[33m",
        "underline": "\x1b[4m"}[color]
    trailer = "\x1b[0m"
    return header + str(s) + trailer

def buildtime(with_time=True):
    fmt = "%Y-%m-%d %H:%M:%S" if with_time else "%Y-%m-%d"
    return datetime.datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")

# SoCRegion ----------------------------------------------------------------------------------------

class SoCRegion:
    def __init__(self, origin=None, size=None, cached=True):
        self.logger = logging.getLogger("SoCRegion")
        self.origin = origin
        self.size   = size
        self.cached = cached

    def decoder(self):
        origin = self.origin
        size   = self.size
        origin &= ~0x80000000
        size   = 2**log2_int(size, False)
        if (origin & (size - 1)) != 0:
            self.logger.error("Origin needs to be aligned on size:")
            self.logger.error(self)
            raise
        origin >>= 2 # bytes to words aligned
        size   >>= 2 # bytes to words aligned
        return lambda a: (a[log2_int(size):-1] == (origin >> log2_int(size)))

    def __str__(self):
        r = ""
        if self.origin is not None:
            r += "Origin: {}, ".format(colorer("0x{:08x}".format(self.origin)))
        if self.size is not None:
            r += "Size: {}, ".format(colorer("0x{:08x}".format(self.size)))
        r += "Cached: {}".format(colorer(self.cached))
        return r


class SoCLinkerRegion(SoCRegion):
    pass

# SoCBus -------------------------------------------------------------------------------------------

class SoCBus:
    supported_standard      = ["wishbone"]
    supported_data_width    = [32, 64]
    supported_address_width = [32]

    # Creation -------------------------------------------------------------------------------------
    def __init__(self, standard, data_width=32, address_width=32, timeout=1e6, reserved_regions={}):
        self.logger = logging.getLogger("SoCBus")
        self.logger.info(colorer("Creating new Bus Handler...", color="cyan"))

        # Check Standard
        if standard not in self.supported_standard:
            self.logger.error("Unsupported Standard: {} supporteds: {:s}".format(
                colorer(standard, color="red"),
                colorer(", ".join(self.supported_standard), color="green")))
            raise

        # Check Data Width
        if data_width not in self.supported_data_width:
            self.logger.error("Unsupported Data_Width: {} supporteds: {:s}".format(
                colorer(data_width, color="red"),
                colorer(", ".join(str(x) for x in self.supported_data_width), color="green")))
            raise

        # Check Address Width
        if address_width not in self.supported_address_width:
            self.logger.error("Unsupported Address Width: {} supporteds: {:s}".format(
                colorer(data_width, color="red"),
                colorer(", ".join(str(x) for x in self.supported_address_width), color="green")))
            raise

        # Create Bus
        self.standard      = standard
        self.data_width    = data_width
        self.address_width = address_width
        self.masters       = {}
        self.slaves        = {}
        self.regions       = {}
        self.timeout       = timeout
        self.logger.info("{}-bit {} Bus, {}GiB Address Space.".format(
            colorer(data_width), colorer(standard), colorer(2**address_width/2**30)))

        # Adding reserved regions
        self.logger.info("Adding {} Regions...".format(colorer("reserved")))
        for name, region in reserved_regions.items():
            if isinstance(region, int):
                region = SoCRegion(origin=region, size=0x1000000)
            self.add_region(name, region)

        self.logger.info(colorer("Bus Handler created.", color="cyan"))

    # Add/Allog/Check Regions ----------------------------------------------------------------------
    def add_region(self, name, region):
        allocated = False
        # Check if SoCLinkerRegion
        if isinstance(region, SoCLinkerRegion):
            self.logger.info("FIXME: SoCLinkerRegion")
        # Check if SoCRegion
        elif isinstance(region, SoCRegion):
            # If no origin specified, allocate region.
            if region.origin is None:
                allocated = True
                region    = self.alloc_region(region.size, region.cached)
                self.regions[name] = region
            # Else add region and check for overlaps.
            else:
                self.regions[name] = region
                overlap = self.check_region(self.regions)
                if overlap is not None:
                    self.logger.error("Region overlap between {} and {}:".format(
                        colorer(overlap[0], color="red"),
                        colorer(overlap[1], color="red")))
                    self.logger.error(str(self.regions[overlap[0]]))
                    self.logger.error(str(self.regions[overlap[1]]))
                    raise
            self.logger.info("{} Region {} {}.".format(
                colorer(name, color="underline"),
                colorer("allocated" if allocated else "added", color="yellow" if allocated else "green"),
                str(region)))
        else:
            self.logger.error("{} is not a supported Region".format(colorer(name, color="red")))
            raise

    def alloc_region(self, size, cached=True):
        self.logger.info("Allocating {} Region of size {}...".format(
            colorer("Cached" if cached else "IO"),
            colorer("0x{:08x}".format(size))))

        # Limit Search Regions
        uncached_regions = {}
        for _, region in self.regions.items():
            if region.cached == False:
                uncached_regions[name] = region
        if cached == False:
            search_regions = uncached_regions
        else:
            search_regions = {"main": SoCRegion(origin=0x00000000, size=2**self.address_width-1)}

        # Iterate on Search_Regions to find a Candidate
        for _, search_region in search_regions.items():
            origin = search_region.origin
            while (origin + size) < (search_region.origin + search_region.size):
                # Create a Candicate.
                candidate = SoCRegion(origin=origin, size=size, cached=cached)
                overlap   = False
                # Check Candidate does not overlap with allocated existing regions
                for _, allocated in self.regions.items():
                    if self.check_region({"0": allocated, "1": candidate}) is not None:
                        origin  = allocated.origin + allocated.size
                        overlap = True
                        break
                if not overlap:
                    # If no overlap, the Candidate is selected
                    return candidate

        self.logger.error("Not enough Address Space to allocate Region")
        raise

    def check_region(self, regions):
        i = 0
        while i < len(regions):
            n0 =  list(regions.keys())[i]
            r0 = regions[n0]
            for n1 in list(regions.keys())[i+1:]:
                r1 = regions[n1]
                if isinstance(r0, SoCLinkerRegion) or isinstance(r1, SoCLinkerRegion):
                    continue
                if r0.origin >= (r1.origin + r1.size):
                    continue
                if r1.origin >= (r0.origin + r0.size):
                    continue
                return (n0, n1)
            i += 1
        return None

    # Add Master/Slave -----------------------------------------------------------------------------
    def add_master(self, name=None, master=None, io_regions={}):
        if name is None:
            name = "master{:d}".format(len(self.masters))
        if name in self.masters.keys():
            self.logger.error("{} already declared as Bus Master:".format(colorer(name, color="red")))
            self.logger.error(self)
            raise
        self.masters[name] = master
        self.logger.info("{} {} as Bus Master.".format(colorer(name, color="underline"), colorer("added", color="green")))
        # FIXME: handle IO regions

    def add_slave(self, name=None, slave=None, region=None):
        no_name   = name is None
        no_region = region is None
        if no_name and no_region:
            self.logger.error("Please specify at least {} or {} of Bus Slave".format(
                colorer("name",   color="red"),
                colorer("region", color="red")))
            raise
        if no_name:
            name = "slave{:d}".format(len(self.slaves))
        if no_region:
            region = self.regions.get(name, None)
            if region is None:
                self.logger.error("Unable to find Region {}".format(colorer(name, color="red")))
                raise
        else:
             self.add_region(name, region)
        if name in self.slaves.keys():
            self.logger.error("{} already declared as Bus Slave:".format(colorer(name, color="red")))
            self.logger.error(self)
            raise
        self.slaves[name] = slave
        self.logger.info("{} {} as Bus Slave.".format(
            colorer(name, color="underline"),
            colorer("added", color="green")))

    # Str ------------------------------------------------------------------------------------------
    def __str__(self):
        r = "{}-bit {} Bus, {}GiB Address Space.\n".format(
            colorer(self.data_width), colorer(self.standard), colorer(2**self.address_width/2**30))
        r += "Bus Regions: ({})\n".format(len(self.regions.keys())) if len(self.regions.keys()) else ""
        for name, region in self.regions.items():
           r += colorer(name, color="underline") + " "*(20-len(name)) + ": " + str(region) + "\n"
        r += "Bus Masters: ({})\n".format(len(self.masters.keys())) if len(self.masters.keys()) else ""
        for name in self.masters.keys():
           r += "- {}\n".format(colorer(name, color="underline"))
        r += "Bus Slaves: ({})\n".format(len(self.slaves.keys())) if len(self.slaves.keys()) else ""
        for name in self.slaves.keys():
           r += "- {}\n".format(colorer(name, color="underline"))
        r = r[:-1]
        return r

# SoCCSR ----------------------------------------------------------------------------------------

class SoCCSR:
    supported_data_width    = [8, 32]
    supported_address_width = [14, 15]
    supported_alignment     = [32, 64]
    supported_paging        = [0x800]

    # Creation -------------------------------------------------------------------------------------
    def __init__(self, data_width=32, address_width=14, alignment=32, paging=0x800, reserved_csrs={}):
        self.logger = logging.getLogger("SoCCSR")
        self.logger.info(colorer("Creating new CSR Handler...", color="cyan"))

        # Check Data Width
        if data_width not in self.supported_data_width:
            self.logger.error("Unsupported data_width: {} supporteds: {:s}".format(
                colorer(data_width, color="red"),
                colorer(", ".join(str(x) for x in self.supported_data_width)), color="green"))
            raise

        # Check Address Width
        if address_width not in self.supported_address_width:
            self.logger.error("Unsupported address_width: {} supporteds: {:s}".format(
                colorer(address_width, color="red"),
                colorer(", ".join(str(x) for x in self.supported_address_width), color="green")))
            raise

        # Check Alignment
        if alignment not in self.supported_alignment:
            self.logger.error("Unsupported alignment: {} supporteds: {:s}".format(
                colorer(alignment, color="red"),
                colorer(", ".join(str(x) for x in self.supported_alignment), color="green")))
            raise

        # Check Paging
        if paging not in self.supported_paging:
            self.logger.error("Unsupported paging: {} supporteds: {:s}".format(
                colorer(paging, color="red"),
                colorer(", ".join(str(x) for x in self.supported_paging), color="green")))
            raise

        # Create CSR Handler
        self.data_width    = data_width
        self.address_width = address_width
        self.alignment     = alignment
        self.paging        = paging
        self.csrs          = {}
        self.n_csrs        = 4*2**address_width//paging # FIXME
        self.logger.info("{}-bit CSR Bus, {}KiB Address Space, {}B Paging (Up to {} Locations).\n".format(
            colorer(self.data_width),
            colorer(2**self.address_width/2**10),
            colorer(self.paging),
            self.n_csrs))

        # Adding reserved CSRs
        self.logger.info("Adding {} CSRs...".format(colorer("reserved")))
        for name, n in reserved_csrs.items():
            self.add(name, n)

        self.logger.info(colorer("CSR Bus Handler created.", color="cyan"))

    # Add ------------------------------------------------------------------------------------------
    def add(self, name, n=None, use_loc_if_exists=False):
        allocated = False
        if not (use_loc_if_exists and name in self.csrs.keys()):
            if name in self.csrs.keys():
                self.logger.error("{} CSR name already used.".format(colorer(name, "red")))
                self.logger.error(self)
                raise
            if n in self.csrs.values():
                self.logger.error("{} CSR Location already used.".format(colorer(n, "red")))
                self.logger.error(self)
                raise
            if n is None:
                allocated = True
                n = self.alloc(name)
            else:
                if n < 0:
                    self.logger.error("{} CSR Location should be positive.".format(
                        colorer(n, color="red")))
                    raise
                if n > self.n_csrs:
                    self.logger.error("{} CSR Location too high (Up to {}).".format(
                        colorer(n, color="red"),
                        colorer(self.n_csrs, color="green")))
                    raise
            self.csrs[name] = n
        else:
            n = self.csrs[name]
        self.logger.info("{} CSR {} at Location {}.".format(
            colorer(name, color="underline"),
            colorer("allocated" if allocated else "added", color="yellow" if allocated else "green"),
            colorer(n)))

    # Alloc ----------------------------------------------------------------------------------------
    def alloc(self, name):
        for n in range(self.data_width//8*2**self.address_width//self.paging):
            if n not in self.csrs.values():
                return n
        self.logger.error("Not enough CSR Locations.")
        self.logger.error(self)
        raise

    # Str ------------------------------------------------------------------------------------------
    def __str__(self):
        r = "{}-bit CSR Bus, {}KiB Address Space, {}B Paging (Up to {} Locations).\n".format(
            colorer(self.data_width),
            colorer(2**self.address_width/2**10),
            colorer(self.paging),
            self.n_csrs)
        r += "CSR Locations: ({})\n".format(len(self.csrs.keys())) if len(self.csrs.keys()) else ""
        for name in self.csrs.keys():
           r += "- {}{}: {}\n".format(colorer(name, color="underline"), " "*(20-len(name)), colorer(self.csrs[name]))
        r = r[:-1]
        return r

# SoCIRQ -------------------------------------------------------------------------------------------

class SoCIRQ:
    # Creation -------------------------------------------------------------------------------------
    def __init__(self, n_irqs=32, reserved_irqs={}):
        self.logger = logging.getLogger("SoCIRQ")
        self.logger.info(colorer("Creating new SoC IRQ Handler...", color="cyan"))

        # Check IRQ Number
        if n_irqs > 32:
            self.logger.error("Unsupported IRQs number: {} supporteds: {:s}".format(
                colorer(n, color="red"), colorer("Up to 32", color="green")))
            raise

        # Create IRQ Handler
        self.n_irqs = n_irqs
        self.irqs   = {}
        self.logger.info("IRQ Handler (up to {} Locations).".format(colorer(n_irqs)))

        # Adding reserved IRQs
        self.logger.info("Adding {} IRQs...".format(colorer("reserved")))
        for name, n in reserved_irqs.items():
            self.add(name, n)

        self.logger.info(colorer("IRQ Handler created.", color="cyan"))

    # Add ------------------------------------------------------------------------------------------
    def add(self, name, n=None):
        allocated = False
        if name in self.irqs.keys():
            self.logger.error("{} IRQ name already used.".format(colorer(name, "red")))
            self.logger.error(self)
            raise
        if n in self.irqs.values():
            self.logger.error("{} IRQ Location already used.".format(colorer(n, "red")))
            self.logger.error(self)
            raise
        if n is None:
            allocated = True
            n = self.alloc(name)
        else:
            if n < 0:
                self.logger.error("{} IRQ Location should be positive.".format(
                    colorer(n, color="red")))
                raise
            if n > self.n_irqs:
                self.logger.error("{} IRQ Location too high (Up to {}).".format(
                    colorer(n, color="red"),
                    colorer(self.n_csrs, color="green")))
                raise
        self.irqs[name] = n
        self.logger.info("{} IRQ {} at Location {}.".format(
            colorer(name, color="underline"),
            colorer("allocated" if allocated else "added", color="yellow" if allocated else "green"),
            colorer(n)))

    # Alloc ----------------------------------------------------------------------------------------
    def alloc(self, name):
        for n in range(self.n_irqs):
            if n not in self.irqs.values():
                return n
        self.logger.error("Not enough Locations.")
        self.logger.error(self)
        raise

    # Str ------------------------------------------------------------------------------------------
    def __str__(self):
        r ="IRQ Handler (up to {} Locations).\n".format(colorer(self.n_irqs))
        r += "IRQs Locations:\n" if len(self.irqs.keys()) else ""
        for name in self.irqs.keys():
           r += "- {}{}: {}\n".format(colorer(name, color="underline"), " "*(20-len(name)), colorer(self.irqs[name]))
        r = r[:-1]
        return r

# SoC ----------------------------------------------------------------------------------------------

class SoC(Module):
    def __init__(self,
        bus_standard         = "wishbone",
        bus_data_width       = 32,
        bus_address_width    = 32,
        bus_timeout          = 1e6,
        bus_reserved_regions = {},

        csr_data_width       = 32,
        csr_address_width    = 14,
        csr_alignment        = 32,
        csr_paging           = 0x800,
        csr_reserved_csrs    = {},

        irq_n_irqs           = 32,
        irq_reserved_irqs    = {},
        ):

        self.logger = logging.getLogger("SoC")
        self.logger.info(colorer("        __   _ __      _  __  ", color="bright"))
        self.logger.info(colorer("       / /  (_) /____ | |/_/  ", color="bright"))
        self.logger.info(colorer("      / /__/ / __/ -_)>  <    ", color="bright"))
        self.logger.info(colorer("     /____/_/\\__/\\__/_/|_|  ", color="bright"))
        self.logger.info(colorer("  Build your hardware, easily!", color="bright"))

        self.logger.info(colorer("-"*80, color="bright"))
        self.logger.info(colorer("Creating new SoC... ({})".format(buildtime()), color="cyan"))
        self.logger.info(colorer("-"*80, color="bright"))

        # SoC Bus Handler --------------------------------------------------------------------------
        self.bus = SoCBus(
            standard         = bus_standard,
            data_width       = bus_data_width,
            address_width    = bus_address_width,
            timeout          = bus_timeout,
            reserved_regions = bus_reserved_regions,
           )

        # SoC Bus Handler --------------------------------------------------------------------------
        self.csr = SoCCSR(
            data_width    = csr_data_width,
            address_width = csr_address_width,
            alignment     = csr_alignment,
            paging        = csr_paging,
            reserved_csrs = csr_reserved_csrs,
        )

        # SoC IRQ Handler --------------------------------------------------------------------------
        self.irq = SoCIRQ(
            n_irqs        = irq_n_irqs,
            reserved_irqs = irq_reserved_irqs
        )

        self.logger.info(colorer("-"*80, color="bright"))
        self.logger.info(colorer("Initial SoC:", color="cyan"))
        self.logger.info(colorer("-"*80, color="bright"))
        self.logger.info(self.bus)
        self.logger.info(self.csr)
        self.logger.info(self.irq)
        self.logger.info(colorer("-"*80, color="bright"))


    def do_finalize(self):
        self.logger.info(colorer("-"*80, color="bright"))
        self.logger.info(colorer("Finalized SoC:", color="cyan"))
        self.logger.info(colorer("-"*80, color="bright"))
        self.logger.info(self.bus)
        self.logger.info(self.csr)
        self.logger.info(self.irq)
        self.logger.info(colorer("-"*80, color="bright"))

        # SoC Bus Interconnect ---------------------------------------------------------------------
        bus_masters = self.bus.masters.values()
        bus_slaves  = [(self.bus.regions[n].decoder(), s) for n, s in self.bus.slaves.items()]
        if len(bus_masters) and len(bus_slaves):
            self.submodules.bus_interconnect = wishbone.InterconnectShared(
                masters        = bus_masters,
                slaves         = bus_slaves,
                register       = True,
                timeout_cycles = self.bus.timeout)

        #exit()

# Test (FIXME: move to litex/text and improve) -----------------------------------------------------

if __name__ == "__main__":
    bus = SoCBus("wishbone", reserved_regions={
        "rom": SoCRegion(origin=0x00000100, size=1024),
        "ram": SoCRegion(size=512),
        }
    )
    bus.add_master("cpu", None)
    bus.add_slave("rom", None, SoCRegion(size=1024))
    bus.add_slave("ram", None, SoCRegion(size=1024))


    csr = SoCCSR(reserved_csrs={"ctrl": 0, "uart": 1})
    csr.add("csr0")
    csr.add("csr1", 0)
    #csr.add("csr2", 46)
    csr.add("csr3", -1)
    print(bus)
    print(csr)

    irq = SoCIRQ(reserved_irqs={"uart": 1})

    soc = SoC()
