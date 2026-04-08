/******************************************************************************
//                                                                           //
//  Project: Software Interface for DMMR-8 Devices                           //
//                                                                           //
//  CGC Instruments, (c) 2010-2026, Version 1-02. All rights reserved.       //
//                                                                           //
//  Definition for COM-DMMR-8 DLL Routines                                   //
//                                                                           //
******************************************************************************/

#ifndef __COM_DMMR_8_H__
#define __COM_DMMR_8_H__

#ifdef __cplusplus
extern "C" {
#endif

/*
	The communication channel must be opened before the first usage.

	If necessary, the channel may be closed and reopened again.
	The channel is closed automatically at the end of the program, the main program does not need to take care about it.

	The return values of the routines are the following defines COM_DMMR_8_ERR_XXX
		 0: routine finished without any error (COM_DMMR_8_NOERR)
		<0: errors COM_DMMR_8_ERR_XXX

	The last error code can be obtained by calling COM_DMMR_8_State.
	The routine COM_DMMR_8_ErrorMessage returns a pointer to a zero-terminated character string or NULL in case of a failure.

	If a communication error occurred, the error code can be red by COM_DMMR_8_IO_State,
	The routine COM_DMMR_8_IO_ErrorMessage provides the corresponding error message.

	The last communication-port error code returned by the operating system can be obtained by COM_DMMR_8_GetCommError.
	The routine COM_DMMR_8_GetCommErrorMessage provides the corresponding error message.
*/

/****************
// Error codes //
****************/
#define COM_DMMR_8_NO_ERR              (   0) // No error occurred
#define COM_DMMR_8_ERR_OPEN            (  -2) // Error opening the port
#define COM_DMMR_8_ERR_CLOSE           (  -3) // Error closing the port
#define COM_DMMR_8_ERR_PURGE           (  -4) // Error purging the port
#define COM_DMMR_8_ERR_CONTROL         (  -5) // Error setting the port control lines
#define COM_DMMR_8_ERR_STATUS          (  -6) // Error reading the port status lines
#define COM_DMMR_8_ERR_COMMAND_SEND    (  -7) // Error sending command
#define COM_DMMR_8_ERR_DATA_SEND       (  -8) // Error sending data
#define COM_DMMR_8_ERR_TERM_SEND       (  -9) // Error sending termination character
#define COM_DMMR_8_ERR_COMMAND_RECEIVE ( -10) // Error receiving command
#define COM_DMMR_8_ERR_DATA_RECEIVE    ( -11) // Error receiving data
#define COM_DMMR_8_ERR_TERM_RECEIVE    ( -12) // Error receiving termination character
#define COM_DMMR_8_ERR_COMMAND_WRONG   ( -13) // Wrong command received
#define COM_DMMR_8_ERR_ARGUMENT_WRONG  ( -14) // Wrong argument received
#define COM_DMMR_8_ERR_ARGUMENT        ( -15) // Wrong argument passed to the function
#define COM_DMMR_8_ERR_RATE            ( -16) // Error setting the baud rate

#define COM_DMMR_8_ERR_NOT_CONNECTED   (-100) // Device not connected
#define COM_DMMR_8_ERR_NOT_READY       (-101) // Device not ready
#define COM_DMMR_8_ERR_READY           (-102) // Device state could not be set to not ready

#define COM_DMMR_8_ERR_BUFF_FULL       (-200) // Buffer for automatic notification data full

#define COM_DMMR_8_NO_DATA             (   1) // No new data is available
#define COM_DMMR_8_AUTO_MEAS_CUR       (   2) // Automatic current data received

WORD _export COM_DMMR_8_GetSWVersion(); // Get the COM-VOLTCONTROL12 software version


/******************
// Communication //
******************/

int _export COM_DMMR_8_Open (BYTE COMNumber); // Open communication port
int _export COM_DMMR_8_Close();               // Close communication port

int _export COM_DMMR_8_SetBaudRate (unsigned & BaudRate); // Set baud rate and return set value

int _export COM_DMMR_8_Purge();                       // Clear data buffers for the communication port
int _export COM_DMMR_8_GetBufferState (bool & Empty); // Return true if the input data buffer of the device is empty
int _export COM_DMMR_8_DevicePurge    (bool & Empty); // Clear output data buffer of the device, return value as COM_DMMR_8_Buffer_State

int _export COM_DMMR_8_GetAutoMask    (unsigned & CommandMask); // Get the mask of the last automatic notification data, can be used to check whether new data has been read during the last communication(s)
int _export COM_DMMR_8_CheckAutoInput (unsigned & CommandMask); // Check for new automatic notification data


/************
// General //
************/

int _export COM_DMMR_8_GetFwVersion ( WORD & FwVersion);                 // Get firmware version
#define COM_DMMR_8_DATA_STRING_SIZE  12                                  // DateString size for COM_DMMR_8_GetFwDate
int _export COM_DMMR_8_GetFwDate    ( char * DateString);                // Get firmware date
#define COM_DMMR_8_PRODUCT_ID_SIZE   81                                  // Identification size for COM_DMMR_8_GetProductID
int _export COM_DMMR_8_GetProductID ( char * Identification);            // Get product identification
int _export COM_DMMR_8_GetProductNo (DWORD & ProductNo);                 // Get product number
int _export COM_DMMR_8_GetManufDate ( WORD & Year, WORD & CalendarWeek); // Get manufacturing date
#define COM_DMMR_8_DEVICE_TYPE (0xAC38)                                  // Expected device type
int _export COM_DMMR_8_GetDevType   ( WORD & DevType);                   // Get device type
int _export COM_DMMR_8_GetHwType    (DWORD & HwType);                    // Get hardware type
int _export COM_DMMR_8_GetHwVersion ( WORD & HwVersion);                 // Get hardware version

int _export COM_DMMR_8_GetUptimeInt (DWORD & Seconds, WORD & Milliseconds, DWORD & TotalSeconds, WORD & TotalMilliseconds); // Get current and total device uptimes as integer values
int _export COM_DMMR_8_GetOptimeInt (DWORD & Seconds, WORD & Milliseconds, DWORD & TotalSeconds, WORD & TotalMilliseconds); // Get current and total device operation times as integer values
int _export COM_DMMR_8_GetUptime    (double & Seconds,                     double & TotalSeconds                         ); // Get current and total device uptimes
int _export COM_DMMR_8_GetOptime    (double & Seconds,                     double & TotalSeconds                         ); // Get current and total device operation times
int _export COM_DMMR_8_GetCPUdata   (double & Load, double & Frequency);                                                    // Get CPU load (0-1 = 0-100%) and frequency (Hz)

int _export COM_DMMR_8_GetHousekeeping (double & Volt12V, double & Volt5V0, double & Volt3V3, double & TempCPU); // Get housekeeping data

int _export COM_DMMR_8_Restart(); // Restart the controller


/**********************
// DMMR-8 controller //
**********************/

// Controller status values:
#define COM_DMMR_8_ST_ON             (     0)                  // Modules are on
//#define COM_DMMR_8_ST_OVERLOAD       (     1)                  // HV PSUs overloaded
//#define COM_DMMR_8_ST_STBY           (     2)                  // HV PSUs are stand-by
#define COM_DMMR_8_ST_ERROR          (0x8000)                  // General error
#define COM_DMMR_8_ST_ERR_MODULE     (COM_DMMR_8_ST_ERROR + 1) // DPA-1F-module error
#define COM_DMMR_8_ST_ERR_VSUP       (COM_DMMR_8_ST_ERROR + 2) // Supply-voltage error
//#define COM_DMMR_8_ST_ERR_TEMP_LOW   (COM_DMMR_8_ST_ERROR + 3) // Low-temperature error
//#define COM_DMMR_8_ST_ERR_TEMP_HIGH  (COM_DMMR_8_ST_ERROR + 4) // Overheating error
//#define COM_DMMR_8_ST_ERR_ILOCK      (COM_DMMR_8_ST_ERROR + 5) // Interlock error
//#define COM_DMMR_8_ST_ERR_PSU_DIS    (COM_DMMR_8_ST_ERROR + 6) // Error due to disabled PSUs
//#define COM_DMMR_8_ST_ERR_HV_PSU     (COM_DMMR_8_ST_ERROR + 7) // HV could not reach the nominal bvalue and the PSUs were turned off
int _export COM_DMMR_8_GetState (WORD & State); // Get device state

// Controller's device state bits:
#define COM_DMMR_8_DS_PSU_ENB     (1<<0x0) // PSUs enabled
#define COM_DMMR_8_DS_VOLT_FAIL   (1<<0x8) // Supply voltages failure
//#define COM_DMMR_8_DS_HV_FAIL     (1<<0x9) // High voltages failure
#define COM_DMMR_8_DS_FAN_FAIL    (1<<0xA) // Fan failure
//#define COM_DMMR_8_DS_ILOCK_FAIL  (1<<0xB) // Interlock failure
#define COM_DMMR_8_DS_MODULE_FAIL (1<<0xC) // Module configuration failure
//#define COM_DMMR_8_DS_RATING_FAIL (1<<0xD) // Module rating failure
#define COM_DMMR_8_DS_HV_STOP     (1<<0xE) // HV PSUs were turned off
int _export COM_DMMR_8_GetDeviceState (WORD & DeviceState); // Get device state
int _export COM_DMMR_8_SetEnable      (bool   Enable);      // Enable/disable modules
int _export COM_DMMR_8_GetEnable      (bool & Enable);      // Get module enable state

// Controller's voltage state bits:
#define COM_DMMR_8_VS_3V3_OK   (1<<0x0) // +3V3 rail voltage OK
#define COM_DMMR_8_VS_5V0_OK   (1<<0x1) // +5V0 rail voltage OK
#define COM_DMMR_8_VS_12V_OK   (1<<0x2) // +12V rail voltage OK
/*
#define COM_DMMR_8_VS_LINE_ON  (1<<0x3) // Line voltage OK
#define COM_DMMR_8_VS_12VP_OK  (1<<0x4) // +12Va rail voltage OK
#define COM_DMMR_8_VS_12VN_OK  (1<<0x5) // -12Va rail voltage OK
#define COM_DMMR_8_VS_HVP_OK   (1<<0x6) // Positive high voltage OK
#define COM_DMMR_8_VS_HVN_OK   (1<<0x7) // Negative high voltage OK
#define COM_DMMR_8_VS_HVP_NZ   (1<<0x8) // Positive high voltage non-zero
#define COM_DMMR_8_VS_HVN_NZ   (1<<0x9) // Negative high voltage non-zero
#define COM_DMMR_8_VS_ICL_ON   (1<<0xF) // ICL active, i.e. shorted
*/
#define COM_DMMR_8_VS_SUPL_OK  (COM_DMMR_8_VS_3V3_OK  | COM_DMMR_8_VS_5V0_OK | COM_DMMR_8_VS_12V_OK) // Supply voltages OK
/*
#define COM_DMMR_8_VS_ANAL_OK  (COM_DMMR_8_VS_12VP_OK | COM_DMMR_8_VS_12VN_OK)                       // Analog voltages OK
#define COM_DMMR_8_VS_HV_OK    (COM_DMMR_8_VS_HVP_OK  | COM_DMMR_8_VS_HVN_OK)                        // High voltages OK
#define COM_DMMR_8_VS_HV_NZ    (COM_DMMR_8_VS_HVP_NZ  | COM_DMMR_8_VS_HVN_NZ)                        // High voltages non-zero
#define COM_DMMR_8_VS_OK       (COM_DMMR_8_VS_SUP_OK  | COM_DMMR_8_VS_ANAL_OK)                       // All supply voltages OK
#define COM_DMMR_8_VS_ALL_OK   (COM_DMMR_8_VS_OK      | COM_DMMR_8_VS_HV_OK)                         // All voltages OK
*/
#define COM_DMMR_8_VS_OK       (COM_DMMR_8_VS_SUP_OK)                                                // All supply voltages OK
#define COM_DMMR_8_VS_ALL_OK   (COM_DMMR_8_VS_OK)                                                    // All voltages OK
int _export COM_DMMR_8_GetVoltageState (WORD & VoltageState); // Get voltage state

// Controller's temperature state bits:
/*
#define COM_DMMR_8_TS_HVPPSU_HIGH  (1<<0x0) // +HV PSU overheated
#define COM_DMMR_8_TS_HVNPSU_HIGH  (1<<0x1) // -HV PSU overheated
#define COM_DMMR_8_TS_AVPSU_HIGH   (1<<0x2) //  AV PSU overheated
#define COM_DMMR_8_TS_TADC_HIGH    (1<<0x3) //     ADC overheated
*/
#define COM_DMMR_8_TS_TCPU_HIGH    (1<<0x4) //     CPU overheated
/*
#define COM_DMMR_8_TS_HVPPSU_LOW   (1<<0x8) // +HV PSU too cold
#define COM_DMMR_8_TS_HVNPSU_LOW   (1<<0x9) // -HV PSU too cold
#define COM_DMMR_8_TS_AVPSU_LOW    (1<<0xA) //  AV PSU too cold
#define COM_DMMR_8_TS_TADC_LOW     (1<<0xB) //     ADC too cold
*/
#define COM_DMMR_8_TS_TCPU_LOW     (1<<0xC) //     CPU too cold
int _export COM_DMMR_8_GetTemperatureState (WORD & TemperatureState); // Get temperature state
/*
// Controller's interlock state bits:
#define COM_DMMR_8_SI_ILOCK_FRONT_ENB   (1<<0x0) // Front interlock enable
#define COM_DMMR_8_SI_ILOCK_REAR_ENB    (1<<0x1) // Rear  interlock enable
#define COM_DMMR_8_SI_ILOCK_FRONT_INV   (1<<0x2) // Front interlock invert
#define COM_DMMR_8_SI_ILOCK_REAR_INV    (1<<0x3) // Rear  interlock invert
#define COM_DMMR_8_SI_ILOCK_FRONT       (1<<0x8) // Front interlock level
#define COM_DMMR_8_SI_ILOCK_REAR        (1<<0x9) // Rear  interlock level
#define COM_DMMR_8_SI_ILOCK_FRONT_LAST  (1<<0xA) // Last (1-ms old) front interlock level
#define COM_DMMR_8_SI_ILOCK_REAR_LAST   (1<<0xB) // Last (1-ms old) rear  interlock level
#define COM_DMMR_8_SI_ILOCK_ENB         (1<<0xF) // Interlock state
#define COM_DMMR_8_SI_ILOCK_ENB_MASK   (COM_DMMR_8_SI_ILOCK_FRONT_ENB | COM_DMMR_8_SI_ILOCK_REAR_ENB)
#define COM_DMMR_8_SI_ILOCK_FRONT_ALL  (COM_DMMR_8_SI_ILOCK_FRONT     | COM_DMMR_8_SI_ILOCK_FRONT_LAST)
#define COM_DMMR_8_SI_ILOCK_REAR_ALL   (COM_DMMR_8_SI_ILOCK_REAR      | COM_DMMR_8_SI_ILOCK_REAR_LAST)
#define COM_DMMR_8_SI_ILOCK_ALL        (COM_DMMR_8_SI_ILOCK_FRONT_ALL | COM_DMMR_8_SI_ILOCK_REAR_ALL)
int _export COM_DMMR_8_GetInterlockState (WORD & InterlockState  ); // Get interlock state
int _export COM_DMMR_8_SetInterlockState (BYTE   InterlockControl); // Set interlock control bits, i.e. only COM_DMMR_8_SI_ILOCK_xxx_ENB & COM_DMMR_8_SI_ILOCK_xxx_INV bits

int _export COM_DMMR_8_GetInputs      (bool & InterlockFront, bool & InterlockRear, bool & InputSync); // Get instantaneous device input levels
int _export COM_DMMR_8_GetSyncControl (bool & External,       bool & Invert,        bool & Level);     // Get device Sync control
int _export COM_DMMR_8_SetSyncControl (bool   External,       bool   Invert,        bool   Level);     // Set device Sync control
*/


/***********************
// DMMR-8 base device //
***********************/

int _export COM_DMMR_8_RestartBase(); // Restart the base device

int _export COM_DMMR_8_GetBaseProductNo (DWORD & ProductNo);                 // Get product number of base device
int _export COM_DMMR_8_GetBaseManufDate ( WORD & Year, WORD & CalendarWeek); // Get manufacturing date of base device
int _export COM_DMMR_8_GetBaseHwVersion ( WORD & HwVersion);                 // Get hardware version of base device
int _export COM_DMMR_8_GetBaseHwType    ( WORD & HwType);                    // Get hardware type of base device

// Status bits of the base device:
#define COM_DMMR_8_BS_MRES       (1<<0x0) // device reset, bit clears automatically
#define COM_DMMR_8_BS_DIS_OVRD   (1<<0x1) // override device disable from controller
#define COM_DMMR_8_BS_DIS_CTRL   (1<<0x2) // control device disable if DisOvrd=1
#define COM_DMMR_8_BS_ILOCK_OVRD (1<<0x3) // override interlock input (BNC at the front)
#define COM_DMMR_8_BS_ILOCK_CTRL (1<<0x4) // control interlock output if IlockOvrd=1
#define COM_DMMR_8_BS_DITH_OVRD  (1<<0x5) // override dithering setting (switch at the front)
#define COM_DMMR_8_BS_DITH_CTRL  (1<<0x6) // control dithering output if DithOvrd=1
#define COM_DMMR_8_BS_ENB        (1<<0x7) // module enable output = negated backplane output DIS, not used in DMMR-8
#define COM_DMMR_8_BS_DISABLE    (1<<0x8) // device disable from controller (backplane signal Disable = COM_DIS, not used in DMMR-8 => bit is permanently reset)
#define COM_DMMR_8_BS_ENABLE     (1<<0x9) // enable output = internal device control signal enabling the PWM controller
#define COM_DMMR_8_BS_ILOCK_IN   (1<<0xA) // interlock input (BNC at the front)
#define COM_DMMR_8_BS_ILOCK_OUT  (1<<0xB) // interlock output = device's control signal
#define COM_DMMR_8_BS_DITH_IN    (1<<0xC) // dithering enable (switch at the front)
#define COM_DMMR_8_BS_DITH_OUT   (1<<0xD) // dithering output = internal device control signal
#define COM_DMMR_8_BS_ON_TEMP    (1<<0xE) // operating temperature larger than minimum threshold
#define COM_DMMR_8_BS_OFF_TEMP   (1<<0xF) // operating temperature larger than maximum threshold
int _export COM_DMMR_8_GetBaseState ( WORD & BaseState); // Get state of base device

// Control bits the base device
#define COM_DMMR_8_BS_LED_BLUE   (1<<0x8) // LED color: blue, W/O
#define COM_DMMR_8_BS_LED_RED    (1<<0x9) // LED color: red, W/O
#define COM_DMMR_8_BS_LED_GREEN  (1<<0xA) // LED color: green, W/O
#define COM_DMMR_8_BS_LED_EXT    (1<<0xB) // LED color: external control, W/O

int _export COM_DMMR_8_GetBaseLEDData (bool & Red, bool & Green, bool & Blue); // Get LED data

#define COM_DMMR_8_FAN_PWM_MAX  ((0x9F << 1) + 1) // Maximum PWM value (100%) for COM_DMMR_8_GetFanPWM
// Status bits for COM_DMMR_8_GetFanPWM:
#define COM_DMMR_8_FAN_OK        (1<< 9)          // Fan runs properly or is stopped
#define COM_DMMR_8_FAN_PWM_DIS   (1<<10)          // Fan PWM control is disabled
#define COM_DMMR_8_FAN_T_LOW     (1<<11)          // Fan control: low-temperature limit reached
#define COM_DMMR_8_FAN_INSTAL    (1<<12)          // Fan is installed
#define COM_DMMR_8_FAN_DIS       (1<<13)          // Fan is disabled via the front switch
#define COM_DMMR_8_FAN_OVRD_DIS  (1<<14)          // Override fan disable
#define COM_DMMR_8_FAN_CTRL_EXT  (1<<15)          // External fan control enabled
int _export COM_DMMR_8_GetBaseFanPWM   (WORD & SetPWM, WORD & State); // Get fan's PWM value and state
int _export COM_DMMR_8_GetBaseFanRPM (double & RPM);                  // Get fan's RPM
int _export COM_DMMR_8_GetBaseTemp   (double & BaseTemp);             // Get base temperature
//#define COM_DMMR_8_FAN_PWM_MAX  10000                                                                                // Maximum PWM value (100%) for COM_DMMR_8_GetFanData
//int _export COM_DMMR_8_GetBaseFanData (bool & Failed, WORD & MaxRPM, WORD & SetRPM, WORD & MeasuredRPM, WORD & PWM); // Get fan data


/**************************
// DPA-1F module service //
**************************/

#define COM_DMMR_8_MODULE_NUM          8  // Maximum module number
#define COM_DMMR_8_ADDR_BROADCAST  (0xFF) // Broadcasting address

// Return values of COM_DMMR_8_GetModulePresence:
#define COM_DMMR_8_MODULE_NOT_FOUND  0                                                                                      // No module found
#define COM_DMMR_8_MODULE_PRESENT    1                                                                                      // Module with a proper type found
#define COM_DMMR_8_MODULE_INVALID    2                                                                                      // Module found but has an invalid type
//#define COM_DMMR_8_PRESENCE_BASE   (COM_DMMR_8_MODULE_NUM)                                                                // Index of the base-module in the presence flags
int _export COM_DMMR_8_GetModulePresence (bool & Valid, unsigned & MaxModule, BYTE ModulePresence [COM_DMMR_8_MODULE_NUM]); // Get device's maximum module number & module-presence flags
int _export COM_DMMR_8_UpdateModulePresence();                                                                              // Update module-presence flags

int _export COM_DMMR_8_RescanModules();                  // Rescan address pins of all modules
int _export COM_DMMR_8_RescanModule  (unsigned Address); // Rescan address pins of the specified module

int _export COM_DMMR_8_RestartModule (unsigned Address); // Restart the specified module

int _export COM_DMMR_8_GetModuleBufferState (unsigned Address, bool & Empty); // Return true if the input data buffer of the specified module is empty
int _export COM_DMMR_8_ModulePurge          (unsigned Address, bool & Empty); // Clear output data buffer of the device, return value as COM_DMMR_8_GetBufferStateModule


int _export COM_DMMR_8_GetScannedModuleState  (bool & ModuleMismatch);                                             // Get the state of the module scan
int _export COM_DMMR_8_SetScannedModuleState();                                                                    // Reset the module mismatch, i.e save the current device configuration
int _export COM_DMMR_8_GetScannedModuleParams (unsigned Address, DWORD & ScannedProductNo, DWORD & SavedProductNo, // Get scanned & saved product number & hardware type of a module
/*       */                                                      DWORD & ScannedHwType,    DWORD & SavedHwType);

int _export COM_DMMR_8_GetModuleFwVersion (unsigned Address,   WORD & FwVersion);                 // Get module firmware version
int _export COM_DMMR_8_GetModuleFwDate    (unsigned Address,   char * DateString);                // Get module firmware date
int _export COM_DMMR_8_GetModuleProductID (unsigned Address,   char * Identification);            // Get module product identification
int _export COM_DMMR_8_GetModuleProductNo (unsigned Address,  DWORD & ProductNo);                 // Get module product number
int _export COM_DMMR_8_GetModuleManufDate (unsigned Address,   WORD & Year, WORD & CalendarWeek); // Get module manufacturing date
#define COM_DMMR_8_MODULE_TYPE (0xC41E)                                                           // Expected module device type
int _export COM_DMMR_8_GetModuleDevType   (unsigned Address,   WORD & DevType);                   // Get module device type
int _export COM_DMMR_8_GetModuleHwType    (unsigned Address,  DWORD & HwType);                    // Get module hardware type
int _export COM_DMMR_8_GetModuleHwVersion (unsigned Address,   WORD & HwVersion);                 // Get module hardware version

int _export COM_DMMR_8_GetModuleUptimeInt (unsigned Address, DWORD & Sec, WORD & Milisec, DWORD & TotalSec, WORD & TotalMilisec); // Get current and total module uptimes as integer values
int _export COM_DMMR_8_GetModuleOptimeInt (unsigned Address, DWORD & Sec, WORD & Milisec, DWORD & TotalSec, WORD & TotalMilisec); // Get current and total module operation times as integer values
int _export COM_DMMR_8_GetModuleUptime    (unsigned Address, double & Seconds,            double & TotalSeconds                ); // Get current and total module uptimes
int _export COM_DMMR_8_GetModuleOptime    (unsigned Address, double & Seconds,            double & TotalSeconds                ); // Get current and total module operation times
int _export COM_DMMR_8_GetModuleCPUdata   (unsigned Address, double & Load);                                                      // Get CPU load (0-1 = 0-100%)

int _export COM_DMMR_8_GetModuleHousekeeping (unsigned Address,  double & Volt3V3,  double & TempCPU,   double & Volt5V0,  double & Volt12V,
/*       */                                                      double & Volt3V3i, double & TempCPUi,  double & Volt2V5i, double & Volt36Vn,
/*       */                                   double & Volt20Vp, double & Volt20Vn, double & Volt15Vp,  double & Volt15Vn,
/*       */                                   double & Volt1V8p, double & Volt1V8n, double & VoltVrefp, double & VoltVrefn);
/*
#define COM_DMMR_8_MODULE_CHANNEL_NUM  4  // Number of module's output channels
int _export COM_DMMR_8_GetModuleOutputVoltage          (unsigned Address, unsigned Channel, double & Voltage);             // Get module's output voltage
int _export COM_DMMR_8_SetModuleOutputVoltage          (unsigned Address, unsigned Channel, double   Voltage);             // Set module's output voltage
int _export COM_DMMR_8_GetMeasuredModuleOutputVoltages (unsigned Address, double Voltage [COM_DMMR_8_MODULE_CHANNEL_NUM]); // Get measured module's output voltages
*/
/*
// Module state bits:
#define COM_DMMR_8_MS_OUT1_LO  (1<<0x0) // Output #1 voltage is lower than limit
#define COM_DMMR_8_MS_OUT2_LO  (1<<0x1) // Output #2 voltage is lower than limit
#define COM_DMMR_8_MS_OUT3_LO  (1<<0x2) // Output #3 voltage is lower than limit
#define COM_DMMR_8_MS_OUT4_LO  (1<<0x3) // Output #4 voltage is lower than limit
#define COM_DMMR_8_MS_OUT1_HI  (1<<0x4) // Output #1 voltage is higher than limit
#define COM_DMMR_8_MS_OUT2_HI  (1<<0x5) // Output #2 voltage is higher than limit
#define COM_DMMR_8_MS_OUT3_HI  (1<<0x6) // Output #3 voltage is higher than limit
#define COM_DMMR_8_MS_OUT4_HI  (1<<0x7) // Output #4 voltage is higher than limit
#define COM_DMMR_8_MS_ACTIVE   (1<<0xF) // Device is active, i.e. output voltages can be nonzero
*/
int _export COM_DMMR_8_GetModuleState (unsigned Address, WORD & ModuleState); // Get module state
/*
int _export COM_DMMR_8_SetModuleMeasChanNum (unsigned Address, unsigned   MeasChanNum); // Set the number of ADC channels used for data acquisition
int _export COM_DMMR_8_GetModuleMeasChanNum (unsigned Address, unsigned & MeasChanNum); // Get the number of ADC channels used for data acquisition
*/
#define COM_DMMR_8_MEAS_RANGE_NUM        5  // number of measurement ranges
int _export COM_DMMR_8_SetModuleMeasRange (unsigned Address, unsigned   MeasRange);                   // Set measurement range
int _export COM_DMMR_8_SetModuleAutoRange (unsigned Address,                       bool   AutoRange); // Set automatic range switching
int _export COM_DMMR_8_GetModuleMeasRange (unsigned Address, unsigned & MeasRange, bool & AutoRange); // Get measurement range & automatic range switching
#define COM_DMMR_8_MEAS_CUR_RDY      (1<<0) // current data ready
#define COM_DMMR_8_HK_MEAS_DATA_RDY  (1<<1) // housekeeping data from measurement block ready
#define COM_DMMR_8_HK_MOD_DATA_RDY   (1<<2) // housekeeping data from module ready
int _export COM_DMMR_8_GetModuleReadyFlags (unsigned Address, BYTE   & ReadyFlags);                       // Get data-ready flags
int _export COM_DMMR_8_GetModuleCurent     (unsigned Address, double & MeasCurent, unsigned & MeasRange); // Get current measured by module & used measurement range

int _export COM_DMMR_8_SetAutomaticCurent (bool   AutomaticCurent);                                                                     // Turn on or off automatic current measurement
int _export COM_DMMR_8_GetAutomaticCurent (bool & AutomaticCurent);                                                                     // Get state of automatic current measurement
//#define COM_DMMR_8_ADDR_NO  (unsigned (-1))                                                                                             // Address value if no data is available
//int _export COM_DMMR_8_GetCurent (unsigned & Address, double & MeasCurent, unsigned & MeasRange, DWORD & Seconds, WORD & Milliseconds); // Check if new current data is available, return module address, current value, used measurement range and time stamp of the data
int _export COM_DMMR_8_GetCurent (unsigned & Address, double & MeasCurent, unsigned & MeasRange, double & Time); // Check if new current data is available, return module address, current value, used measurement range and time stamp of the data


/*****************************
// Configuration management //
*****************************/

#define COM_DMMR_8_MAX_REG (0x60 - 3)
int _export COM_DMMR_8_GetCurrentConfig (      DWORD Config [COM_DMMR_8_MAX_REG]); // Get current configuration
int _export COM_DMMR_8_SetCurrentConfig (const DWORD Config [COM_DMMR_8_MAX_REG]); // Set current configuration

#define COM_DMMR_8_MAX_CONFIG  500
int _export COM_DMMR_8_GetConfigList (bool Active [COM_DMMR_8_MAX_CONFIG], bool Valid [COM_DMMR_8_MAX_CONFIG]); // Get configuration list
int _export COM_DMMR_8_SaveCurrentConfig (WORD ConfigNumber);                                                   // Save current configuration to NVM
int _export COM_DMMR_8_LoadCurrentConfig (WORD ConfigNumber);                                                   // Load current configuration from NVM

#define COM_DMMR_8_CONFIG_NAME_SIZE  0x89                                                                 // Allowed size of the configuration name
int _export COM_DMMR_8_GetConfigName  (WORD ConfigNumber,       char Name [COM_DMMR_8_CONFIG_NAME_SIZE]); // Get configuration name
int _export COM_DMMR_8_SetConfigName  (WORD ConfigNumber, const char Name [COM_DMMR_8_CONFIG_NAME_SIZE]); // Set configuration name

int _export COM_DMMR_8_GetConfigData  (WORD ConfigNumber,       DWORD Config [COM_DMMR_8_MAX_REG]); // Get configuration data
int _export COM_DMMR_8_SetConfigData  (WORD ConfigNumber, const DWORD Config [COM_DMMR_8_MAX_REG]); // Set configuration data

int _export COM_DMMR_8_GetConfigFlags (WORD ConfigNumber, bool & Active, bool & Valid); // Get configuration flags
int _export COM_DMMR_8_SetConfigFlags (WORD ConfigNumber, bool   Active, bool   Valid); // Set configuration flags


/*******************
// Error handling //
*******************/

int          _export COM_DMMR_8_GetInterfaceState();                     // Get software interface state
#ifdef _MSC_VER
_export const char * COM_DMMR_8_GetErrorMessage();                       // Get error message corresponding to the software interface state
_export const char * COM_DMMR_8_GetIOErrorMessage();                     // Get error message corresponding to the serial port interface state
#else
const char * _export COM_DMMR_8_GetErrorMessage();                       // Get error message corresponding to the software interface state
const char * _export COM_DMMR_8_GetIOErrorMessage();                     // Get error message corresponding to the serial port interface state
#endif
int          _export COM_DMMR_8_GetIOState          (int   & IOState);   // Get and clear last serial port interface state
#ifdef _MSC_VER
_export const char * COM_DMMR_8_GetIOStateMessage   (int     IOState);   // Get error message corresponding to the specified interface state
#else
const char * _export COM_DMMR_8_GetIOStateMessage   (int     IOState);   // Get error message corresponding to the specified interface state
#endif
int          _export COM_DMMR_8_GetCommError        (DWORD & CommError); // Get and clear last communication-port error
#ifdef _MSC_VER
_export const char * COM_DMMR_8_GetCommErrorMessage (DWORD   CommError); // Get error message corresponding to the communication port error
#else
const char * _export COM_DMMR_8_GetCommErrorMessage (DWORD   CommError); // Get error message corresponding to the communication port error
#endif

#ifdef __cplusplus
}
#endif

#endif//__COM_DMMR_8_H__
