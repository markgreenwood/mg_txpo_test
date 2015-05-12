#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import math
import time
from time import localtime, strftime
import threading
from pysummit import comport
from pysummit import decoders as dec
from pysummit.devices import TxAPI
from pysummit.devices import RxAPI
import rfmeter
from rfmeter.agilent import E4418B
import logging

def main(TX, RX=None, tp=None, pc=None, args=[]):
    
    # Read the settings of the TX (Master) device
    TX.wr(0x406004, 0x00) # IRQ enable reg
    TX.wr(0x408840, 0x00) # CCA level reg
    TX.wr(0x401004, 0x07) # 18Mb/s

    (status, CCAlevel) = TX.rd(0x408840)
    if(status != 0x01):
        print TX.decode_error_status(status)
    print "  CCA Level regr 408840: 0x%X" % CCAlevel

    (status, IRQenables) = TX.rd(0x406004)
    if(status != 0x01):
        print TX.decode_error_status(status)
    print "  IRQ Enable regr 406004: 0x%X" % IRQenables

    (status, DataRate) = TX.rd(0x401004)
    if(status != 0x01):
        print TX.decode_error_status(status)
    print "  DataRate regr 401004: 0x%X" % DataRate

    gc_addrs = [0x4089A0,
                0x4089A4,
                0x4089A8,
                0x4089AC,
                0x4089B0,
                0x4089B4,
                0x4089B8,
                0x4089BC]

    filename = 'get_pdout_parms_%s.csv' % (TX['mac'].replace(':','-'))

    # Disable power compensation
    (status, null) = TX.set_power_comp_enable(0)

    with open(filename, 'w') as f:
        out_str = "datetime, MAC, channel, pdout, delay, nsamples"
        print out_str
        f.write("%s\n" % out_str)

        txgcval = 0x2D
        for regaddr in gc_addrs:
            TX.wr(regaddr, txgcval)

        delay = 4000 
        for nsamples in [4,8,16,32,64]:
            for ch in range(8,35):
                TX.set_radio_channel(0, ch)
    
                # Get the PD out value
                for n in range(4):  # get 4 replications
                    (status, pdout) = TX.get_pdout(delay, nsamples)

                    time_now = strftime("%m/%d/%Y %H:%M:%S",localtime())
                    out_str = "%s, %s, %d, %d, %d, %d" % \
                        (time_now, TX['mac'], ch, pdout, delay, nsamples)
                    print out_str
                    f.write("%s\n" % out_str)
                    f.flush()

    # Reenable power compensation
    (status, null) = TX.set_power_comp_enable(1)

if __name__ == '__main__':
    # Set up logging to a file and the console
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)-8s] %(message)s",
        filename="power_reading.log",
        filemode="w")
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    formatter = logging.Formatter("%(name)-8s: %(levelname)-8s %(message)s")
    console.setFormatter(formatter)
    logging.getLogger('').addHandler(console)

    Tx = TxAPI() # Instantiate a master

# Start the test
    main(Tx)
