#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import math
import time
from time import localtime, strftime
import threading
from pysummit import comport
from pysummit import decoders as dec
from pysummit import descriptors as desc
from pysummit.devices import TxAPI
from pysummit.devices import RxAPI
import rfmeter
from rfmeter.agilent import E4418B
import logging
import ctypes
from pysummit import swm_dutyfactor as sdf

FLASH_MAP_MFG_DATA_START_ADDR = 0xC0000

DUMP_PDOUT = False
DUMP_TXGC_REGS = True
TIMING_INFO = False

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
                print dec.decode_error_status(status, 'transmit_packets')
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
            if (TIMING_INFO):
                print("%s - %s dBm" % (strftime("%m/%d/%Y %H:%M:%S",localtime()), meas))
            self.measurements.append(meas)
            total_runs += 1

        self.pm.cmd("INIT:CONT ON")
        pm_ready.clear()


def tx_measure(dev, power_meter, packet_count):
    sdev_thread = SummitDeviceThread(dev, packet_count)
    pm_thread = PMThread(power_meter)

    pm_thread.start()
    sdev_thread.start()
    pm_thread.join()
    return pm_thread.measurements

def main(TX, RX, iterations, test_profile, power_controller):
    # -------------------------------------------------------
    # Main program flow
    # -------------------------------------------------------

    # Read MFG data from flash
    rx_mfg_data = desc.DATAFLASH_SPEAKER_MFG_DATA_SECTION()
    status = RX[0].target.SWM_Diag_GetFlashData(
        FLASH_MAP_MFG_DATA_START_ADDR,
        ctypes.sizeof(desc.DATAFLASH_SPEAKER_MFG_DATA_SECTION),
        ctypes.byref(rx_mfg_data)
        )

    # Determine if module supports TPM (moduleID is Sherwood XD or Athena 4XD, firmware is 198.x or greater)
    # and get default (cal) power level
    modID = rx_mfg_data.speakerMfgData.moduleDescriptor.moduleID
    fwver = rx_mfg_data.speakerMfgData.moduleDescriptor.firmwareVersion
    #defpwr = rx_mfg_data.radioCalData.defaultPwr

    if ((modID == 0xFD) and ((fwver >> 5) >= 198)):
            module_supports_tpm = True
    else:
            module_supports_tpm = False
    print("\nmoduleID: 0x%X\nfirmwareVersion: %d.%d\nmodule_supports_tpm: %d" %
            (modID, fwver >> 5, fwver & 0x1F, module_supports_tpm))

    # -------------------------------------------------------
    # Set up power meter (one-time)
    # -------------------------------------------------------
    # Instantiate PM
    COM = rfmeter.comport.ComPort('/dev/ttyUSB0')
    COM.connect()
    PM = E4418B(COM)

    # Read offset file
    pm_offset_file = open('pm_offset.dat', 'r')
    pm_offset = float(pm_offset_file.read(6))
    pm_offset_file.close()

    # Get duty factor for power meter correction
    duty_factor = sdf.getSummitDutyFactor(modID, fwver)

    # Reset/initialize: clear errors, remote operation
    print ("========================================================")
    print ("Power Meter ============================================")

    PM.meter_reset()
    PM.clear_errors()
    PM.cmd("SYST:PRES")
    PM.cmd("SYST:REM")

    # Check sensor type: load duty factor and offset (unless channel-specific)
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
    PM.cmd("SENS:AVER:COUN 1")
    PM.cmd("SENS:POW:AC:RANGE 1")

    print ("========================================================")
    print (" Duty Factor = " + str(duty_factor * 100) + "%")
    print (" Correction  = " + str( round( (-10.0) * math.log10(duty_factor), 2) ) + "dB")
    print ("========================================================")
    print (" Applying Offset Data from file <pm_offset.dat>")
    print (" Offset = " + str(pm_offset) + "dB")
    print ("========================================================")
    print ("")

    # -------------------------------------------------------
    # Set up Summit device (one-time)
    # -------------------------------------------------------
    gc_addrs = [0x4089A0,
                0x4089A4,
                0x4089A8,
                0x4089AC,
                0x4089B0,
                0x4089B4,
                0x4089B8,
                0x4089BC]

    filename = 'txpo_%s.txt' % (RX[0]['mac'].replace(':','-'))

    if (TIMING_INFO):
        print("Initiating comm with the Summit module at %s" % strftime("%m/%d/%Y %H:%M:%S",localtime()))

    # For both masters and slaves...
    RX[0].wr(0x406004, 0x00) # IRQ enable reg - disable interrupts
    RX[0].wr(0x408840, 0x00) # CCA level reg - set CCA level

    if (modID in sdf.olympus_modules): # if it's a Master
        RX[0].wr(0x401004, 0x07) # Set data rate to 18Mb/s
    else: # it's a Slave
        RX[0].wr(0x401004, 0x0D) # Set data rate to 6Mb/s

    # Read and report the settings of the Summit device
    (status, CCAlevel) = RX[0].rd(0x408840)
    if(status != 0x01):
        print dec.decode_error_status(status)
    print "  CCA Level regr 408840: 0x%X" % CCAlevel

    (status, IRQenables) = RX[0].rd(0x406004)
    if(status != 0x01):
        print dec.decode_error_status(status)
    print "  IRQ Enable regr 406004: 0x%X" % IRQenables

    (status, DataRate) = RX[0].rd(0x401004)
    if(status != 0x01):
        print dec.decode_error_status(status)
    print "  DataRate regr 401004: 0x%X" % DataRate

    # Ensure enabling power compensation
    (status, null) = RX[0].set_power_comp_enable(1)

    # Disable DFS and TPM
    # NOT for slaves
