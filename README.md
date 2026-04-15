# Serial Plotter + Terminal

A small Python desktop tool that combines three things in a single window:

- a **serial terminal**
- a **live serial plotter**
- a set of **5 persistent quick-send commands**

It is designed for debugging and monitoring embedded devices that send both:

- regular textual messages for the terminal, and
- numeric telemetry lines for plotting, identified by a configurable prefix.

The application is built with **Tkinter** for the UI, **Matplotlib** for plotting, and **pyserial** for serial communication.

Its main goal is to provide a practical replacement for a basic serial terminal plus a simple plotter, with a workflow that is especially useful during firmware development and lab testing.

This README was prepared from the actual script behavior and command-line options defined in `serial_plotter_terminal.py`. fileciteturn0file0

---

## Features

- **Serial terminal view**
  - shows all incoming serial lines that do **not** start with the plot prefix
  - supports local echo for sent commands

- **Live plotter**
  - plots only lines that start with a configurable prefix
  - supports labeled key/value pairs and plain numeric lists
  - keeps a rolling time window

- **Auto reconnect**
  - the app can start even if the serial port is not available
  - it automatically retries the connection
  - if the cable is unplugged during runtime, the app goes offline and reconnects when the port comes back

- **Quick Send panel**
  - 5 editable command slots
  - each command has its own send button
  - commands are saved to a JSON file and restored on startup

- **Start / Stop / Clear plot controls**
  - stop pauses plotting without closing the serial connection
  - clear resets the plotted data and time base

---

## Requirements

- Python 3
- `pyserial`
- `matplotlib`
- Tkinter support in your Python installation

## Install dependencies

```bash
pip install pyserial matplotlib
```

On many systems, Tkinter is already included with Python.  
If it is missing on Linux, install the appropriate OS package.

Examples:

### Debian / Ubuntu

```bash
sudo apt install python3-tk
```

### Fedora

```bash
sudo dnf install python3-tkinter
```

---

## How the tool works

The program separates incoming serial lines into two channels:

### 1. Plot channel
Only lines starting with the configured prefix are sent to the plotter.

Default prefix:

```text
@
```

Example plot lines:

```text
@T:90.00 M:89.91
@T=90.00,M=89.91
@1,2,3
@1 2 3
```

Supported plot formats:

- **labeled numeric pairs**
  - `T:90.00 M:89.91`
  - `T=90.00,M=89.91`

- **plain numeric lists**
  - `1,2,3`
  - `1 2 3`
  - `1;2;3`

When the line is a numeric list without labels, the script automatically names the series:

- `v0`
- `v1`
- `v2`
- ...

### 2. Terminal channel
Any line that does **not** start with the plot prefix is shown in the terminal area.

Example terminal lines:

```text
Booting...
Motor ready
Command accepted
```

This makes it easy to keep debug text and numeric telemetry separated while using the same serial stream.

---

## Command-line usage

## Basic syntax

```bash
python3 serial_plotter_terminal.py --port PORT [options]
```

`--port` is required.

Example:

```bash
python3 serial_plotter_terminal.py --port /dev/ttyACM0
```

---

## Command-line options

### `--port`
Serial port name to open.

This argument is required.

Examples:

```bash
--port /dev/ttyACM0
--port /dev/ttyUSB0
--port COM5
```

---

### `--baud`
Serial baud rate.

Default:

```text
115200
```

Example:

```bash
--baud 921600
```

---

### `--window`
Length of the rolling plot window, in seconds.

Default:

```text
10.0
```

If set to `20`, the plot shows only the most recent 20 seconds of data.

Example:

```bash
--window 20
```

---

### `--max-series`
Maximum number of plotted series that can be created.

Default:

```text
16
```

This limit prevents the UI from creating an excessive number of traces if incoming data labels change continuously.

Example:

```bash
--max-series 8
```

---

### `--prefix`
Prefix used to identify plot lines.

Default:

```text
@
```

Only lines beginning with this prefix are parsed for plotting.  
All other lines go to the terminal.

Example:

```bash
--prefix "##"
```

If you use this option, a plot line might look like:

```text
## T:90.0 M:89.9
```

---

### `--ylim-auto`
Enables continuous automatic Y-axis scaling.

Default:

```text
enabled
```

The script uses Python's `BooleanOptionalAction`, so you can explicitly disable it with:

```bash
--no-ylim-auto
```

Examples:

```bash
python3 serial_plotter_terminal.py --port /dev/ttyACM0 --ylim-auto
python3 serial_plotter_terminal.py --port /dev/ttyACM0 --no-ylim-auto
```

---

### `--quick-file`
Path of the JSON file used to store the 5 quick commands.

Default:

```text
serial_plotter_terminal.quick.json
```

