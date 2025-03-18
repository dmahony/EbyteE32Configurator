#!/usr/bin/env python3
"""
E32-915MHz LoRa Module Configurator
-----------------------------------
A cross-platform application for configuring EBYTE E32 series LoRa modules.
Specifically optimized for the 915MHz version.
Provides both GUI and CLI interfaces for complete module configuration.

Usage:
    - Run without arguments to open the GUI
    - Run with --cli argument to use the command-line interface
    - Run with --help to see all available CLI options
"""

import argparse
import sys
import time
import threading
import json
import os
import serial
import serial.tools.list_ports
from enum import Enum, auto
import logging

# GUI imports - wrapped in try/except to allow CLI-only usage if GUI dependencies are missing
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog, scrolledtext
    import tkinter.font as tkFont
    HAS_GUI = True
except ImportError:
    HAS_GUI = False

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("E32-Configurator")

# Version information
__version__ = "1.0.0"

class ModuleMode(Enum):
    """E32 operating modes based on M0 and M1 pins"""
    NORMAL = auto()          # M0=0, M1=0: Transparent transmission mode
    WOR_SENDING = auto()     # M0=1, M1=0: WOR transmitting mode
    WOR_RECEIVING = auto()   # M0=0, M1=1: WOR receiving mode
    CONFIGURATION = auto()   # M0=1, M1=1: Configuration mode (for setting parameters)

