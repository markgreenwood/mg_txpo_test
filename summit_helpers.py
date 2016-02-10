SHERWOOD_XD_MOD_ID = 0xFD
SHERWOOD_XC_MOD_ID = 0x0F
GLENWOOD_MOD_ID = 0x06
ATHENA_UFL_MOD_ID = 0x0D
ATHENA_4X_MOD_ID = 0x01
ATHENA_4XC_MOD_ID = 0x0C
ATHENA_4XD_MOD_ID = 0xCD

olympus_modules = (
    SHERWOOD_XD_MOD_ID,
    SHERWOOD_XC_MOD_ID,
    GLENWOOD_MOD_ID,
    )

apollo_modules = (
    ATHENA_UFL_MOD_ID,
    ATHENA_4X_MOD_ID,
    ATHENA_4XC_MOD_ID,
    ATHENA_4XD_MOD_ID,
    )

def getOlympusDutyFactor(fw_rev):
    # Duty factor for Olympus (@ 18 Mb/s) changed from 34% to 45% with FW199
    return (((fw_rev >> 5) < 199) and 0.34) or 0.45

def getApolloDutyFactor(fw_rev):
    # Duty factor for Apollo (@ 6 Mb/s) changed from 55% to 70% with FW197
    return (((fw_rev >> 5) < 197) and 0.55) or 0.70

def getSummitDutyFactor(module_id, fw_rev):
    if module_id in olympus_modules: # Master/Olympus
        return getOlympusDutyFactor(fw_rev)
    elif module_id in apollo_modules: # Slave/Apollo
        return getApolloDutyFactor(fw_rev)
    else:
        return 1.0 # if module type unknown, default to 100%

