# Pipecat Dictation - Window Control System

## Overview
This repository contains a sophisticated window control system for sending keyboard and mouse input to specific application windows. It's designed for voice dictation systems and automation, with robust multi-window management and persistence.

## Key Features

### Multi-Window Memory System
- **Remember Multiple Windows**: Save any number of application windows with custom names
- **Persistent Storage**: Window configurations are saved to `~/.pipecat-dictation/window_memory.json`
- **Smart Focus Management**: Automatically tracks most recently used window
- **Window Targeting**: Send input to any saved window by name

### Platform Support

#### Linux/Wayland
- **Primary Tool**: `ydotool` (version 0.1.8)
- **Window Detection**: GNOME Shell Extensions (Windows/WindowsExt) via gdbus
- **Position Calculation**: Uses exact window geometry to calculate center position
- **Why**: Wayland's security model requires kernel-level input injection

#### Linux/X11  
- **Input Tool**: `pynput` or `ydotool`
- **Window Detection**: `xdotool` for window information
- **Why**: X11 allows traditional window management approaches

#### macOS
- **Tool**: `pynput` library
- **Why**: Native Python library with excellent macOS support

## Installation Requirements

### Linux/Wayland
1. Install ydotool: `sudo apt install ydotool`
2. Install GNOME extension: "Window Calls" or "Window Calls Extended"
3. Add user to input group: `sudo usermod -a -G input $USER`
4. Log out and back in for group changes

### macOS
- No additional setup required (pynput works out of the box)

## Usage

### CLI Commands

```bash
# List all saved windows
python window_control.py list
python window_control.py  # (defaults to list)

# Add/update a window (will count down 3 seconds)
python window_control.py add "my editor"
python window_control.py add "terminal" --wait 5  # Wait 5 seconds

# Remove a saved window
python window_control.py remove "my editor"

# Focus a specific window
python window_control.py focus "my editor"
python window_control.py focus  # Focus last used

# Test sending text to a window
python window_control.py test "my editor"
python window_control.py test  # Test last used
```

### Python API

```python
from window_control import WindowController

# Create controller (auto-loads saved windows)
controller = WindowController()

# Add a new window
controller.remember_window("my editor", wait_seconds=3)

# Send text to a specific window
controller.send_keystrokes_to_window("Hello, World!", "my editor")
controller.send_key_to_window("enter", "my editor")

# Send to last used window (default)
controller.send_keystrokes_to_window("Quick note")

# Restore mouse position after sending
controller.send_keystrokes_to_window("Text", "editor", restore_mouse=True)

# Focus a window programmatically
controller.focus_window("my editor")

# List all windows
controller.list_windows()
```

## Architecture

### Window Information Storage
```python
@dataclass
class WindowInfo:
    position: Tuple[int, int]      # Center position of window
    title: Optional[str]            # Window title
    window_id: Optional[str]        # Platform-specific ID
    wm_class: Optional[str]         # Application class
    pid: Optional[int]              # Process ID
    last_used: Optional[float]      # Timestamp of last use
    geometry: Optional[Dict]        # x, y, width, height
```

### Cache File Format
Located at `~/.pipecat-dictation/window_memory.json`:
```json
{
  "windows": {
    "editor": {
      "position": [1920, 540],
      "title": "Code Editor - main.py",
      "window_id": "123456789",
      "wm_class": "code",
      "pid": 1234,
      "last_used": 1234567890.123,
      "geometry": {
        "x": 1440,
        "y": 0,
        "width": 960,
        "height": 1080
      }
    }
  },
  "last_used": "editor",
  "updated": "2024-01-01T12:00:00"
}
```

## How It Works

### On Wayland (GNOME)
1. **Window Detection**: Uses gdbus to call GNOME Shell Extensions
   - `Windows.List` - Get all windows with focus state
   - `Windows.Details` - Get exact geometry of specific window
2. **Position Calculation**: Computes window center from geometry (not mouse position)
3. **Focus Method**: Uses ydotool to move mouse to center and click
4. **Input Injection**: ydotool types at kernel level via /dev/uinput

### Window Focus Logic
1. If window name specified → use that window
2. Else if last_used exists → use last used window  
3. Else → use first window in map
4. Updates last_used timestamp after each focus

## Important Notes

### ydotool Version Compatibility
- Written for ydotool 0.1.8
- Uses lowercase key names (`enter`, not `Return`)
- Key delay of 20ms for reliable typing

### GNOME Extensions Required
On Wayland, requires one of:
- "Window Calls" extension
- "Window Calls Extended" extension
These provide the gdbus interface for window information.

### Security Considerations
- ydotool requires access to `/dev/uinput` (kernel input)
- User must be in `input` group
- Can send input to any application - use responsibly

## Troubleshooting

### "ydotoold backend unavailable"
This warning is normal - ydotool works without the daemon, just with slight latency.

### Windows not focusing on Wayland
1. Check GNOME extension is installed and enabled
2. Verify with: `gdbus call --session --dest=org.gnome.Shell --object-path=/org/gnome/Shell/Extensions/Windows --method=org.gnome.Shell.Extensions.Windows.List`

### Cache file issues
- Location: `~/.pipecat-dictation/window_memory.json`
- Delete to reset all saved windows
- Manually editable JSON if needed

## Files

- `window_control.py` - Main module with CLI and API
- `window_controller_enhanced.py` - Previous single-window version (deprecated)
- `setup_wayland_terminal.sh` - Setup script for ydotool installation
- `test_window_control.py` - Basic test script

## Future Enhancements
- Window validation (check if saved windows still exist)
- Support for other Wayland compositors (Sway, KDE)
- Window group management
- Keyboard shortcut integration