#    if module_supports_tpm:
#        (status, null) = RX[0].dfs_override(5)
#        (status, null) = RX[0].set_transmit_power(defpwr)
#    else: # no TPM, just disable DFS engine
#        (status, null) = RX[0].dfs_override(1)

    with open(filename, 'w') as f:
        headings = "datetime, MAC, channel, temp, txgc, txpo"
        if (DUMP_PDOUT):
            headings = headings + ", pdout"
        if (DUMP_TXGC_REGS):
            headings = headings + ", gc_index, gc0, gc1, gc2, gc3, gc4, gc5, gc6, gc7"

        print headings
        f.write("%s\n" % headings)

        for ch in range(8,35):
            # Channel-dependent power meter setup
            # Not implemented yet...

            # Channel-dependent Summit device setup
            RX[0].set_radio_channel(0, ch)

            # Get temp, power, txgc, and pdout; report values
            # Get the temperature
            (status, temp) = RX[0].temperature()

            # Get TXGC value
            (status, gc_index) = RX[0].rd(0x40100c)
            if(status == 0x01):
                (status, txgc) = RX[0].rd(gc_addrs[gc_index])
                if(status != 0x01):
                    print dec.decode_error_status(status)
            else:
                print dec.decode_error_status(status)

            # Get values from the TX_PWR registers if applicable
            if (DUMP_TXGC_REGS):
                gc_val = []
                for reg_idx in range(8):
                    (status, val) = RX[0].rd(gc_addrs[reg_idx])
                    gc_val.append(val)

            # Transmit and take power measurements

            if (TIMING_INFO):
                print("Starting tx_measure at %s" % strftime("%m/%d/%Y %H:%M:%S",localtime()))
            data = tx_measure(dev=RX, power_meter=PM, packet_count=5000)
            if (TIMING_INFO):
                print("Finished tx_measure at %s" % strftime("%m/%d/%Y %H:%M:%S",localtime()))
            data = map(float, data)
            if(len(data) > 2):
                avg = sum(data[1:-1])/float(len(data[1:-1]))
            elif(len(data) > 1):
                avg = float(data[0])
            else:
                avg = 0

            # Get the pdout value
            if (DUMP_PDOUT):
                (status, pdout) = RX[0].get_pdout(9000, 32)
                #print "  pdout: 0x%X" % pdout

            time_now = strftime("%m/%d/%Y %H:%M:%S",localtime())

            # Let the part cool down?
            #time.sleep(5)

            outputs = (time_now, RX[0]['mac'], ch, temp, txgc, avg)
            fmt_str = "%s, %s, %d, %d, %d, %r"

            if (DUMP_PDOUT):
                outputs = outputs + (pdout,)
                fmt_str = fmt_str + ", %d"

            if (DUMP_TXGC_REGS):
                outputs = outputs + (gc_index,) + tuple(gc_val[0:8])
                fmt_str = fmt_str + ", %d, %d, %d, %d, %d, %d, %d, %d, %d"

            out_str = fmt_str % outputs
            print out_str
            f.write("%s\n" % out_str)
            f.flush()

    # Reenable power compensation
    (status, null) = RX[0].set_power_comp_enable(1)
    # -------------------------------------------------------
    # End main program flow description
    # -------------------------------------------------------

if __name__ == '__main__':
    # Set up logging to a file and the console
    logging.basicConfig(
        level=logging.INFO,
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