Example:

```bash
--quick-file my_quick_commands.json
```

The file is automatically created or updated as you edit the quick command fields.

---

### `--retry`
Reconnect retry interval, in seconds.

Default:

```text
1.0
```

This controls how often the background serial thread retries opening the port when the device is offline or disconnected.

Example:

```bash
--retry 2.5
```

---

## Full example commands

### Linux example

```bash
python3 serial_plotter_terminal.py \
  --port /dev/ttyACM0 \
  --baud 115200 \
  --window 15 \
  --max-series 8 \
  --prefix @ \
  --quick-file serial_plotter_terminal.quick.json \
  --retry 1.0
```

### Windows example

```bash
python serial_plotter_terminal.py --port COM5 --baud 115200 --window 15
```

### Using a custom plot prefix

```bash
python3 serial_plotter_terminal.py --port /dev/ttyUSB0 --prefix "##"
```

### Disabling Y autoscale

```bash
python3 serial_plotter_terminal.py --port /dev/ttyUSB0 --no-ylim-auto
```

---

## User interface overview

The main window contains:

### Top bar
- **Start**: resume plotting after a stop
- **Stop**: pause plotting
- **Clear**: clear plotted data and reset the time base
- **Status label**: shows online/offline state
- **Semaphore indicator**:
  - green = serial connected
  - red = offline

### Center area
- the Matplotlib live plot

### Bottom area
- a terminal text box for non-plot serial messages
- a command entry field to send one-line commands
- a **Quick Send** section with 5 stored commands

---

## Sending commands

### Main input field
Type a command in the bottom entry field and press `Enter`.

The command is:

- echoed locally in the terminal as `> your_command`
- sent over the serial port with a trailing newline

If the serial port is offline, the command is not sent and the terminal shows:

```text
[offline] not sent (serial not connected)
```

### Quick Send buttons
Each quick command row contains:

- an editable text field
- a `Send N` button

The content is saved automatically to the JSON quick-command file.

You can also use `Ctrl+Enter` while focused on a quick command field to send that slot.

---

## Plot data format details

The parser accepts the following input styles after the prefix has been removed.

### Labeled values with `:`

```text
@T:90.00 M:89.91
```

Result:
- series `T` gets `90.00`
- series `M` gets `89.91`

### Labeled values with `=`

```text
@T=90.00,M=89.91
```

Result:
- series `T` gets `90.00`
- series `M` gets `89.91`

### Plain numeric lists

```text
@1,2,3
```

or

```text
@1 2 3
```

or

```text
@1;2;3
```

Result:
- `v0 = 1`
- `v1 = 2`
- `v2 = 3`

### Invalid plot lines
If a prefixed line cannot be parsed as numeric telemetry, it is ignored for plotting.

---

## Auto-reconnect behavior

The serial communication runs in a background thread that continuously manages the port.

Behavior:

- if the app starts and the port is missing, the UI still opens
- the status remains offline
- the thread retries opening the port every `--retry` seconds
- when the device appears, the app goes online automatically
- if the device disconnects during runtime, the app switches offline and retries again

This is especially useful when working with microcontroller boards that reset, reboot, or briefly disconnect after flashing.

---

## Quick command persistence

Quick commands are stored as JSON.

Default file:

```text
serial_plotter_terminal.quick.json
```

Example content:

```json
{
  "quick": [
    "status",
    "help",
    "motor on",
    "motor off",
    "plot enable"
  ]
}
```

The file is written automatically after editing the quick-command fields.

---

## Typical embedded workflow example

A device could send mixed output like this:

```text
Boot complete
Ready
@T:90.00 M:89.91
@T:90.00 M:89.94
Command OK
@T:90.00 M:89.97
```

In the application:

- `Boot complete`, `Ready`, and `Command OK` appear in the terminal
- `T` and `M` are plotted in real time

This is very handy for:
- PID tuning
- motion control debugging
- sensor monitoring
- actuator feedback comparison
- serial command testing

---

## Notes and limitations

- The UI uses the **TkAgg** Matplotlib backend, so it requires a graphical desktop environment.
- The script is intended for line-based serial streams.
- Plotting is based on the local PC time at the moment each line is received.
- If more unique series names arrive than allowed by `--max-series`, additional series are ignored.
- Stopping the plot does not stop the serial reader; it only discards incoming plot samples until plotting is resumed.

---

## Suggested use cases

- debugging firmware over UART/USB serial
- plotting control loop variables
- monitoring sensor values in real time
- sending repetitive test commands with one click
- working with devices that may disconnect and reconnect frequently

---

## License

Add the license that matches your project, for example:

- MIT
- Apache-2.0
- GPL-3.0

If this script is part of a larger repository, align this section with the repository license.
