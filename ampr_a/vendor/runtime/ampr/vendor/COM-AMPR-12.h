/******************************************************************************
//                                                                           //
//  Project: Software Interface for AMPR-12 Devices                          //
//                                                                           //
//  CGC Instruments, (c) 2010-2024, Version 1-00. All rights reserved.       //
//                                                                           //
//  Definition for COM-AMPR-12 DLL Routines                                  //
//                                                                           //
******************************************************************************/

#ifndef __COM_AMPR_12_H__
#define __COM_AMPR_12_H__

#ifdef __cplusplus
extern "C" {
#endif

/*
	The communication channel must be opened before the first usage.

	If necessary, the channel may be closed and reopened again.
	The channel is closed automatically at the end of the program, the main program does not need to take care about it.

	The return values of the routines are the following defines COM_AMPR_12_ERR_XXX
		 0: routine finished without any error (COM_AMPR_12_NOERR)
		<0: errors COM_AMPR_12_ERR_XXX

	The last error code can be obtained by calling COM_AMPR_12_State.
	The routine COM_AMPR_12_ErrorMessage returns a pointer to a zero-terminated character string or NULL in case of a failure.

	If a communication error occurred, the error code can be red by COM_AMPR_12_IO_State,
	The routine COM_AMPR_12_IO_ErrorMessage provides the corresponding error message.

	The last communication-port error code returned by the operating system can be obtained by COM_AMPR_12_GetCommError.
	The routine COM_AMPR_12_GetCommErrorMessage provides the corresponding error message.
*/

/****************
// Error codes //
****************/
#define COM_AMPR_12_NO_ERR              (   0) // No error occurred
#define COM_AMPR_12_ERR_OPEN            (  -2) // Error opening the port
#define COM_AMPR_12_ERR_CLOSE           (  -3) // Error closing the port
#define COM_AMPR_12_ERR_PURGE           (  -4) // Error purging the port
#define COM_AMPR_12_ERR_CONTROL         (  -5) // Error setting the port control lines
#define COM_AMPR_12_ERR_STATUS          (  -6) // Error reading the port status lines
#define COM_AMPR_12_ERR_COMMAND_SEND    (  -7) // Error sending command
#define COM_AMPR_12_ERR_DATA_SEND       (  -8) // Error sending data
#define COM_AMPR_12_ERR_TERM_SEND       (  -9) // Error sending termination character
#define COM_AMPR_12_ERR_COMMAND_RECEIVE ( -10) // Error receiving command
#define COM_AMPR_12_ERR_DATA_RECEIVE    ( -11) // Error receiving data
#define COM_AMPR_12_ERR_TERM_RECEIVE    ( -12) // Error receiving termination character
#define COM_AMPR_12_ERR_COMMAND_WRONG   ( -13) // Wrong command received
#define COM_AMPR_12_ERR_ARGUMENT_WRONG  ( -14) // Wrong argument received
#define COM_AMPR_12_ERR_ARGUMENT        ( -15) // Wrong argument passed to the function
#define COM_AMPR_12_ERR_RATE            ( -16) // Error setting the baud rate

#define COM_AMPR_12_ERR_NOT_CONNECTED   (-100) // Device not connected
#define COM_AMPR_12_ERR_NOT_READY       (-101) // Device not ready
#define COM_AMPR_12_ERR_READY           (-102) // Device state could not be set to not ready

WORD _export COM_AMPR_12_GetSWVersion(); // Get the COM-VOLTCONTROL12 software version


/******************
// Communication //
******************/

int _export COM_AMPR_12_Open (BYTE COMNumber); // Open the port
int _export COM_AMPR_12_Close();               // Close the port

int _export COM_AMPR_12_SetBaudRate (unsigned & BaudRate); // Set the baud rate and return the set value

int _export COM_AMPR_12_Purge();                       // Clear data buffers for the port
int _export COM_AMPR_12_DevicePurge    (bool & empty); // Clear output data buffer of the device, return value as COM_AMPR_12_Buffer_State
int _export COM_AMPR_12_GetBufferState (bool & empty); // Return true if the input data buffer of the device is empty


/************
// General //
************/

int _export COM_AMPR_12_GetFwVersion ( WORD & FwVersion);                 // Get the firmware version
#define COM_AMPR_12_DATA_STRING_SIZE  12                                      // DateString size for COM_AMPR_12_GetFwDate
int _export COM_AMPR_12_GetFwDate    ( char * DateString);            // Get the firmware date
#define COM_AMPR_12_PRODUCT_ID_SIZE   81                                      // Identification size for COM_AMPR_12_GetProductID
int _export COM_AMPR_12_GetProductID ( char * Identification);        // Get the product identification
int _export COM_AMPR_12_GetProductNo (DWORD & Number);                    // Get the product number
int _export COM_AMPR_12_GetManufDate ( WORD & Year, WORD & CalendarWeek); // Get the manufacturing date
#define COM_AMPR_12_DEVICE_TYPE (0xA3D8)                                      // Expected device type
int _export COM_AMPR_12_GetDevType   ( WORD & DevType);                   // Get the device type
int _export COM_AMPR_12_GetHwType    (DWORD & HwType);                    // Get the hardware type
int _export COM_AMPR_12_GetHwVersion ( WORD & HwVersion);                 // Get the hardware version

int _export COM_AMPR_12_GetUptime    (DWORD & Sec, WORD & Milisec, DWORD & TotalSec, WORD & TotalMilisec); // Get current and total device uptimes
int _export COM_AMPR_12_GetOptime    (DWORD & Sec, WORD & Milisec, DWORD & TotalSec, WORD & TotalMilisec); // Get current and total device operation times
int _export COM_AMPR_12_GetCPUdata   (double & Load, double & Frequency);                    // Get CPU load (0-1 = 0-100%) and frequency (Hz)

int _export COM_AMPR_12_GetHousekeeping (double & Volt12V,  double & Volt5V0,  double & Volt3V3, double & VoltAgnd, // Get the housekeeping data
/*           */                              double & Volt12Vp, double & Volt12Vn, double & VoltHVp, double & VoltHVn,
/*           */                              double & TempCPU,  double & TempADC,
/*           */                              double & TempAV,   double & TempHVp,  double & TempHVn, double & LineFreq);

int _export COM_AMPR_12_Restart(); // Restart the controller


/***********************
// AMPR-12 controller //
***********************/

// Controller status values:
#define COM_AMPR_12_ST_ON             (     0)                   // PSUs are on
#define COM_AMPR_12_ST_OVERLOAD       (     1)                   // HV PSUs overloaded
#define COM_AMPR_12_ST_STBY           (     2)                   // HV PSUs are stand-by
#define COM_AMPR_12_ST_ERROR          (0x8000)                   // General error
#define COM_AMPR_12_ST_ERR_MODULE     (COM_AMPR_12_ST_ERROR + 1) // PSU-module error
#define COM_AMPR_12_ST_ERR_VSUP       (COM_AMPR_12_ST_ERROR + 2) // Supply-voltage error
#define COM_AMPR_12_ST_ERR_TEMP_LOW   (COM_AMPR_12_ST_ERROR + 3) // Low-temperature error
#define COM_AMPR_12_ST_ERR_TEMP_HIGH  (COM_AMPR_12_ST_ERROR + 4) // Overheating error
#define COM_AMPR_12_ST_ERR_ILOCK      (COM_AMPR_12_ST_ERROR + 5) // Interlock error
#define COM_AMPR_12_ST_ERR_PSU_DIS    (COM_AMPR_12_ST_ERROR + 6) // Error due to disabled PSUs
#define COM_AMPR_12_ST_ERR_HV_PSU     (COM_AMPR_12_ST_ERROR + 7) // HV could not reach the nominal bvalue and the PSUs were turned off
int _export COM_AMPR_12_GetState  (WORD & State); // Get device state

// Controller's device state bits:
#define COM_AMPR_12_DS_PSU_ENB     (1<<0x0) // PSUs enabled
#define COM_AMPR_12_DS_VOLT_FAIL   (1<<0x8) // Supply voltages failure
#define COM_AMPR_12_DS_HV_FAIL     (1<<0x9) // High voltages failure
#define COM_AMPR_12_DS_FAN_FAIL    (1<<0xA) // Fan failure
#define COM_AMPR_12_DS_ILOCK_FAIL  (1<<0xB) // Interlock failure
#define COM_AMPR_12_DS_MODULE_FAIL (1<<0xC) // Module configuration failure
#define COM_AMPR_12_DS_RATING_FAIL (1<<0xD) // Module rating failure
#define COM_AMPR_12_DS_HV_STOP     (1<<0xE) // HV PSUs were turned off
int _export COM_AMPR_12_GetDeviceState (WORD & DeviceState); // Get device state
int _export COM_AMPR_12_EnablePSU      (bool & Enable);      // Set PSUs-enable bit in device state and return the bit value

// Controller's voltage state bits:
#define COM_AMPR_12_VS_3V3_OK   (1<<0x0) // +3V3 rail voltage OK
#define COM_AMPR_12_VS_5V0_OK   (1<<0x1) // +5V0 rail voltage OK
#define COM_AMPR_12_VS_12V_OK   (1<<0x2) // +12V rail voltage OK
#define COM_AMPR_12_VS_LINE_ON  (1<<0x3) // Line voltage OK
#define COM_AMPR_12_VS_12VP_OK  (1<<0x4) // +12Va rail voltage OK
#define COM_AMPR_12_VS_12VN_OK  (1<<0x5) // -12Va rail voltage OK
#define COM_AMPR_12_VS_HVP_OK   (1<<0x6) // Positive high voltage OK
#define COM_AMPR_12_VS_HVN_OK   (1<<0x7) // Negative high voltage OK
#define COM_AMPR_12_VS_HVP_NZ   (1<<0x8) // Positive high voltage non-zero
#define COM_AMPR_12_VS_HVN_NZ   (1<<0x9) // Negative high voltage non-zero
#define COM_AMPR_12_VS_ICL_ON   (1<<0xF) // ICL active, i.e. shorted
#define COM_AMPR_12_VS_SUPL_OK  (COM_AMPR_12_VS_3V3_OK  | COM_AMPR_12_VS_5V0_OK | COM_AMPR_12_VS_12V_OK) // Supply voltages OK
#define COM_AMPR_12_VS_ANAL_OK  (COM_AMPR_12_VS_12VP_OK | COM_AMPR_12_VS_12VN_OK)                        // Analog voltages OK
#define COM_AMPR_12_VS_HV_OK    (COM_AMPR_12_VS_HVP_OK  | COM_AMPR_12_VS_HVN_OK)                         // High voltages OK
#define COM_AMPR_12_VS_HV_NZ    (COM_AMPR_12_VS_HVP_NZ  | COM_AMPR_12_VS_HVN_NZ)                         // High voltages non-zero
#define COM_AMPR_12_VS_OK       (COM_AMPR_12_VS_SUP_OK  | COM_AMPR_12_VS_ANAL_OK)                        // All supply voltages OK
#define COM_AMPR_12_VS_ALL_OK   (COM_AMPR_12_VS_OK      | COM_AMPR_12_VS_HV_OK)                          // All voltages OK
int _export COM_AMPR_12_GetVoltageState (WORD & VoltageState); // Get voltage state

// Controller's temperature state bits:
#define COM_AMPR_12_TS_HVPPSU_HIGH  (1<<0x0) // +HV PSU overheated
#define COM_AMPR_12_TS_HVNPSU_HIGH  (1<<0x1) // -HV PSU overheated
#define COM_AMPR_12_TS_AVPSU_HIGH   (1<<0x2) //  AV PSU overheated
#define COM_AMPR_12_TS_TADC_HIGH    (1<<0x3) //     ADC overheated
#define COM_AMPR_12_TS_TCPU_HIGH    (1<<0x4) //     CPU overheated
#define COM_AMPR_12_TS_HVPPSU_LOW   (1<<0x8) // +HV PSU too cold
#define COM_AMPR_12_TS_HVNPSU_LOW   (1<<0x9) // -HV PSU too cold
#define COM_AMPR_12_TS_AVPSU_LOW    (1<<0xA) //  AV PSU too cold
#define COM_AMPR_12_TS_TADC_LOW     (1<<0xB) //     ADC too cold
#define COM_AMPR_12_TS_TCPU_LOW     (1<<0xC) //     CPU too cold
int _export COM_AMPR_12_GetTemperatureState (WORD & TemperatureState); // Get temperature state

// Controller's interlock state bits:
#define COM_AMPR_12_SI_ILOCK_FRONT_ENB   (1<<0x0) // Front interlock enable
#define COM_AMPR_12_SI_ILOCK_REAR_ENB    (1<<0x1) // Rear  interlock enable
#define COM_AMPR_12_SI_ILOCK_FRONT_INV   (1<<0x2) // Front interlock invert
#define COM_AMPR_12_SI_ILOCK_REAR_INV    (1<<0x3) // Rear  interlock invert
#define COM_AMPR_12_SI_ILOCK_FRONT       (1<<0x8) // Front interlock level
#define COM_AMPR_12_SI_ILOCK_REAR        (1<<0x9) // Rear  interlock level
#define COM_AMPR_12_SI_ILOCK_FRONT_LAST  (1<<0xA) // Last (1-ms old) front interlock level
#define COM_AMPR_12_SI_ILOCK_REAR_LAST   (1<<0xB) // Last (1-ms old) rear  interlock level
#define COM_AMPR_12_SI_ILOCK_ENB         (1<<0xF) // Interlock state
#define COM_AMPR_12_SI_ILOCK_ENB_MASK   (COM_AMPR_12_SI_ILOCK_FRONT_ENB | COM_AMPR_12_SI_ILOCK_REAR_ENB)
#define COM_AMPR_12_SI_ILOCK_FRONT_ALL  (COM_AMPR_12_SI_ILOCK_FRONT     | COM_AMPR_12_SI_ILOCK_FRONT_LAST)
#define COM_AMPR_12_SI_ILOCK_REAR_ALL   (COM_AMPR_12_SI_ILOCK_REAR      | COM_AMPR_12_SI_ILOCK_REAR_LAST)
#define COM_AMPR_12_SI_ILOCK_ALL        (COM_AMPR_12_SI_ILOCK_FRONT_ALL | COM_AMPR_12_SI_ILOCK_REAR_ALL)
int _export COM_AMPR_12_GetInterlockState (WORD & InterlockState  ); // Get interlock state
int _export COM_AMPR_12_SetInterlockState (BYTE   InterlockControl); // Set interlock control bits, i.e. only COM_AMPR_12_SI_ILOCK_xxx_ENB & COM_AMPR_12_SI_ILOCK_xxx_INV bits

int _export COM_AMPR_12_GetInputs      (bool & InterlockFront, bool & InterlockRear, bool & InputSync); // Get instantaneous device input levels
int _export COM_AMPR_12_GetSyncControl (bool & External,       bool & Invert,        bool & Level);     // Get device Sync control
int _export COM_AMPR_12_SetSyncControl (bool   External,       bool   Invert,        bool   Level);     // Set device Sync control

#define COM_AMPR_12_FAN_PWM_MAX  10000                                                                                // Maximum PWM value (100%) for COM_AMPR_12_GetFanData
int _export COM_AMPR_12_GetFanData (bool & Failed, WORD & MaxRPM, WORD & SetRPM, WORD & MeasuredRPM, WORD & PWM); // Get fan data

int _export COM_AMPR_12_GetLEDData (bool & Red, bool & Green, bool & Blue); // Get LED data
/*
int _export COM_AMPR_12_GetHVenable (bool & HVenable); // Get enable of the HV-PSUs
int _export COM_AMPR_12_SetHVenable (bool   HVenable); // Set enable of the HV-PSUs
int _export COM_AMPR_12_GetLVenable (bool & LVenable); // Get enable of the LV-PSU
int _export COM_AMPR_12_SetLVenable (bool   LVenable); // Set enable of the LV-PSU
*/

/**************************
// AMP-4D module service //
**************************/

#define COM_AMPR_12_MODULE_NUM         12  // Maximum module number
#define COM_AMPR_12_ADDR_BASE       (0x80) // Base-module address
#define COM_AMPR_12_ADDR_BROADCAST  (0xFF) // Broadcasting address

// Return values of COM_AMPR_12_GetModulePresence:
#define COM_AMPR_12_MODULE_NOT_FOUND  0                                                                                             // No module found
#define COM_AMPR_12_MODULE_PRESENT    1                                                                                             // Module with a proper type found
#define COM_AMPR_12_MODULE_INVALID    2                                                                                             // Module found but has an invalid type
#define COM_AMPR_12_PRESENCE_BASE   (COM_AMPR_12_MODULE_NUM)                                                                        // Index of the base-module in the presence flags
int _export COM_AMPR_12_GetModulePresence (bool & Valid, unsigned & MaxModule, BYTE ModulePresence [COM_AMPR_12_MODULE_NUM+1]); // Get device's maximum module number & module-presence flags
int _export COM_AMPR_12_UpdateModulePresence();                                                                                 // Update module-presence flags

int _export COM_AMPR_12_RescanModules();                  // Rescan address pins of all modules
int _export COM_AMPR_12_RescanModule  (unsigned Address); // Rescan address pins of the specified module

int _export COM_AMPR_12_RestartModule (unsigned Address); // Restart the specified module

int _export COM_AMPR_12_GetScannedModuleState  (bool & ModuleMismatch, bool & RatingFailure);                       // Get the state of the module scan
int _export COM_AMPR_12_SetScannedModuleState();                                                                    // Reset the module mismatch, i.e save the current device configuration
int _export COM_AMPR_12_GetScannedModuleParams (unsigned Address, DWORD & ScannedProductNo, DWORD & SavedProductNo, // Get scanned & saved product number & hardware type of a module
/*           */                                                       DWORD & ScannedHwType,    DWORD & SavedHwType);

int _export COM_AMPR_12_GetModuleFwVersion (unsigned Address,   WORD & FwVersion);                 // Get the module firmware version
int _export COM_AMPR_12_GetModuleFwDate    (unsigned Address,   char * DateString);            // Get the module firmware date
int _export COM_AMPR_12_GetModuleProductID (unsigned Address,   char * Identification);        // Get the module product identification
int _export COM_AMPR_12_GetModuleProductNo (unsigned Address,  DWORD & ProductNo);                 // Get the module product number
int _export COM_AMPR_12_GetModuleManufDate (unsigned Address,   WORD & Year, WORD & CalendarWeek); // Get the module manufacturing date
#define COM_AMPR_12_MODULE_TYPE (0x07E6)                                                               // Expected module device type
int _export COM_AMPR_12_GetModuleDevType   (unsigned Address,   WORD & DevType);                   // Get the module device type
int _export COM_AMPR_12_GetModuleHwType    (unsigned Address,  DWORD & HwType);                    // Get the module hardware type
int _export COM_AMPR_12_GetModuleHwVersion (unsigned Address,   WORD & HwVersion);                 // Get the module hardware version

int _export COM_AMPR_12_GetModuleUptime    (unsigned Address,  DWORD & Sec, WORD & Milisec, DWORD & TotalSec, WORD & TotalMilisec); // Get current and total uptimes
int _export COM_AMPR_12_GetModuleOptime    (unsigned Address,  DWORD & Sec, WORD & Milisec, DWORD & TotalSec, WORD & TotalMilisec); // Get current and total operation times
int _export COM_AMPR_12_GetModuleCPUdata   (unsigned Address, double & Load);                                                       // Get CPU load (0-1 = 0-100%)

int _export COM_AMPR_12_GetModuleHousekeeping (unsigned Address,  double & Volt3V3,  double & TempCPU,  double & Volt5V0,
/*           */                                    double & Volt12Vp, double & Volt12Vn, double & Volt1V8p, double & Volt1V8n); // Get the housekeeping data
int _export COM_AMPR_12_GetBaseHousekeeping                      (double & Volt3V3,  double & TempCPU);                     // Get the housekeeping data of the base module

#define COM_AMPR_12_MODULE_CHANNEL_NUM  4  // Number of module's output channels
int _export COM_AMPR_12_GetModuleOutputVoltage          (unsigned Address, unsigned Channel, double & Voltage);              // Get module's output voltage
int _export COM_AMPR_12_SetModuleOutputVoltage          (unsigned Address, unsigned Channel, double   Voltage);              // Set module's output voltage
int _export COM_AMPR_12_GetMeasuredModuleOutputVoltages (unsigned Address, double Voltage [COM_AMPR_12_MODULE_CHANNEL_NUM]); // Get measured module's output voltages

// Module state bits:
#define COM_AMPR_12_MS_OUT1_LO  (1<<0x0) // Output #1 voltage is lower than limit
#define COM_AMPR_12_MS_OUT2_LO  (1<<0x1) // Output #2 voltage is lower than limit
#define COM_AMPR_12_MS_OUT3_LO  (1<<0x2) // Output #3 voltage is lower than limit
#define COM_AMPR_12_MS_OUT4_LO  (1<<0x3) // Output #4 voltage is lower than limit
#define COM_AMPR_12_MS_OUT1_HI  (1<<0x4) // Output #1 voltage is higher than limit
#define COM_AMPR_12_MS_OUT2_HI  (1<<0x5) // Output #2 voltage is higher than limit
#define COM_AMPR_12_MS_OUT3_HI  (1<<0x6) // Output #3 voltage is higher than limit
#define COM_AMPR_12_MS_OUT4_HI  (1<<0x7) // Output #4 voltage is higher than limit
#define COM_AMPR_12_MS_ACTIVE   (1<<0xF) // Device is active, i.e. output voltages can be nonzero
int _export COM_AMPR_12_GetModuleState (unsigned Address, WORD & ModuleState); // Get module state


/*****************************
// Configuration management //
*****************************/

#define COM_AMPR_12_MAX_REG (0x60 - 3)
int _export COM_AMPR_12_GetCurrentConfig (      DWORD Config [COM_AMPR_12_MAX_REG]); // Get current configuration
int _export COM_AMPR_12_SetCurrentConfig (const DWORD Config [COM_AMPR_12_MAX_REG]); // Set current configuration

#define COM_AMPR_12_MAX_CONFIG  500
int _export COM_AMPR_12_GetConfigList (bool Active [COM_AMPR_12_MAX_CONFIG], bool Valid [COM_AMPR_12_MAX_CONFIG]); // Get configuration list
int _export COM_AMPR_12_SaveCurrentConfig (WORD ConfigNumber);                                                     // Save current configuration to NVM
int _export COM_AMPR_12_LoadCurrentConfig (WORD ConfigNumber);                                                     // Load current configuration from NVM

#define COM_AMPR_12_CONFIG_NAME_SIZE  0x89                                                                      // Allowed size of the configuration name
int _export COM_AMPR_12_GetConfigName  (WORD ConfigNumber,       char Name [COM_AMPR_12_CONFIG_NAME_SIZE]); // Get configuration name
int _export COM_AMPR_12_SetConfigName  (WORD ConfigNumber, const char Name [COM_AMPR_12_CONFIG_NAME_SIZE]); // Set configuration name

int _export COM_AMPR_12_GetConfigData  (WORD ConfigNumber,       DWORD Config [COM_AMPR_12_MAX_REG]); // Get configuration data
int _export COM_AMPR_12_SetConfigData  (WORD ConfigNumber, const DWORD Config [COM_AMPR_12_MAX_REG]); // Set configuration data

int _export COM_AMPR_12_GetConfigFlags (WORD ConfigNumber, bool & Active, bool & Valid); // Get configuration flags
int _export COM_AMPR_12_SetConfigFlags (WORD ConfigNumber, bool   Active, bool   Valid); // Set configuration flags


/*******************
// Error handling //
*******************/

int          _export COM_AMPR_12_GetInterfaceState();                     // Get software interface state
_export const char * COM_AMPR_12_GetErrorMessage();                       // Get the error message corresponding to the software interface state
_export const char * COM_AMPR_12_GetIOErrorMessage();                     // Get the error message corresponding to the serial port interface state
int          _export COM_AMPR_12_GetIOState          (int   & IOState);   // Get and clear last serial port interface state
_export const char * COM_AMPR_12_GetIOStateMessage   (int     IOState);   // Get the error message corresponding to the specified interface state
int          _export COM_AMPR_12_GetCommError        (DWORD & CommError); // Get and clear last communication-port error
_export const char * COM_AMPR_12_GetCommErrorMessage (DWORD   CommError); // Get the error message corresponding to the communication port error

#ifdef __cplusplus
}
#endif

#endif//__COM_AMPR_12_H__