class E32Module:
    """
    Handles communication with the E32 LoRa module
    """
    def __init__(self, port=None, baudrate=9600, timeout=1, m0_pin=None, m1_pin=None, aux_pin=None, use_gpio=False, manual_config=False):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial = None
        self.current_mode = None
        
        # GPIO pins for module control (optional, for Raspberry Pi or similar)
        self.m0_pin = m0_pin
        self.m1_pin = m1_pin
        self.aux_pin = aux_pin
        self.use_gpio = use_gpio
        
        # Flag to indicate if the user has manually set configuration mode
        self.manual_config = manual_config
        
        # Initialize GPIO if available and requested
        if self.use_gpio:
            try:
                import RPi.GPIO as GPIO
                self.GPIO = GPIO
                self.GPIO.setmode(GPIO.BCM)
                if self.m0_pin:
                    self.GPIO.setup(self.m0_pin, GPIO.OUT)
                if self.m1_pin:
                    self.GPIO.setup(self.m1_pin, GPIO.OUT)
                if self.aux_pin:
                    self.GPIO.setup(self.aux_pin, GPIO.IN)
                logger.info("GPIO initialized for module control")
            except ImportError:
                logger.warning("GPIO library not available. Cannot control module pins directly.")
                self.use_gpio = False
    
    def connect(self):
        """Connect to the LoRa module"""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE
            )
            logger.info(f"Connected to port {self.port} at {self.baudrate} baud")
            return True
        except serial.SerialException as e:
            logger.error(f"Failed to connect: {e}")
            return False
            
    def disconnect(self):
        """Disconnect from the LoRa module"""
        if self.serial and self.serial.is_open:
            self.serial.close()
            logger.info("Disconnected from module")
    
    def _set_mode_pins(self, mode):
        """Set M0 and M1 pins for the specified mode"""
        if not self.use_gpio or not (self.m0_pin and self.m1_pin):
            logger.warning("Cannot set mode pins: GPIO control not available or pins not specified")
            logger.info("Please ensure M0 and M1 pins are set correctly manually")
            # Return True to allow operation to continue if user has manually set the pins
            return True
            
        if mode == ModuleMode.NORMAL:
            self.GPIO.output(self.m0_pin, GPIO.LOW)
            self.GPIO.output(self.m1_pin, GPIO.LOW)
        elif mode == ModuleMode.WOR_SENDING:
            self.GPIO.output(self.m0_pin, GPIO.HIGH)
            self.GPIO.output(self.m1_pin, GPIO.LOW)
        elif mode == ModuleMode.WOR_RECEIVING:
            self.GPIO.output(self.m0_pin, GPIO.LOW)
            self.GPIO.output(self.m1_pin, GPIO.HIGH)
        elif mode == ModuleMode.CONFIGURATION:
            self.GPIO.output(self.m0_pin, GPIO.HIGH)
            self.GPIO.output(self.m1_pin, GPIO.HIGH)
        else:
            logger.error(f"Invalid mode: {mode}")
            return False
            
        # Wait for AUX pin to go HIGH if available
        if self.aux_pin:
            timeout = 100  # 1 second (10ms * 100)
            while timeout > 0 and not self.GPIO.input(self.aux_pin):
                time.sleep(0.01)
                timeout -= 1
            
            if timeout <= 0:
                logger.warning("Timeout waiting for AUX pin to go HIGH")
                
        # Additional delay to ensure mode switch is complete
        time.sleep(0.1)
        return True
    
    def set_mode(self, mode):
        """Set the module's operating mode"""
        if self.current_mode == mode:
            logger.debug(f"Module already in {mode} mode")
            return True
            
        # For configuration mode, check if it's already in that mode
        if mode == ModuleMode.CONFIGURATION and self._check_config_mode():
            logger.info("Module already in configuration mode")
            self.current_mode = ModuleMode.CONFIGURATION
            return True
            
        logger.info(f"Setting module to {mode} mode")
        result = self._set_mode_pins(mode)
        if result:
            self.current_mode = mode
            
        return result
    
    def send_command(self, command, timeout=1):
        """Send command to the module and receive response"""
        if not self.serial or not self.serial.is_open:
            logger.error("Not connected to module")
            return None
            
        # Clear any pending data
        self.serial.reset_input_buffer()
        
        # Send command
        logger.debug(f"Sending command: {command.hex()}")
        self.serial.write(command)
        
        # Read response
        response = bytearray()
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.serial.in_waiting:
                byte = self.serial.read(1)
                response.extend(byte)
                
                # For E32, responses are typically 6 bytes for parameter gets
                # But let's wait a bit longer to make sure we get everything
                if len(response) >= 6 and command[0] == 0xC1:
                    # For parameter query, we expect exactly 6 bytes
                    break
                elif len(response) >= 1 and command[0] in [0xC0, 0xC2, 0xC4]:
                    # For set parameters, reset, etc., response might be shorter
                    # or might be the same length as the command
                    if response[0] == command[0]:
                        # Wait a bit more to get complete response
                        time.sleep(0.05)
                        if self.serial.in_waiting:
                            response.extend(self.serial.read(self.serial.in_waiting))
                        break
                    
            time.sleep(0.01)
        
        if response:
            logger.debug(f"Response received: {response.hex()}")
        else:
            logger.warning(f"No response received for command: {command.hex()}")
            
        return response
    
    def enter_config_mode(self):
        """Enter configuration mode for sending commands"""
        # First check if the module is already in configuration mode
        if self._check_config_mode():
            logger.info("Module already in configuration mode")
            self.current_mode = ModuleMode.CONFIGURATION
            return True
        # If not, try to set the mode using pins
        return self.set_mode(ModuleMode.CONFIGURATION)
        
    def _check_config_mode(self):
        """Check if the module is already in configuration mode
        Returns True if the module is in configuration mode, False otherwise"""
        if not self.serial or not self.serial.is_open:
            return False
            
        # Send a simple query command (get parameters)
        try:
            # Clear any pending data
            self.serial.reset_input_buffer()
            
            # Send the command to read parameters
            command = bytes([0xC1, 0xC1, 0xC1])
            self.serial.write(command)
            
            # Wait for response
            start_time = time.time()
            response = bytearray()
            
            # Wait up to 0.5 seconds for response
            while (time.time() - start_time) < 0.5:
                if self.serial.in_waiting:
                    byte = self.serial.read(1)
                    response.extend(byte)
                    
                    # If we have 6 bytes, check if valid
                    if len(response) >= 6:
                        break
                        
                time.sleep(0.01)
            
            # Check if response starts with C1 or C0 (valid response to parameter query)
            if response and len(response) >= 6:
                if response[0] == 0xC1:
                    logger.info("Configuration mode verified - received valid C1 response")
                    return True
                elif response[0] == 0xC0:
                    # Some E32 modules might respond with C0 instead of C1
                    logger.info("Configuration mode verified - received valid C0 response")
                    return True
                    
            logger.debug(f"No valid configuration mode response: {response.hex() if response else 'No data'}")
        except Exception as e:
            logger.debug(f"Exception when checking config mode: {e}")
            
        return False
    
    def exit_config_mode(self):
        """Exit configuration mode and return to normal mode"""
        # If using manual configuration, don't exit config mode
        if hasattr(self, 'manual_config') and self.manual_config:
            logger.info("Not exiting configuration mode - manual configuration is enabled")
            return True
            
        return self.set_mode(ModuleMode.NORMAL)
    
    def get_parameters(self):
        """Read all parameters from the module"""
        if not self.enter_config_mode():
            return None
            
        params = {}
        
        try:
            # Send command to read parameters (C1C1C1)
            command = bytes([0xC1, 0xC1, 0xC1])
            response = self.send_command(command)
            
            # The module should respond with a C1 followed by parameters
            # However, some E32 modules might respond with C0 followed by parameters instead
            if response and len(response) >= 6:
                if response[0] == 0xC1:
                    # Standard response format
                    logger.info("Received standard C1 response format")
                    params["addh"] = response[1]
                    params["addl"] = response[2]
                    params["sped"] = response[3]
                    params["chan"] = response[4]
                    params["option"] = response[5]
                elif response[0] == 0xC0:
                    # Alternative response format (some modules respond with C0)
                    logger.info("Received alternative C0 response format")
                    params["addh"] = response[1]
                    params["addl"] = response[2]
                    params["sped"] = response[3]
                    params["chan"] = response[4]
                    params["option"] = response[5]
                else:
                    logger.error(f"Invalid response header when reading parameters: {response.hex() if response else 'None'}")
                    return None
                
                # Calculate derived parameters
                params["address"] = (params["addh"] << 8) + params["addl"]
                
                # Parse SPED byte
                params["parity"] = (params["sped"] >> 6) & 0x03
                params["uart_baud"] = (params["sped"] >> 3) & 0x07
                params["air_data_rate"] = params["sped"] & 0x07
                
                # Parse OPTION byte
                params["fixed_transmission"] = (params["option"] >> 7) & 0x01
                params["io_drive_mode"] = (params["option"] >> 6) & 0x01
                params["wake_up_time"] = (params["option"] >> 3) & 0x07
                params["fec"] = (params["option"] >> 2) & 0x01
                params["transmission_power"] = params["option"] & 0x03
                
                # Calculate frequency (for 915MHz version base + channel*1MHz)
                params["frequency"] = 915 + params["chan"]
                
            else:
                logger.error(f"Invalid response when reading parameters: {response.hex() if response else 'None'}")
                return None
                
        except Exception as e:
            logger.error(f"Error reading parameters: {e}")
            params = None
            
        # Don't exit configuration mode if we are using manual configuration
        # This lets the user continue working with the module without constantly toggling pins
        if hasattr(self, 'manual_config') and self.manual_config:
            logger.info("Keeping module in configuration mode (manual configuration selected)")
            return params
        
        # Exit configuration mode
        self.exit_config_mode()
            
        return params
    
    def set_parameters(self, params):
        """Write parameters to the module"""
        if not self.enter_config_mode():
            return False
            
        success = True
        
        try:
            # Prepare the command bytes
            command = bytearray(6)
            command[0] = 0xC0  # Command header for setting parameters
            
            # Set address bytes
            if "address" in params:
                address = params["address"]
                command[1] = (address >> 8) & 0xFF  # ADDH
                command[2] = address & 0xFF         # ADDL
            elif "addh" in params and "addl" in params:
                command[1] = params["addh"]
                command[2] = params["addl"]
            else:
                # Use defaults if not specified
                command[1] = 0x00
                command[2] = 0x00
            
            # Set SPED byte (parity, UART baud rate, air data rate)
            sped = 0
            if "parity" in params:
                sped |= (params["parity"] & 0x03) << 6
            if "uart_baud" in params:
                sped |= (params["uart_baud"] & 0x07) << 3
            if "air_data_rate" in params:
                sped |= params["air_data_rate"] & 0x07
            command[3] = sped
            
            # Set CHAN byte (channel)
            if "chan" in params:
                command[4] = params["chan"] & 0xFF
            else:
                # For 915MHz, use channel 0 (915MHz) as default
                command[4] = 0x00
            
            # Set OPTION byte (fixed transmission, IO drive mode, wake-up time, FEC, transmission power)
            option = 0
            if "fixed_transmission" in params:
                option |= (params["fixed_transmission"] & 0x01) << 7
            if "io_drive_mode" in params:
                option |= (params["io_drive_mode"] & 0x01) << 6
            if "wake_up_time" in params:
                option |= (params["wake_up_time"] & 0x07) << 3
            if "fec" in params:
                option |= (params["fec"] & 0x01) << 2
            if "transmission_power" in params:
                option |= params["transmission_power"] & 0x03
            command[5] = option
            
            # Send the command
            response = self.send_command(command)
            
            # Check if the response is valid
            # Some modules return C0 followed by parameters, some just return nothing
            if not response:
                # No response is common for set parameters, we'll consider it successful
                logger.info("No response from module after setting parameters (this is normal for some modules)")
            elif len(response) < 1 or (response[0] != 0xC0 and response[0] != 0xFF):
                logger.warning(f"Unexpected response when setting parameters: {response.hex() if response else 'None'}")
                # We'll still consider it successful since some modules don't respond properly
            else:
                logger.info(f"Successfully set parameters, response: {response.hex()}")
            
        except Exception as e:
            logger.error(f"Error setting parameters: {e}")
            success = False
            
        # Don't exit config mode if manual configuration is enabled
        if not (hasattr(self, 'manual_config') and self.manual_config):
            self.exit_config_mode()
            
        return success
    
    def reset_module(self):
        """Reset the module"""
        if not self.enter_config_mode():
            return False
            
        try:
            # Send reset command (C4C4C4)
            command = bytes([0xC4, 0xC4, 0xC4])
            response = self.send_command(command)
            
            # For reset, we don't expect a specific response, but the module should reset
            # Wait a bit to allow the module to complete the reset
            time.sleep(1)
            
            logger.info("Module reset sent")
            return True
        except Exception as e:
            logger.error(f"Error resetting module: {e}")
            return False
        finally:
            self.exit_config_mode()
    
    def factory_reset(self):
        """Reset the module to factory defaults"""
        # For E32, we can set the default parameters
        default_params = {
            "addh": 0x00,
            "addl": 0x00,
            "sped": 0x1A,  # 9600 8N1 2.4k air rate
            "chan": 0x17,  # Channel 23
            "option": 0x44  # 0x44 = 0b01000100: IO drive mode=1, FEC=1, power=0
        }
        
        return self.set_parameters(default_params)
            
    def version(self):
        """Get module version"""
        if not self.enter_config_mode():
            return None
            
        try:
            # Send version command (C3C3C3)
            command = bytes([0xC3, 0xC3, 0xC3])
            response = self.send_command(command)
            
            if response and len(response) >= 4 and response[0] == 0xC3:
                # Extract version information
                version_info = {
                    "model": response[1],
                    "version": response[2],
                    "features": response[3]
                }
                return version_info
            else:
                logger.error("Invalid response when getting version")
                return None
        except Exception as e:
            logger.error(f"Error getting version: {e}")
            return None
        finally:
            self.exit_config_mode()

