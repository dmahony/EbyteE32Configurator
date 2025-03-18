# E32-915MHz LoRa Module Configurator

A cross-platform application for configuring EBYTE E32 series LoRa modules, specifically optimized for the 915MHz version. This tool provides both graphical (GUI) and command line (CLI) interfaces to make module configuration easy and accessible.

![E32 Module](https://github.com/dmahony/EbyteE32Configurator/raw/main/images/e32_module.jpg)

## Features

- **Dual Interfaces**: Choose between an intuitive GUI or powerful CLI
- **Manual Pin Configuration**: Works with modules where M0/M1 pins are manually configured
- **Complete Parameter Management**: Configure all module settings defined in the E32 manual
- **Real-time Communication Testing**: Send and receive data directly from the application
- **Configuration Storage**: Save and load configurations for quick setup
- **Hardware Control**: Optional GPIO control for automatic mode switching (Raspberry Pi)

## Installation

### Prerequisites

- Python 3.6 or higher
- Required packages: `pyserial`
- Optional: `tkinter` for GUI (typically included with Python)

### Setup

1. Clone this repository:
   ```
   git clone https://github.com/dmahony/EbyteE32Configurator.git
   ```

2. Install dependencies:
   ```
   pip install pyserial
   ```

3. For Raspberry Pi GPIO control (optional):
   ```
   pip install RPi.GPIO
   ```

## Usage

### Graphical Interface (GUI)

Run the application without arguments to use the graphical interface:

```
python e32_configurator.py
```

The GUI provides:
- Easy connection management
- Visual parameter configuration
- Testing tools for sending/receiving data
- Configuration saving/loading

### Command Line Interface (CLI)

Use the `--cli` argument followed by specific commands:

```
# Scan available serial ports
python e32_configurator.py --cli scan-ports

# Read module parameters
python e32_configurator.py --cli read --port COM3

# Write parameters
python e32_configurator.py --cli write --port COM3 --address 1 --channel 15

# Save configuration to file
python e32_configurator.py --cli save-config --port COM3 --output config.json
```

Run with `--help` to see all available options:

```
python e32_configurator.py --cli --help
```

## Configuration Parameters

### Basic Settings

- **Module Address**: 0-65535 (unique identifier)
- **Channel**: 0-83 (determines frequency: 915MHz + Channel*1MHz)
- **UART Baud Rate**: 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200 bps
- **UART Parity**: 8N1, 8O1, 8E1
- **Air Data Rate**: 0.3k, 1.2k, 2.4k, 4.8k, 9.6k, 19.2k bps
- **Transmission Power**: 10dBm, 13dBm, 17dBm, 20dBm (or 21dBm, 24dBm, 27dBm, 30dBm depending on module version)

### Advanced Settings

- **Fixed Transmission**: Transparent vs. fixed point addressing
- **IO Drive Mode**: Push-pull output vs. open-collector output
- **Wake-up Time**: 250ms to 2000ms
- **FEC (Forward Error Correction)**: Enabled/Disabled

## Hardware Connection

Connect your E32-915MHz module to your computer using a USB-to-serial adapter:

| E32 Module | USB-to-Serial Adapter |
|------------|------------------------|
| M0         | HIGH for config mode   |
| M1         | HIGH for config mode   |
| TXD        | RXD                    |
| RXD        | TXD                    |
| AUX        | Optional               |
| VCC        | 3.3V-5.0V              |
| GND        | GND                    |

![Wiring Diagram](https://github.com/dmahony/EbyteE32Configurator/raw/main/images/wiring_diagram.jpg)

### Configuration Mode

The E32 module has four operating modes:

| Mode | M0  | M1  | Description                        |
|------|-----|-----|------------------------------------|
| 0    | LOW | LOW | Normal operation                   |
| 1    | HIGH| LOW | Wake-up mode                       |
| 2    | LOW | HIGH| Power-saving mode                  |
| 3    | HIGH| HIGH| Configuration mode (for this tool) |

For using this configurator, ensure the module is in **Mode 3** (both M0 and M1 set HIGH).

## Troubleshooting

### Cannot Connect to Module

- Verify the module is properly powered (3.3V-5.0V)
- Ensure M0 and M1 pins are both set HIGH for configuration mode
- Check that you've selected the correct COM port
- Try a different baud rate (default is 9600)

### Invalid Response When Reading Parameters

- Make sure the module is in configuration mode (M0=HIGH, M1=HIGH)
- Check your wiring (TXD→RXD, RXD→TXD)
- Ensure the module is properly powered

### Module Not Responding to Commands

- Reset the module by cycling power
- Verify signal levels (some USB-to-TTL adapters use 5V logic which might be too high)
- Add a short delay (100-200ms) between commands

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Based on the official E32 Series User Manual from EBYTE
- Inspired by the E220 configurator design

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
