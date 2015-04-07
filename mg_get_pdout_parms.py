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

dev_running = threading.Event()
pm_ready = threading.Event()

class SummitDeviceThread(threading.Thread):
    """A thread for transmitting packets

    Transmit a fixed number of packets. Set the dev_running event before
    starting the transmission and clear the dev_running event after the
    transmission is complete.
    """
    def __init__(self, dev, packet_count):
        super(SummitDeviceThread, self).__init__()
        self.daemon = True
        self.dev = dev
        self.packet_count = packet_count
        self.logger = logging.getLogger('SummitDeviceThread')

    def run(self):
        self.logger.info("Transmitting %d packets" % self.packet_count)

        if(pm_ready.is_set()):
            dev_running.set()
            (status, null) = self.dev.transmit_packets(self.packet_count)
            if(status != 0x01):
                print self.dev.decode_error_status(status, 'transmit_packets')
            dev_running.clear()

class PMThread(threading.Thread):
    """A power meter thread

    The power meter will take continuous measurements as long as the dev_running
    event is set.

    """
    def __init__(self, pm):
        super(PMThread, self).__init__()
        self.daemon = True
        self.pm = pm
        self.logger = logging.getLogger('PMThread')
        self.measurements = []

    def run(self):
        total_runs = 0
        self.logger.info("Taking power measurement...")
        pm_ready.set()
        while(not dev_running.is_set()):
            pass
        while(dev_running.is_set()):
            meas = self.pm.cmd("MEAS?", timeout=15)
            self.logger.info("%d: %s" % (total_runs, meas))
            self.measurements.append(meas)
            total_runs += 1

        self.pm.cmd("INIT:CONT ON")
        pm_ready.clear()


def tx_measure(dev, power_meter, packet_count):
    rx_thread = SummitDeviceThread(dev, packet_count)
    pm_thread = PMThread(power_meter)

    pm_thread.start()
    rx_thread.start()
    pm_thread.join()
    return pm_thread.measurements

def main(TX, RX, iterations, test_profile, power_controller):
    # Instantiate a Power Meter and give it an open COM port
    COM = rfmeter.comport.ComPort('/dev/ttyUSB0')
    COM.connect()
    PM = E4418B(COM)

    ### Beginning of Dave Schilling's new PM code ###

    # File operations to load in the power meter offset
    pm_offset_file = open('pm_offset.dat', 'r')
    pm_offset = float(pm_offset_file.read(6))
    pm_offset_file.close()

    # Uncomment the duty factor setting appropriate to your test
    #duty_factor = 0.23 # 36mbit
    duty_factor = 0.34 # 18mbit
    #duty_factor = 0.55 # 6mbit
    #duty_factor = 0.75 # ISOC

    # Set up Power Meter as we like it
    print ("========================================================")
    print ("Power Meter ============================================")

    print ("Resetting power meter...")
    PM.meter_reset()
    print ("Clearing errors...")
    PM.clear_errors()
    print ("Issuing SYST:PRES command...")
    PM.cmd("SYST:PRES")
    PM.cmd("SYST:REM")

    pm_sensor = PM.cmd("SERV:SENS1:TYPE?")
    print "Sensor identifies as:", pm_sensor
    #  "E4412A"=4412, "E4413A"=4413, "A"=HP8481A
    if (pm_sensor == "A"):
        PM.cmd("CORR:CSET1:SEL 'HP8481A'")
        PM.cmd("CORR:CSET1:STAT ON")
        print ("========================================================")
        print (" Using Sensor Cal Table", PM.cmd("CORR:CSET1:SEL?"))
        PM.cmd("CORR:DCYC " + str(duty_factor * 100) + "PCT")
        PM.cmd("CORR:GAIN2 " + str(pm_offset))

    elif(pm_sensor == "E4412A" or pm_sensor == "E4413A"):
        PM.cmd("CORR:DCYC " + str(duty_factor * 100) + "PCT", do_error_check=False)
        PM.clear_errors()
        PM.cmd("CORR:GAIN2 " + str(pm_offset))

    else:
        PM.cmd("CORR:DCYC " + str(duty_factor * 100) + "PCT")
        PM.cmd("CORR:GAIN2 " + str(pm_offset))

    PM.cmd("FREQ " + "5.500GHZ")

    print ("========================================================")
    print (" Duty Factor = " + str(duty_factor * 100) + "%")
    print (" Correction  = " + str( round( (-10.0) * math.log10(duty_factor), 2) ) + "dB")
    print ("========================================================")
    print (" Applying Offset Data from file <pm_offset.dat>")
    print (" Offset = " + str(pm_offset) + "dB")
    print ("========================================================")
    print ("")

    ### End of Dave Schilling's new PM code ###

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
        out_str = "datetime, MAC, channel, temp, txgc, txpo, pdout, delay, nsamples"
        print out_str
        f.write("%s\n" % out_str)

        txgcval = 0x2D
        delay = 4000 
        for nsamples in [4,8,16,32,64]:
            for ch in range(8,35):
                TX.set_radio_channel(0, ch)
    
                # Get the temperature
                (status, temp) = TX.temperature()
    
                # Set the TxGC registers with the fixed value
                for regaddr in gc_addrs:
                    TX.wr(regaddr, txgcval)
    
                # Transmit and take power measurements
                data = tx_measure(dev=TX, power_meter=PM, packet_count=5000)
                data = map(float, data)
                if(len(data) > 2):
                    avg = sum(data[1:-1])/float(len(data[1:-1]))
                elif(len(data) > 1):
                    avg = float(data[0])
                else:
                    avg = 0
    
                (status, gc_index) = TX.rd(0x40100c)
                if(status == 0x01):
                    gc_index = gc_index - 1
                    (status, gc) = TX.rd(gc_addrs[gc_index])
                    if(status != 0x01):
                        print TX.decode_error_status(status)
                else:
                    print TX.decode_error_status(status)
    
                # Get the PD out value
                for n in range(4):  # get 4 replications
                    (status, pdout) = TX.get_pdout(delay, nsamples)

                    time_now = strftime("%m/%d/%Y %H:%M:%S",localtime())
                    out_str = "%s, %s, %d, %d, %d, %r, %d, %d, %d" % \
                        (time_now, TX['mac'], ch, temp, gc, avg, pdout, delay, nsamples)
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

# Start the test
    main()