class E32ConfigGUI:
    """
    GUI interface for configuring the E32 module
    """
    def __init__(self, master):
        self.master = master
        self.master.title("E32-915MHz LoRa Module Configurator")
        self.master.geometry("800x600")
        self.master.minsize(800, 600)
        
        # Module instance
        self.module = None
        
        # Serial port variables
        self.port_var = tk.StringVar()
        self.baudrate_var = tk.IntVar(value=9600)
        
        # Parameter variables
        self._init_parameter_vars()
        
        # Create the main notebook/tabs
        self.notebook = ttk.Notebook(self.master)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Create tabs
        self.connection_tab = ttk.Frame(self.notebook)
        self.basic_tab = ttk.Frame(self.notebook)
        self.advanced_tab = ttk.Frame(self.notebook)
        self.monitor_tab = ttk.Frame(self.notebook)
        
        self.notebook.add(self.connection_tab, text="Connection")
        self.notebook.add(self.basic_tab, text="Basic Settings")
        self.notebook.add(self.advanced_tab, text="Advanced Settings")
        self.notebook.add(self.monitor_tab, text="Monitor")
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        self.status_bar = tk.Label(
            self.master, 
            textvariable=self.status_var, 
            bd=1, 
            relief=tk.SUNKEN, 
            anchor=tk.W
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Setup tabs
        self._setup_connection_tab()
        self._setup_basic_tab()
        self._setup_advanced_tab()
        self._setup_monitor_tab()
        
        # Disable tabs until connected
        self.notebook.tab(1, state="disabled")
        self.notebook.tab(2, state="disabled")
        self.notebook.tab(3, state="disabled")
        
        # Add a window close handler
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)
        
    def _init_parameter_vars(self):
        """Initialize variables for module parameters"""
        # Basic settings
        self.address_var = tk.IntVar(value=0)
        self.channel_var = tk.IntVar(value=0)
        self.uart_baud_var = tk.IntVar(value=3)  # Default: 9600 bps
        self.parity_var = tk.IntVar(value=0)     # Default: 8N1
        self.air_rate_var = tk.IntVar(value=2)   # Default: 2.4 kbps
        self.power_var = tk.IntVar(value=0)      # Default: Max power
        
        # Advanced settings
        self.fixed_trans_var = tk.IntVar(value=0)  # Default: Transparent
        self.io_drive_var = tk.IntVar(value=1)     # Default: Push-pull
        self.wake_time_var = tk.IntVar(value=0)    # Default: 250ms
        self.fec_var = tk.IntVar(value=1)          # Default: FEC On
    
    def _setup_connection_tab(self):
        """Setup the connection tab"""
        # Create a frame for the connection settings
        conn_frame = ttk.LabelFrame(self.connection_tab, text="Connection Settings")
        conn_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Serial port selection
        ttk.Label(conn_frame, text="Serial Port:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.port_combo = ttk.Combobox(conn_frame, textvariable=self.port_var)
        self.port_combo.grid(row=0, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Button(conn_frame, text="Refresh", command=self._refresh_ports).grid(row=0, column=2, padx=5, pady=5)
        
        # Baud rate selection
        ttk.Label(conn_frame, text="Baud Rate:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        baud_rates = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
        self.baud_combo = ttk.Combobox(conn_frame, textvariable=self.baudrate_var, values=baud_rates)
        self.baud_combo.grid(row=1, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        
        # Connect/Disconnect button
        self.connect_button = ttk.Button(conn_frame, text="Connect", command=self._toggle_connection)
        self.connect_button.grid(row=2, column=0, columnspan=3, padx=5, pady=20)
        
        # Version and info
        ttk.Label(conn_frame, text=f"E32-915MHz LoRa Module Configurator v{__version__}").grid(
            row=3, column=0, columnspan=3, padx=5, pady=5
        )
        
        # Description
        desc_text = """This application allows you to configure EBYTE E32-915MHz series LoRa modules.
Connect the module to your computer using a USB-to-serial adapter with the following wiring:

- Connect module's M0 and M1 pins to your adapter if you need automatic mode switching
- Make sure the module is powered with 3.3-5V DC
- Default baud rate is 9600 bps

For configuration mode, both M0 and M1 must be set HIGH.
If you've already set M0=HIGH and M1=HIGH manually, check the option below.
        """
        desc_label = ttk.Label(conn_frame, text=desc_text, justify=tk.LEFT, wraplength=500)
        desc_label.grid(row=4, column=0, columnspan=3, padx=5, pady=10, sticky=tk.W)
        
        # Additional options frame
        options_frame = ttk.LabelFrame(conn_frame, text="Additional Options")
        options_frame.grid(row=5, column=0, columnspan=3, padx=5, pady=10, sticky=tk.W+tk.E)
        
        # Manual configuration mode option
        self.manual_config_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options_frame, 
            text="I have manually set M0=HIGH, M1=HIGH for configuration mode",
            variable=self.manual_config_var
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)
        
        # GPIO control options (for Raspberry Pi or similar)
        self.use_gpio_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options_frame, 
            text="Use GPIO pins for mode control (Raspberry Pi)",
            variable=self.use_gpio_var
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=5, pady=5)
        
        # GPIO pin settings
        ttk.Label(options_frame, text="M0 GPIO Pin:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.m0_pin_var = tk.IntVar(value=16)
        ttk.Entry(options_frame, textvariable=self.m0_pin_var, width=5).grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)
        
        ttk.Label(options_frame, text="M1 GPIO Pin:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        self.m1_pin_var = tk.IntVar(value=17)
        ttk.Entry(options_frame, textvariable=self.m1_pin_var, width=5).grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)
        
        ttk.Label(options_frame, text="AUX GPIO Pin:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=2)
        self.aux_pin_var = tk.IntVar(value=22)
        ttk.Entry(options_frame, textvariable=self.aux_pin_var, width=5).grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)
        
        # Load/Save configuration buttons
        button_frame = ttk.Frame(conn_frame)
        button_frame.grid(row=6, column=0, columnspan=3, padx=5, pady=10)
        
        ttk.Button(button_frame, text="Load Config", command=self._load_config).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Save Config", command=self._save_config).pack(side=tk.LEFT, padx=5)
        
        # Initial refresh of ports
        self._refresh_ports()
    
    def _setup_basic_tab(self):
        """Setup the basic settings tab"""
        # Create a frame for the basic settings
        basic_frame = ttk.LabelFrame(self.basic_tab, text="Basic Configuration")
        basic_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Module address
        ttk.Label(basic_frame, text="Module Address (0-65535):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(basic_frame, textvariable=self.address_var).grid(row=0, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Label(basic_frame, text="Unique identifier for the module").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        
        # Channel
        ttk.Label(basic_frame, text="Channel (0-83):").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(basic_frame, textvariable=self.channel_var).grid(row=1, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Label(basic_frame, text="Frequency = 915MHz + Channel*1MHz").grid(row=1, column=2, sticky=tk.W, padx=5, pady=5)
        
        # UART Baud Rate
        ttk.Label(basic_frame, text="UART Baud Rate:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        baud_options = ["1200 bps", "2400 bps", "4800 bps", "9600 bps", "19200 bps", "38400 bps", "57600 bps", "115200 bps"]
        ttk.Combobox(basic_frame, textvariable=self.uart_baud_var, values=list(range(len(baud_options))), 
                     state="readonly").grid(row=2, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Label(basic_frame, text=f"Serial port speed").grid(row=2, column=2, sticky=tk.W, padx=5, pady=5)
        
        # Create a function to update the displayed baud rate
        def update_baud_display(*args):
            idx = self.uart_baud_var.get()
            if 0 <= idx < len(baud_options):
                baud_label.config(text=f"Selected: {baud_options[idx]}")
        
        # Add a label to display the selected baud rate
        baud_label = ttk.Label(basic_frame, text=f"Selected: {baud_options[self.uart_baud_var.get()]}")
        baud_label.grid(row=2, column=3, sticky=tk.W, padx=5, pady=5)
        
        # Bind the variable to the update function
        self.uart_baud_var.trace_add("write", update_baud_display)
        
        # Parity
        ttk.Label(basic_frame, text="Serial Parity:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=5)
        parity_options = ["8N1", "8O1", "8E1", "8N1 (same as 0)"]
        ttk.Combobox(basic_frame, textvariable=self.parity_var, values=list(range(len(parity_options))), 
                    state="readonly").grid(row=3, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Label(basic_frame, text="Serial data format").grid(row=3, column=2, sticky=tk.W, padx=5, pady=5)
        
        # Create a function to update the displayed parity
        def update_parity_display(*args):
            idx = self.parity_var.get()
            if 0 <= idx < len(parity_options):
                parity_label.config(text=f"Selected: {parity_options[idx]}")
        
        # Add a label to display the selected parity
        parity_label = ttk.Label(basic_frame, text=f"Selected: {parity_options[self.parity_var.get()]}")
        parity_label.grid(row=3, column=3, sticky=tk.W, padx=5, pady=5)
        
        # Bind the variable to the update function
        self.parity_var.trace_add("write", update_parity_display)
        
        # Air Rate
        ttk.Label(basic_frame, text="Air Rate:").grid(row=4, column=0, sticky=tk.W, padx=5, pady=5)
        airrate_options = ["0.3 kbps", "1.2 kbps", "2.4 kbps", "4.8 kbps", "9.6 kbps", "19.2 kbps", "19.2 kbps", "19.2 kbps"]
        ttk.Combobox(basic_frame, textvariable=self.air_rate_var, values=list(range(len(airrate_options))), 
                     state="readonly").grid(row=4, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Label(basic_frame, text="Wireless transmission rate").grid(row=4, column=2, sticky=tk.W, padx=5, pady=5)
        
        # Create a function to update the displayed air rate
        def update_airrate_display(*args):
            idx = self.air_rate_var.get()
            if 0 <= idx < len(airrate_options):
                airrate_label.config(text=f"Selected: {airrate_options[idx]}")
        
        # Add a label to display the selected air rate
        airrate_label = ttk.Label(basic_frame, text=f"Selected: {airrate_options[self.air_rate_var.get()]}")
        airrate_label.grid(row=4, column=3, sticky=tk.W, padx=5, pady=5)
        
        # Bind the variable to the update function
        self.air_rate_var.trace_add("write", update_airrate_display)
        
        # Transmit Power
        ttk.Label(basic_frame, text="Transmit Power:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=5)
        power_options = ["30 dBm (max)", "27 dBm", "24 dBm", "21 dBm"]
        ttk.Combobox(basic_frame, textvariable=self.power_var, values=list(range(len(power_options))), 
                     state="readonly").grid(row=5, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Label(basic_frame, text="RF transmission power").grid(row=5, column=2, sticky=tk.W, padx=5, pady=5)
        
        # Create a function to update the displayed power
        def update_power_display(*args):
            idx = self.power_var.get()
            if 0 <= idx < len(power_options):
                power_label.config(text=f"Selected: {power_options[idx]}")
        
        # Add a label to display the selected power
        power_label = ttk.Label(basic_frame, text=f"Selected: {power_options[self.power_var.get()]}")
        power_label.grid(row=5, column=3, sticky=tk.W, padx=5, pady=5)
        
        # Bind the variable to the update function
        self.power_var.trace_add("write", update_power_display)
        
        # Frequency display
        ttk.Label(basic_frame, text="Frequency:").grid(row=6, column=0, sticky=tk.W, padx=5, pady=5)
        
        # Create a function to update the displayed frequency based on channel
        def update_frequency_display(*args):
            channel = self.channel_var.get()
            try:
                frequency = 915 + channel
                freq_label.config(text=f"{frequency} MHz")
            except:
                freq_label.config(text="Invalid channel")
        
        # Add a label to display the calculated frequency
        freq_label = ttk.Label(basic_frame, text="915 MHz")
        freq_label.grid(row=6, column=1, sticky=tk.W, padx=5, pady=5)
        
        # Bind the channel variable to the frequency update function
        self.channel_var.trace_add("write", update_frequency_display)
        
        # Buttons frame
        button_frame = ttk.Frame(basic_frame)
        button_frame.grid(row=7, column=0, columnspan=4, pady=20)
        
        # Read/Write buttons
        ttk.Button(button_frame, text="Read from Module", command=self._read_params).pack(side=tk.LEFT, padx=10)
        ttk.Button(button_frame, text="Write to Module", command=self._write_params).pack(side=tk.LEFT, padx=10)
        ttk.Button(button_frame, text="Reset Module", command=self._reset_module).pack(side=tk.LEFT, padx=10)
        ttk.Button(button_frame, text="Factory Reset", command=self._factory_reset).pack(side=tk.LEFT, padx=10)
    
    def _setup_advanced_tab(self):
        """Setup the advanced settings tab"""
        # Create a frame for the advanced settings
        adv_frame = ttk.LabelFrame(self.advanced_tab, text="Advanced Configuration")
        adv_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Fixed Transmission Mode
        ttk.Label(adv_frame, text="Transmission Mode:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        trans_options = ["Transparent Transmission", "Fixed Point Transmission"]
        ttk.Combobox(adv_frame, textvariable=self.fixed_trans_var, values=list(range(len(trans_options))), 
                     state="readonly").grid(row=0, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Label(adv_frame, text="Data transmission method").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        
        # Create a function to update the displayed transmission mode
        def update_trans_display(*args):
            idx = self.fixed_trans_var.get()
            if 0 <= idx < len(trans_options):
                trans_label.config(text=f"Selected: {trans_options[idx]}")
        
        # Add a label to display the selected transmission mode
        trans_label = ttk.Label(adv_frame, text=f"Selected: {trans_options[self.fixed_trans_var.get()]}")
        trans_label.grid(row=0, column=3, sticky=tk.W, padx=5, pady=5)
        
        # Bind the variable to the update function
        self.fixed_trans_var.trace_add("write", update_trans_display)
        
        # IO Drive Mode
        ttk.Label(adv_frame, text="IO Drive Mode:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        io_options = ["Open-collector output", "Push-pull output"]
        ttk.Combobox(adv_frame, textvariable=self.io_drive_var, values=list(range(len(io_options))), 
                     state="readonly").grid(row=1, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Label(adv_frame, text="IO pin drive mode").grid(row=1, column=2, sticky=tk.W, padx=5, pady=5)
        
        # Create a function to update the displayed IO drive mode
        def update_io_display(*args):
            idx = self.io_drive_var.get()
            if 0 <= idx < len(io_options):
                io_label.config(text=f"Selected: {io_options[idx]}")
        
        # Add a label to display the selected IO drive mode
        io_label = ttk.Label(adv_frame, text=f"Selected: {io_options[self.io_drive_var.get()]}")
        io_label.grid(row=1, column=3, sticky=tk.W, padx=5, pady=5)
        
        # Bind the variable to the update function
        self.io_drive_var.trace_add("write", update_io_display)
        
        # Wake-up Time
        ttk.Label(adv_frame, text="Wake-up Time:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        wake_options = ["250ms", "500ms", "750ms", "1000ms", "1250ms", "1500ms", "1750ms", "2000ms"]
        ttk.Combobox(adv_frame, textvariable=self.wake_time_var, values=list(range(len(wake_options))), 
                     state="readonly").grid(row=2, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Label(adv_frame, text="Wireless wake-up interval").grid(row=2, column=2, sticky=tk.W, padx=5, pady=5)
        
        # Create a function to update the displayed wake-up time
        def update_wake_display(*args):
            idx = self.wake_time_var.get()
            if 0 <= idx < len(wake_options):
                wake_label.config(text=f"Selected: {wake_options[idx]}")
        
        # Add a label to display the selected wake-up time
        wake_label = ttk.Label(adv_frame, text=f"Selected: {wake_options[self.wake_time_var.get()]}")
        wake_label.grid(row=2, column=3, sticky=tk.W, padx=5, pady=5)
        
        # Bind the variable to the update function
        self.wake_time_var.trace_add("write", update_wake_display)
        
        # FEC Mode
        ttk.Label(adv_frame, text="FEC Mode:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=5)
        fec_options = ["FEC Off", "FEC On"]
        ttk.Combobox(adv_frame, textvariable=self.fec_var, values=list(range(len(fec_options))), 
                     state="readonly").grid(row=3, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        ttk.Label(adv_frame, text="Forward Error Correction").grid(row=3, column=2, sticky=tk.W, padx=5, pady=5)
        
        # Create a function to update the displayed FEC mode
        def update_fec_display(*args):
            idx = self.fec_var.get()
            if 0 <= idx < len(fec_options):
                fec_label.config(text=f"Selected: {fec_options[idx]}")
        
        # Add a label to display the selected FEC mode
        fec_label = ttk.Label(adv_frame, text=f"Selected: {fec_options[self.fec_var.get()]}")
        fec_label.grid(row=3, column=3, sticky=tk.W, padx=5, pady=5)
        
        # Bind the variable to the update function
        self.fec_var.trace_add("write", update_fec_display)
        
        # Operating mode explanation
        mode_frame = ttk.LabelFrame(adv_frame, text="Operating Modes")
        mode_frame.grid(row=4, column=0, columnspan=4, sticky=tk.W+tk.E, padx=5, pady=10)
        
        mode_text = """
The E32 module has 4 operating modes controlled by M0 and M1 pins:

Mode 0 (M0=0, M1=0): Normal mode - UART and wireless channels are open for transparent transmission
Mode 1 (M0=1, M1=0): Wake-up mode - Same as mode 0 but adds wake-up code before data
Mode 2 (M0=0, M1=1): Power-saving mode - For receiving only, UART closed until data received
Mode 3 (M0=1, M1=1): Sleep mode - For configuration, uses serial port at 9600 8N1

Note: These modes cannot be changed from this software unless you use GPIO control.
        """
        
        mode_label = ttk.Label(mode_frame, text=mode_text, justify=tk.LEFT, wraplength=700)
        mode_label.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Buttons frame
        button_frame = ttk.Frame(adv_frame)
        button_frame.grid(row=5, column=0, columnspan=4, pady=20)
        
        # Read/Write buttons (same functionality as basic tab)
        ttk.Button(button_frame, text="Read from Module", command=self._read_params).pack(side=tk.LEFT, padx=10)
        ttk.Button(button_frame, text="Write to Module", command=self._write_params).pack(side=tk.LEFT, padx=10)
    
    def _setup_monitor_tab(self):
        """Setup the monitor tab for seeing module status and testing"""
        # Create a frame for the monitor
        monitor_frame = ttk.LabelFrame(self.monitor_tab, text="Module Monitor")
        monitor_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Version info
        ttk.Label(monitor_frame, text="Module Information:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.version_var = tk.StringVar(value="Not available")
        ttk.Label(monitor_frame, textvariable=self.version_var).grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Button(monitor_frame, text="Get Version", command=self._get_version).grid(row=0, column=2, padx=5, pady=5)
        
        # Current parameters display
        param_frame = ttk.LabelFrame(monitor_frame, text="Current Parameters")
        param_frame.grid(row=1, column=0, columnspan=3, sticky=tk.W+tk.E+tk.N+tk.S, padx=5, pady=10)
        
        self.param_text = scrolledtext.ScrolledText(param_frame, height=10, width=60)
        self.param_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Add a button to refresh parameters
        ttk.Button(param_frame, text="Refresh Parameters", command=self._refresh_params_display).pack(pady=5)
        
        # Test transmission frame
        test_frame = ttk.LabelFrame(monitor_frame, text="Test Transmission")
        test_frame.grid(row=2, column=0, columnspan=3, sticky=tk.W+tk.E+tk.N+tk.S, padx=5, pady=10)
        
        # Data to send
        ttk.Label(test_frame, text="Data to send:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.test_data_var = tk.StringVar(value="Hello LoRa!")
        ttk.Entry(test_frame, textvariable=self.test_data_var, width=40).grid(row=0, column=1, sticky=tk.W+tk.E, padx=5, pady=5)
        
        # Send button
        ttk.Button(test_frame, text="Send Data", command=self._send_test_data).grid(row=0, column=2, padx=5, pady=5)
        
        # Received data
        ttk.Label(test_frame, text="Received Data:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.received_text = scrolledtext.ScrolledText(test_frame, height=5, width=60)
        self.received_text.grid(row=2, column=0, columnspan=3, sticky=tk.W+tk.E, padx=5, pady=5)
        
        # Clear button
        ttk.Button(test_frame, text="Clear Received", command=lambda: self.received_text.delete(1.0, tk.END)).grid(row=3, column=0, columnspan=3, pady=5)
        
        # Start/stop receiving
        self.receiving_var = tk.BooleanVar(value=False)
        self.receive_button = ttk.Button(test_frame, text="Start Receiving", command=self._toggle_receiving)
        self.receive_button.grid(row=4, column=0, columnspan=3, pady=5)
    
    def _refresh_ports(self):
        """Refresh the list of available serial ports"""
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combo['values'] = ports
        
        if ports:
            if self.port_var.get() not in ports:
                self.port_var.set(ports[0])
        else:
            self.port_var.set("")
            
    def _toggle_connection(self):
        """Connect to or disconnect from the module"""
        if self.module and self.module.serial and self.module.serial.is_open:
            # Disconnect
            self.module.disconnect()
            self.module = None
            self.connect_button.config(text="Connect")
            self.status_var.set("Disconnected from module")
            
            # Disable tabs
            self.notebook.tab(1, state="disabled")
            self.notebook.tab(2, state="disabled")
            self.notebook.tab(3, state="disabled")
        else:
            # Connect
            port = self.port_var.get()
            baudrate = self.baudrate_var.get()
            
            if not port:
                messagebox.showerror("Error", "No serial port selected")
                return
                
            # Initialize module
            use_gpio = self.use_gpio_var.get()
            manual_config = self.manual_config_var.get()
            m0_pin = self.m0_pin_var.get() if use_gpio else None
            m1_pin = self.m1_pin_var.get() if use_gpio else None
            aux_pin = self.aux_pin_var.get() if use_gpio else None
            
            # Display a reminder for manual configuration mode if needed
            if not use_gpio and manual_config:
                self.status_var.set("Using manual configuration mode (M0=HIGH, M1=HIGH)")
            elif not use_gpio and not manual_config:
                result = messagebox.askquestion("Configuration Mode Reminder", 
                    "You have not selected GPIO control or manual configuration mode.\n\n" +
                    "Have you set M0=HIGH and M1=HIGH manually for configuration mode?",
                    icon='warning')
                if result != 'yes':
                    messagebox.showinfo("Configuration Required", 
                        "Please set M0=HIGH and M1=HIGH manually before connecting.")
                    return
                # User confirmed they set pins manually, so set the flag
                manual_config = True
            
            self.module = E32Module(
                port=port,
                baudrate=baudrate,
                timeout=1,
                m0_pin=m0_pin,
                m1_pin=m1_pin,
                aux_pin=aux_pin,
                use_gpio=use_gpio,
                manual_config=manual_config
            )
            
            if self.module.connect():
                self.connect_button.config(text="Disconnect")
                self.status_var.set(f"Connected to module on {port} at {baudrate} baud")
                
                # Enable tabs
                self.notebook.tab(1, state="normal")
                self.notebook.tab(2, state="normal")
                self.notebook.tab(3, state="normal")
                
                # Try to read parameters
                self._read_params()
            else:
                messagebox.showerror("Error", f"Failed to connect to module on {port}")
                self.module = None
    
    def _read_params(self):
        """Read parameters from the module"""
        if not self.module:
            messagebox.showerror("Error", "Not connected to module")
            return
            
        self.status_var.set("Reading parameters from module...")
        self.master.update()
        
        params = self.module.get_parameters()
        
        if params:
            # Update UI with retrieved parameters
            self.address_var.set(params.get("address", 0))
            self.channel_var.set(params.get("chan", 0))
            self.uart_baud_var.set(params.get("uart_baud", 3))
            self.parity_var.set(params.get("parity", 0))
            self.air_rate_var.set(params.get("air_data_rate", 2))
            self.power_var.set(params.get("transmission_power", 0))
            self.fixed_trans_var.set(params.get("fixed_transmission", 0))
            self.io_drive_var.set(params.get("io_drive_mode", 1))
            self.wake_time_var.set(params.get("wake_up_time", 0))
            self.fec_var.set(params.get("fec", 1))
            
            self.status_var.set("Parameters read successfully")
            self._refresh_params_display()
        else:
            self.status_var.set("Failed to read parameters")
            messagebox.showerror("Error", "Failed to read parameters from module")
    
    def _write_params(self):
        """Write parameters to the module"""
        if not self.module:
            messagebox.showerror("Error", "Not connected to module")
            return
            
        # Validate parameters
        try:
            address = int(self.address_var.get())
            if not 0 <= address <= 65535:
                raise ValueError("Address must be between 0 and 65535")
                
            channel = int(self.channel_var.get())
            if not 0 <= channel <= 83:
                raise ValueError("Channel must be between 0 and 83")
                
        except ValueError as e:
            messagebox.showerror("Parameter Error", str(e))
            return
            
        # Split address into high and low bytes
        addh = (address >> 8) & 0xFF
        addl = address & 0xFF
        
        # Collect parameters
        params = {
            "addh": addh,
            "addl": addl,
            "address": address,
            "chan": self.channel_var.get(),
            "uart_baud": self.uart_baud_var.get(),
            "parity": self.parity_var.get(),
            "air_data_rate": self.air_rate_var.get(),
            "transmission_power": self.power_var.get(),
            "fixed_transmission": self.fixed_trans_var.get(),
            "io_drive_mode": self.io_drive_var.get(),
            "wake_up_time": self.wake_time_var.get(),
            "fec": self.fec_var.get()
        }
        
        # Calculate SPED byte
        sped = (params["parity"] & 0x03) << 6
        sped |= (params["uart_baud"] & 0x07) << 3
        sped |= params["air_data_rate"] & 0x07
        params["sped"] = sped
        
        # Calculate OPTION byte
        option = (params["fixed_transmission"] & 0x01) << 7
        option |= (params["io_drive_mode"] & 0x01) << 6
        option |= (params["wake_up_time"] & 0x07) << 3
        option |= (params["fec"] & 0x01) << 2
        option |= params["transmission_power"] & 0x03
        params["option"] = option
        
        # Ask for confirmation if baudrate is changing from the default 9600
        if params["uart_baud"] != 3:  # Default baud rate index (9600 bps)
            baud_options = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
            if not messagebox.askyesno(
                "Confirm Baud Rate Change", 
                f"You are changing the module's baud rate to {baud_options[params['uart_baud']]} bps.\n\n"
                "If you continue, you may need to disconnect and reconnect at the new baud rate.\n\n"
                "Are you sure you want to continue?"
            ):
                return
                
        self.status_var.set("Writing parameters to module...")
        self.master.update()
        
        if self.module.set_parameters(params):
            self.status_var.set("Parameters written successfully")
            
            # If baudrate changed, notify the user to reconnect
            if params["uart_baud"] != 3:  # Default baud rate index (9600 bps)
                messagebox.showinfo(
                    "Baud Rate Changed",
                    f"The module's baud rate has been changed to {baud_options[params['uart_baud']]} bps.\n\n"
                    "Please disconnect and reconnect at the new baud rate."
                )
                
            # Update the parameter display
            self._refresh_params_display()
        else:
            self.status_var.set("Failed to write parameters")
            messagebox.showerror("Error", "Failed to write parameters to module")
    
    def _reset_module(self):
        """Reset the module"""
        if not self.module:
            messagebox.showerror("Error", "Not connected to module")
            return
            
        if messagebox.askyesno("Confirm Reset", "Are you sure you want to reset the module?"):
            self.status_var.set("Resetting module...")
            self.master.update()
            
            if self.module.reset_module():
                self.status_var.set("Module reset successfully")
                
                # Re-read parameters after reset
                self._read_params()
            else:
                self.status_var.set("Failed to reset module")
                messagebox.showerror("Error", "Failed to reset module")
    
    def _factory_reset(self):
        """Reset the module to factory defaults"""
        if not self.module:
            messagebox.showerror("Error", "Not connected to module")
            return
            
        if messagebox.askyesno(
            "Confirm Factory Reset", 
            "Are you sure you want to reset the module to factory defaults?\n\n"
            "This will erase all custom settings."
        ):
            self.status_var.set("Performing factory reset...")
            self.master.update()
            
            if self.module.factory_reset():
                self.status_var.set("Factory reset successful")
                
                # Re-read parameters after factory reset
                self._read_params()
            else:
                self.status_var.set("Failed to perform factory reset")
                messagebox.showerror("Error", "Failed to perform factory reset")
    
    def _get_version(self):
        """Get module version information"""
        if not self.module:
            messagebox.showerror("Error", "Not connected to module")
            return
            
        self.status_var.set("Getting module version...")
        self.master.update()
        
        version_info = self.module.version()
        
        if version_info:
            version_str = f"Model: E32-{version_info['model']}, Version: {version_info['version']}, Features: {version_info['features']}"
            self.version_var.set(version_str)
            self.status_var.set("Version read successfully")
        else:
            self.status_var.set("Failed to read version")
            messagebox.showerror("Error", "Failed to read module version")
    
    def _refresh_params_display(self):
        """Refresh the parameters display in the monitor tab"""
        if not self.module:
            self.param_text.delete(1.0, tk.END)
            self.param_text.insert(tk.END, "Not connected to module")
            return
            
        params = self.module.get_parameters()
        
        if not params:
            self.param_text.delete(1.0, tk.END)
            self.param_text.insert(tk.END, "Failed to read parameters")
            return
            
        # Format display text
        self.param_text.delete(1.0, tk.END)
        
        # Lists for lookup of human-readable values
        baud_rates = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]
        parity_options = ["8N1", "8O1", "8E1", "8N1 (same as 0)"]
        air_rates = ["0.3k", "1.2k", "2.4k", "4.8k", "9.6k", "19.2k", "19.2k", "19.2k"]
        power_options = ["30 dBm", "27 dBm", "24 dBm", "21 dBm"]
        wake_options = ["250ms", "500ms", "750ms", "1000ms", "1250ms", "1500ms", "1750ms", "2000ms"]
        
        # Format and display parameters
        self.param_text.insert(tk.END, f"Address: {params['address']} (0x{params['address']:04X})\n")
        self.param_text.insert(tk.END, f"Channel: {params['chan']} (Frequency: {915 + params['chan']} MHz)\n")
        
        uart_baud = params['uart_baud']
        if 0 <= uart_baud < len(baud_rates):
            self.param_text.insert(tk.END, f"UART Baud Rate: {baud_rates[uart_baud]} bps\n")
        
        parity = params['parity']
        if 0 <= parity < len(parity_options):
            self.param_text.insert(tk.END, f"UART Parity: {parity_options[parity]}\n")
        
        air_rate = params['air_data_rate']
        if 0 <= air_rate < len(air_rates):
            self.param_text.insert(tk.END, f"Air Data Rate: {air_rates[air_rate]}\n")
        
        power = params['transmission_power']
        if 0 <= power < len(power_options):
            self.param_text.insert(tk.END, f"Transmission Power: {power_options[power]}\n")
        
        self.param_text.insert(tk.END, f"Fixed Transmission: {'Enabled' if params['fixed_transmission'] else 'Disabled'}\n")
        self.param_text.insert(tk.END, f"IO Drive Mode: {'Push-pull' if params['io_drive_mode'] else 'Open-collector'}\n")
        
        wake_time = params['wake_up_time']
        if 0 <= wake_time < len(wake_options):
            self.param_text.insert(tk.END, f"Wake-up Time: {wake_options[wake_time]}\n")
        
        self.param_text.insert(tk.END, f"FEC: {'Enabled' if params['fec'] else 'Disabled'}\n")
        
        # Raw bytes
        self.param_text.insert(tk.END, f"\nRaw Parameter Bytes:\n")
        self.param_text.insert(tk.END, f"ADDH: 0x{params['addh']:02X}\n")
        self.param_text.insert(tk.END, f"ADDL: 0x{params['addl']:02X}\n")
        self.param_text.insert(tk.END, f"SPED: 0x{params['sped']:02X}\n")
        self.param_text.insert(tk.END, f"CHAN: 0x{params['chan']:02X}\n")
        self.param_text.insert(tk.END, f"OPTION: 0x{params['option']:02X}\n")
    
    def _send_test_data(self):
        """Send test data through the module"""
        if not self.module or not self.module.serial or not self.module.serial.is_open:
            messagebox.showerror("Error", "Not connected to module")
            return
            
        data = self.test_data_var.get()
        if not data:
            return
            
        try:
            # Switch to normal mode for transmission
            if self.module.set_mode(ModuleMode.NORMAL):
                # Send the data
                self.module.serial.write(data.encode('utf-8'))
                self.status_var.set(f"Data sent: {data}")
            else:
                self.status_var.set("Failed to set module to normal mode")
                messagebox.showerror("Error", "Failed to set module to normal mode for transmission")
        except Exception as e:
            self.status_var.set(f"Error sending data: {str(e)}")
            messagebox.showerror("Error", f"Failed to send data: {str(e)}")
    
    def _toggle_receiving(self):
        """Toggle receiving mode"""
        if not self.module or not self.module.serial or not self.module.serial.is_open:
            messagebox.showerror("Error", "Not connected to module")
            return
            
        if self.receiving_var.get():
            # Stop receiving
            self.receiving_var.set(False)
            self.receive_button.config(text="Start Receiving")
            self.status_var.set("Stopped receiving")
        else:
            # Start receiving
            self.receiving_var.set(True)
            self.receive_button.config(text="Stop Receiving")
            self.status_var.set("Started receiving")
            
            # Start receive thread
            threading.Thread(target=self._receive_data, daemon=True).start()
    
    def _receive_data(self):
        """Receive data in a separate thread"""
        # Switch to normal mode for reception
        if not self.module.set_mode(ModuleMode.NORMAL):
            self.status_var.set("Failed to set module to normal mode")
            messagebox.showerror("Error", "Failed to set module to normal mode for reception")
            self.receiving_var.set(False)
            self.receive_button.config(text="Start Receiving")
            return
            
        self.module.serial.reset_input_buffer()
        
        while self.receiving_var.get():
            try:
                if self.module.serial.in_waiting:
                    data = self.module.serial.read(self.module.serial.in_waiting)
                    if data:
                        # Try to decode as UTF-8, fall back to hex if not possible
                        try:
                            decoded = data.decode('utf-8')
                        except UnicodeDecodeError:
                            decoded = f"HEX: {data.hex()}"
                            
                        # Add timestamp
                        timestamp = time.strftime("%H:%M:%S")
                        
                        # Update the text widget in thread-safe way
                        self.master.after(0, self._update_received_text, f"[{timestamp}] {decoded}\n")
            except Exception as e:
                self.master.after(0, self._update_status, f"Error receiving data: {str(e)}")
                break
                
            time.sleep(0.1)
    
    def _update_received_text(self, text):
        """Update received text in a thread-safe way"""
        self.received_text.insert(tk.END, text)
        self.received_text.see(tk.END)
    
    def _update_status(self, text):
        """Update status bar in a thread-safe way"""
        self.status_var.set(text)
    
    def _load_config(self):
        """Load configuration from file"""
        filename = filedialog.askopenfilename(
            title="Load Configuration File",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        
        if not filename:
            return
            
        try:
            with open(filename, 'r') as f:
                config = json.load(f)
                
            # Update UI with loaded parameters
            if "address" in config:
                self.address_var.set(config["address"])
            
            if "chan" in config:
                self.channel_var.set(config["chan"])
            
            if "uart_baud" in config:
                self.uart_baud_var.set(config["uart_baud"])
            
            if "parity" in config:
                self.parity_var.set(config["parity"])
            
            if "air_data_rate" in config:
                self.air_rate_var.set(config["air_data_rate"])
            
            if "transmission_power" in config:
                self.power_var.set(config["transmission_power"])
            
            if "fixed_transmission" in config:
                self.fixed_trans_var.set(config["fixed_transmission"])
            
            if "io_drive_mode" in config:
                self.io_drive_var.set(config["io_drive_mode"])
            
            if "wake_up_time" in config:
                self.wake_time_var.set(config["wake_up_time"])
            
            if "fec" in config:
                self.fec_var.set(config["fec"])
                
            self.status_var.set(f"Configuration loaded from {os.path.basename(filename)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load configuration: {e}")
    
    def _save_config(self):
        """Save configuration to file"""
        filename = filedialog.asksaveasfilename(
            title="Save Configuration File",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        
        if not filename:
            return
            
        try:
            address = int(self.address_var.get())
            addh = (address >> 8) & 0xFF
            addl = address & 0xFF
            
            config = {
                "address": address,
                "addh": addh,
                "addl": addl,
                "chan": self.channel_var.get(),
                "uart_baud": self.uart_baud_var.get(),
                "parity": self.parity_var.get(),
                "air_data_rate": self.air_rate_var.get(),
                "transmission_power": self.power_var.get(),
                "fixed_transmission": self.fixed_trans_var.get(),
                "io_drive_mode": self.io_drive_var.get(),
                "wake_up_time": self.wake_time_var.get(),
                "fec": self.fec_var.get()
            }
            
            # Calculate SPED byte
            sped = (config["parity"] & 0x03) << 6
            sped |= (config["uart_baud"] & 0x07) << 3
            sped |= config["air_data_rate"] & 0x07
            config["sped"] = sped
            
            # Calculate OPTION byte
            option = (config["fixed_transmission"] & 0x01) << 7
            option |= (config["io_drive_mode"] & 0x01) << 6
            option |= (config["wake_up_time"] & 0x07) << 3
            option |= (config["fec"] & 0x01) << 2
            option |= config["transmission_power"] & 0x03
            config["option"] = option
            
            with open(filename, 'w') as f:
                json.dump(config, f, indent=4)
                
            self.status_var.set(f"Configuration saved to {os.path.basename(filename)}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save configuration: {e}")
    
    def _on_close(self):
        """Clean up when window is closed"""
        if self.module:
            self.module.disconnect()
        self.master.destroy()


class E32CLI:
    """
    Command-line interface for configuring the E32 module
    """
    def __init__(self, args):
        self.args = args
        self.module = None
        
    def run(self):
        """Run the CLI based on arguments"""
        # Connect to the module
        self.module = E32Module(
            port=self.args.port,
            baudrate=self.args.baudrate,
            timeout=1,
            m0_pin=self.args.m0_pin,
            m1_pin=self.args.m1_pin,
            aux_pin=self.args.aux_pin,
            use_gpio=self.args.use_gpio
        )
        
        if not self.module.connect():
            logger.error(f"Failed to connect to module on {self.args.port}")
            return 1
            
        logger.info(f"Connected to module on {self.args.port} at {self.args.baudrate} baud")
        
        try:
            # Handle command
            if self.args.command == 'read':
                self._read_params()
            elif self.args.command == 'write':
                self._write_params()
            elif self.args.command == 'reset':
                self._reset_module()
            elif self.args.command == 'factory-reset':
                self._factory_reset()
            elif self.args.command == 'version':
                self._get_version()
            elif self.args.command == 'save-config':
                self._save_config()
            elif self.args.command == 'load-config':
                self._load_config()
            elif self.args.command == 'scan-ports':
                self._scan_ports()
            elif self.args.command == 'send-data':
                self._send_data()
            else:
                logger.error(f"Unknown command: {self.args.command}")
                return 1
                
            return 0
        finally:
            self.module.disconnect()
            
    def _read_params(self):
        """Read and display module parameters"""
        logger.info("Reading parameters from module...")
        
        params = self.module.get_parameters()
        
        if params:
            logger.info("Module parameters:")
            
            # Format parameters for display
            baud_rates = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]
            parity_options = ["8N1", "8O1", "8E1", "8N1 (same as 0)"]
            air_rates = ["0.3k", "1.2k", "2.4k", "4.8k", "9.6k", "19.2k", "19.2k", "19.2k"]
            power_options = ["30 dBm", "27 dBm", "24 dBm", "21 dBm"]
            wake_options = ["250ms", "500ms", "750ms", "1000ms", "1250ms", "1500ms", "1750ms", "2000ms"]
            
            # Print formatted parameters
            print(f"Address:              {params['address']} (0x{params['address']:04X})")
            print(f"Channel:              {params['chan']} ({915 + params['chan']}MHz)")
            
            if 0 <= params['uart_baud'] < len(baud_rates):
                print(f"UART Baud Rate:       {baud_rates[params['uart_baud']]} bps")
            
            if 0 <= params['parity'] < len(parity_options):
                print(f"UART Parity:          {parity_options[params['parity']]}")
            
            if 0 <= params['air_data_rate'] < len(air_rates):
                print(f"Air Data Rate:        {air_rates[params['air_data_rate']]}")
            
            if 0 <= params['transmission_power'] < len(power_options):
                print(f"Transmission Power:   {power_options[params['transmission_power']]}")
            
            print(f"Fixed Transmission:   {'Enabled' if params['fixed_transmission'] else 'Disabled'}")
            print(f"IO Drive Mode:        {'Push-pull' if params['io_drive_mode'] else 'Open-collector'}")
            
            if 0 <= params['wake_up_time'] < len(wake_options):
                print(f"Wake-up Time:         {wake_options[params['wake_up_time']]}")
            
            print(f"FEC:                  {'Enabled' if params['fec'] else 'Disabled'}")
            
            # Raw bytes
            print(f"\nRaw Parameter Bytes:")
            print(f"ADDH:                 0x{params['addh']:02X}")
            print(f"ADDL:                 0x{params['addl']:02X}")
            print(f"SPED:                 0x{params['sped']:02X}")
            print(f"CHAN:                 0x{params['chan']:02X}")
            print(f"OPTION:               0x{params['option']:02X}")
            
            if self.args.output:
                # Save to file
                try:
                    with open(self.args.output, 'w') as f:
                        json.dump(params, f, indent=4)
                    logger.info(f"Parameters saved to {self.args.output}")
                except Exception as e:
                    logger.error(f"Failed to save parameters to file: {e}")
        else:
            logger.error("Failed to read parameters from module")
            return 1
            
        return 0
    
    def _write_params(self):
        """Write parameters to module"""
        # Load parameters from file if specified
        if self.args.input:
            try:
                with open(self.args.input, 'r') as f:
                    params = json.load(f)
                logger.info(f"Parameters loaded from {self.args.input}")
            except Exception as e:
                logger.error(f"Failed to load parameters from file: {e}")
                return 1
        else:
            # Collect parameters from command line
            params = {}
            
            if self.args.address is not None:
                params["address"] = self.args.address
                
            if self.args.channel is not None:
                params["chan"] = self.args.channel
                
            if self.args.uart_baud is not None:
                params["uart_baud"] = self.args.uart_baud
                
            if self.args.parity is not None:
                params["parity"] = self.args.parity
                
            if self.args.air_rate is not None:
                params["air_data_rate"] = self.args.air_rate
                
            if self.args.power is not None:
                params["transmission_power"] = self.args.power
                
            if self.args.fixed_trans is not None:
                params["fixed_transmission"] = 1 if self.args.fixed_trans else 0
                
            if self.args.io_drive is not None:
                params["io_drive_mode"] = 1 if self.args.io_drive else 0
                
            if self.args.wake_time is not None:
                params["wake_up_time"] = self.args.wake_time
                
            if self.args.fec is not None:
                params["fec"] = 1 if self.args.fec else 0
        
        # Process address into high and low bytes if needed
        if "address" in params and not "addh" in params and not "addl" in params:
            address = params["address"]
            params["addh"] = (address >> 8) & 0xFF
            params["addl"] = address & 0xFF
        
        # Check if we have parameters to write
        if not params:
            logger.error("No parameters specified to write")
            return 1
            
        logger.info("Writing parameters to module...")
        
        if self.module.set_parameters(params):
            logger.info("Parameters written successfully")
            return 0
        else:
            logger.error("Failed to write parameters to module")
            return 1
    
    def _reset_module(self):
        """Reset the module"""
        logger.info("Resetting module...")
        
        if self.module.reset_module():
            logger.info("Module reset successfully")
            return 0
        else:
            logger.error("Failed to reset module")
            return 1
    
    def _factory_reset(self):
        """Reset the module to factory defaults"""
        logger.info("Performing factory reset...")
        
        if self.module.factory_reset():
            logger.info("Factory reset successful")
            return 0
        else:
            logger.error("Failed to perform factory reset")
            return 1
    
    def _get_version(self):
        """Get module version information"""
        logger.info("Getting module version...")
        
        version_info = self.module.version()
        
        if version_info:
            print(f"Module: E32-{version_info['model']}")
            print(f"Version: {version_info['version']}")
            print(f"Features: {version_info['features']}")
            return 0
        else:
            logger.error("Failed to get module version")
            return 1
    
    def _save_config(self):
        """Save current module configuration to file"""
        if not self.args.output:
            logger.error("No output file specified")
            return 1
            
        logger.info("Reading parameters from module...")
        params = self.module.get_parameters()
        
        if not params:
            logger.error("Failed to read parameters from module")
            return 1
            
        try:
            with open(self.args.output, 'w') as f:
                json.dump(params, f, indent=4)
            logger.info(f"Configuration saved to {self.args.output}")
            return 0
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            return 1
    
    def _load_config(self):
        """Load and apply configuration from file"""
        if not self.args.input:
            logger.error("No input file specified")
            return 1
            
        try:
            with open(self.args.input, 'r') as f:
                params = json.load(f)
                
            logger.info(f"Configuration loaded from {self.args.input}")
            
            if self.module.set_parameters(params):
                logger.info("Parameters applied successfully")
                return 0
            else:
                logger.error("Failed to apply parameters to module")
                return 1
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            return 1
    
    def _scan_ports(self):
        """Scan and display available serial ports"""
        ports = serial.tools.list_ports.comports()
        
        if not ports:
            logger.info("No serial ports found")
        else:
            logger.info("Available serial ports:")
            for port in ports:
                print(f"- {port.device}: {port.description}")
                
        return 0
    
    def _send_data(self):
        """Send data through the module"""
        if not self.args.data:
            logger.error("No data specified to send")
            return 1
            
        logger.info(f"Sending data: {self.args.data}")
        
        # Switch to normal mode for transmission
        if not self.module.set_mode(ModuleMode.NORMAL):
            logger.error("Failed to set module to normal mode")
            return 1
            
        try:
            self.module.serial.write(self.args.data.encode('utf-8'))
            logger.info("Data sent successfully")
            return 0
        except Exception as e:
            logger.error(f"Failed to send data: {e}")
            return 1


def setup_arg_parser():
    """Set up the argument parser for CLI mode"""
    parser = argparse.ArgumentParser(description='E32-915MHz LoRa Module Configurator')
    
    # Global options
    parser.add_argument('--cli', action='store_true', help='Run in command-line mode')
    parser.add_argument('--port', help='Serial port to use (e.g., COM3, /dev/ttyUSB0)')
    parser.add_argument('--baudrate', type=int, default=9600, help='Serial baudrate (default: 9600)')
    parser.add_argument('--use-gpio', action='store_true', help='Use GPIO pins for mode control (Raspberry Pi)')
    parser.add_argument('--m0-pin', type=int, help='GPIO pin number for M0 pin')
    parser.add_argument('--m1-pin', type=int, help='GPIO pin number for M1 pin')
    parser.add_argument('--aux-pin', type=int, help='GPIO pin number for AUX pin')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # read command
    read_parser = subparsers.add_parser('read', help='Read module parameters')
    read_parser.add_argument('--output', '-o', help='Save parameters to file')
    
    # write command
    write_parser = subparsers.add_parser('write', help='Write module parameters')
    write_parser.add_argument('--input', '-i', help='Load parameters from file')
    write_parser.add_argument('--address', type=int, help='Module address (0-65535)')
    write_parser.add_argument('--channel', type=int, help='Channel (0-83)')
    write_parser.add_argument('--uart-baud', type=int, help='UART baudrate index (0-7)')
    write_parser.add_argument('--parity', type=int, help='UART parity (0-3)')
    write_parser.add_argument('--air-rate', type=int, help='Air rate index (0-7)')
    write_parser.add_argument('--power', type=int, help='Transmit power index (0-3)')
    write_parser.add_argument('--fixed-trans', type=bool, help='Fixed transmission mode (True/False)')
    write_parser.add_argument('--io-drive', type=bool, help='IO drive mode (True=Push-Pull, False=Open-Collector)')
    write_parser.add_argument('--wake-time', type=int, help='Wake-up time index (0-7)')
    write_parser.add_argument('--fec', type=bool, help='FEC enable (True/False)')
    
    # reset command
    subparsers.add_parser('reset', help='Reset the module')
    
    # factory-reset command
    subparsers.add_parser('factory-reset', help='Reset the module to factory defaults')
    
    # version command
    subparsers.add_parser('version', help='Get module version information')
    
    # save-config command
    save_parser = subparsers.add_parser('save-config', help='Save current module configuration to file')
    save_parser.add_argument('--output', '-o', required=True, help='Output file')
    
    # load-config command
    load_parser = subparsers.add_parser('load-config', help='Load and apply configuration from file')
    load_parser.add_argument('--input', '-i', required=True, help='Input file')
    
    # scan-ports command
    subparsers.add_parser('scan-ports', help='Scan and display available serial ports')
    
    # send-data command
    send_parser = subparsers.add_parser('send-data', help='Send data through the module')
    send_parser.add_argument('--data', required=True, help='Data to send')
    
    return parser

def main():
    """Main entry point"""
    parser = setup_arg_parser()
    args = parser.parse_args()
    
    # Set up logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Run in CLI mode if requested
    if args.cli:
        if not args.command:
            parser.print_help()
            return 1
            
        if args.command != 'scan-ports' and not args.port:
            logger.error("Serial port must be specified (--port)")
            return 1
            
        cli = E32CLI(args)
        return cli.run()
    else:
        # Run in GUI mode
        if not HAS_GUI:
            logger.error("GUI dependencies not available. Please install tkinter.")
            return 1
            
        root = tk.Tk()
        app = E32ConfigGUI(root)
        root.mainloop()
        return 0

if __name__ == "__main__":
    sys.exit(main())