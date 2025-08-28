"""
Enhanced window control module with multi-window memory and persistence.
Supports remembering multiple windows and directing input to any of them.
"""

import os
import json
import platform
import time
import subprocess
import argparse
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from pynput import keyboard, mouse
from pynput.keyboard import Key, Controller as KeyboardController
from pynput.mouse import Button, Controller as MouseController

# Configurable cache file location
CACHE_DIR = Path.home() / ".pipecat-dictation"
CACHE_FILE = CACHE_DIR / "window_memory.json"


@dataclass
class WindowInfo:
    """Store information about a remembered window."""
    position: Tuple[int, int]  # Center position of window
    title: Optional[str] = None
    window_id: Optional[str] = None
    wm_class: Optional[str] = None
    pid: Optional[int] = None
    last_used: Optional[float] = None
    geometry: Optional[Dict[str, int]] = None  # x, y, width, height

    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            "position": list(self.position),
            "title": self.title,
            "window_id": self.window_id,
            "wm_class": self.wm_class,
            "pid": self.pid,
            "last_used": self.last_used,
            "geometry": self.geometry
        }
    
    @classmethod
    def from_dict(cls, data):
        """Create from dictionary loaded from JSON."""
        data["position"] = tuple(data["position"])
        return cls(**data)


def get_platform():
    """Detect the current platform."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    elif system == "Linux":
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            return "linux_wayland"
        else:
            return "linux_x11"
    else:
        return "unknown"


def is_ydotool_available():
    """Check if ydotool is available and working."""
    try:
        result = subprocess.run(["which", "ydotool"], capture_output=True, check=False)
        if result.returncode != 0:
            return False
        
        result = subprocess.run(
            ["ydotool", "key", "--help"], 
            capture_output=True, 
            check=False, 
            timeout=1
        )
        return result.returncode == 0
    except Exception:
        return False


class WindowController:
    """Window controller with multi-window memory and persistence."""
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.platform = get_platform()
        self.keyboard_controller = KeyboardController()
        self.mouse_controller = MouseController()
        
        # Set cache directory
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_file = self.cache_dir / "window_memory.json"
        
        # Check ydotool availability on Linux
        self.has_ydotool = False
        if self.platform in ["linux_wayland", "linux_x11"]:
            self.has_ydotool = is_ydotool_available()
        
        # Window memory map: name -> WindowInfo
        self.window_map: Dict[str, WindowInfo] = {}
        self.original_position: Optional[Tuple[int, int]] = None
        self.last_used_window: Optional[str] = None
        
        # Print platform info (only in verbose mode)
        self.verbose = False
        
        # Load cached windows
        self.load_cache()
    
    def set_verbose(self, verbose: bool):
        """Enable/disable verbose output."""
        self.verbose = verbose
        if verbose:
            print(f"Platform: {self.platform}")
            if self.platform == "linux_wayland":
                if self.has_ydotool:
                    print("  Using ydotool for input")
                else:
                    print("  WARNING: ydotool not available on Wayland")
    
    def load_cache(self):
        """Load window map from cache file."""
        if not self.cache_file.exists():
            return
        
        try:
            with open(self.cache_file, 'r') as f:
                data = json.load(f)
                self.window_map = {
                    name: WindowInfo.from_dict(info) 
                    for name, info in data.get("windows", {}).items()
                }
                self.last_used_window = data.get("last_used")
                if self.verbose:
                    print(f"Loaded {len(self.window_map)} windows from cache")
        except Exception as e:
            print(f"Warning: Could not load cache: {e}")
    
    def save_cache(self):
        """Save window map to cache file."""
        try:
            # Create cache directory if it doesn't exist
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            
            # Prepare data for JSON
            data = {
                "windows": {
                    name: info.to_dict() 
                    for name, info in self.window_map.items()
                },
                "last_used": self.last_used_window,
                "updated": datetime.now().isoformat()
            }
            
            # Write to cache file
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            if self.verbose:
                print(f"Saved {len(self.window_map)} windows to cache")
        except Exception as e:
            print(f"Warning: Could not save cache: {e}")
    
    def capture_current_window(self) -> Optional[WindowInfo]:
        """Capture information about the currently focused window."""
        # Get current mouse position (fallback)
        current_pos = self.mouse_controller.position
        window_info = WindowInfo(position=current_pos)
        
        # Platform-specific window info gathering
        if self.platform == "linux_wayland":
            # On Wayland with GNOME, use the Windows extension via gdbus
            try:
                import json as json_module
                
                # Get the list of windows
                result = subprocess.run(
                    [
                        "gdbus", "call", "--session",
                        "--dest=org.gnome.Shell",
                        "--object-path=/org/gnome/Shell/Extensions/Windows",
                        "--method=org.gnome.Shell.Extensions.Windows.List"
                    ],
                    capture_output=True,
                    text=True,
                    check=False
                )
                
                if result.returncode == 0:
                    output = result.stdout.strip()
                    if output.startswith("('") and output.endswith("',)"):
                        json_str = output[2:-3]
                        json_str = json_str.replace("\\'", "'").replace('\\"', '"')
                        windows = json_module.loads(json_str)
                        
                        # Find the focused window
                        focused_window_id = None
                        for window in windows:
                            if window.get("focus", False):
                                window_info.title = window.get("title", "")
                                window_info.window_id = str(window.get("id", ""))
                                window_info.wm_class = window.get("wm_class", "")
                                window_info.pid = window.get("pid")
                                focused_window_id = window.get("id")
                                break
                        
                        # Get detailed position info
                        if focused_window_id:
                            result = subprocess.run(
                                [
                                    "gdbus", "call", "--session",
                                    "--dest=org.gnome.Shell",
                                    "--object-path=/org/gnome/Shell/Extensions/Windows",
                                    "--method=org.gnome.Shell.Extensions.Windows.Details",
                                    str(focused_window_id)
                                ],
                                capture_output=True,
                                text=True,
                                check=False
                            )
                            
                            if result.returncode == 0:
                                output = result.stdout.strip()
                                if output.startswith("('") and output.endswith("',)"):
                                    json_str = output[2:-3]
                                    json_str = json_str.replace("\\'", "'").replace('\\"', '"')
                                    details = json_module.loads(json_str)
                                    
                                    # Calculate center position
                                    x = details.get("x", 0)
                                    y = details.get("y", 0)
                                    width = details.get("width", 800)
                                    height = details.get("height", 600)
                                    
                                    window_info.position = (x + width // 2, y + height // 2)
                                    window_info.geometry = {
                                        "x": x, "y": y,
                                        "width": width, "height": height
                                    }
            except Exception as e:
                if self.verbose:
                    print(f"Warning: Could not get window info via gdbus: {e}")
        
        elif self.platform == "linux_x11":
            # On X11, use xdotool
            try:
                result = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.returncode == 0:
                    window_info.title = result.stdout.strip()
                
                result = subprocess.run(
                    ["xdotool", "getactivewindow"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if result.returncode == 0:
                    window_info.window_id = result.stdout.strip()
            except:
                pass
        
        return window_info
    
    def remember_window(self, name: str, wait_seconds: int = 3) -> bool:
        """
        Remember the currently focused window with a given name.
        
        Args:
            name: Name to save this window as
            wait_seconds: Seconds to wait before capturing
            
        Returns:
            True if window was successfully captured
        """
        # Sanitize window name
        name = name.strip()
        if not name:
            print("Error: Window name cannot be empty")
            return False
        
        print(f"\nClick on the window you want to save as '{name}'")
        print(f"Capturing in {wait_seconds} seconds...")
        
        for i in range(wait_seconds, 0, -1):
            print(f"  {i}...")
            time.sleep(1)
        
        # Capture the window
        window_info = self.capture_current_window()
        if not window_info:
            print("Error: Could not capture window information")
            return False
        
        # Update last used timestamp
        window_info.last_used = time.time()
        
        # Save to map
        is_update = name in self.window_map
        self.window_map[name] = window_info
        self.last_used_window = name
        
        # Save to cache
        self.save_cache()
        
        # Print confirmation
        action = "Updated" if is_update else "Saved"
        print(f"\n✓ {action} window '{name}'")
        if window_info.title:
            print(f"  Title: {window_info.title}")
        if window_info.wm_class:
            print(f"  Class: {window_info.wm_class}")
        if window_info.geometry:
            g = window_info.geometry
            print(f"  Geometry: {g['width']}x{g['height']} at ({g['x']}, {g['y']})")
        print(f"  Center: {window_info.position}")
        
        return True
    
    def focus_window(self, name: Optional[str] = None) -> bool:
        """
        Focus a remembered window.
        
        Args:
            name: Window name to focus, or None for most recently used
            
        Returns:
            True if window was successfully focused
        """
        # Determine which window to focus
        if name:
            if name not in self.window_map:
                print(f"Error: No window named '{name}'")
                return False
            target_name = name
        elif self.last_used_window and self.last_used_window in self.window_map:
            target_name = self.last_used_window
        elif self.window_map:
            # Use first window if no last used
            target_name = next(iter(self.window_map))
        else:
            print("Error: No windows remembered")
            return False
        
        window = self.window_map[target_name]
        
        # Store current mouse position
        self.original_position = self.mouse_controller.position
        
        # Focus the window
        if self.platform in ["linux_wayland", "linux_x11"] and self.has_ydotool:
            try:
                subprocess.run(
                    ["ydotool", "mousemove", 
                     str(window.position[0]), str(window.position[1])],
                    check=False
                )
                time.sleep(0.1)
                subprocess.run(["ydotool", "click", "1"], check=False)
            except:
                pass
        else:
            # Fallback: use pynput
            self.mouse_controller.position = window.position
            time.sleep(0.05)
            self.mouse_controller.click(Button.left)
        
        time.sleep(0.1)
        
        # Update last used
        window.last_used = time.time()
        self.last_used_window = target_name
        self.save_cache()
        
        return True
    
    def send_keystrokes_to_window(self, text: str, 
                                   window_name: Optional[str] = None,
                                   restore_mouse: bool = False):
        """Send keystrokes to a remembered window."""
        if not self.focus_window(window_name):
            return
        
        # Send the keystrokes
        self.send_keystrokes(text)
        
        # Restore mouse position if requested
        if restore_mouse and self.original_position:
            self.mouse_controller.position = self.original_position
            self.original_position = None
    
    def send_key_to_window(self, key: str,
                           window_name: Optional[str] = None,
                           restore_mouse: bool = False):
        """Send a key to a remembered window."""
        if not self.focus_window(window_name):
            return
        
        # Send the key
        self.send_key(key)
        
        # Restore mouse position if requested
        if restore_mouse and self.original_position:
            self.mouse_controller.position = self.original_position
            self.original_position = None
    
    def send_keystrokes(self, text: str):
        """Send keystrokes using the appropriate method."""
        time.sleep(0.1)
        
        if self.platform in ["linux_wayland", "linux_x11"] and self.has_ydotool:
            try:
                subprocess.run(
                    ["ydotool", "type", "--key-delay", "20", "--", text],
                    capture_output=True,
                    text=True,
                    check=False
                )
            except:
                pass
        else:
            self.keyboard_controller.type(text)
    
    def send_key(self, key: str):
        """Send a single key press."""
        if self.platform in ["linux_wayland", "linux_x11"] and self.has_ydotool:
            key_map = {
                "enter": "enter",
                "tab": "tab",
                "space": "space",
                "backspace": "backspace",
                "delete": "delete",
                "escape": "escape",
                "up": "up",
                "down": "down",
                "left": "left",
                "right": "right"
            }
            
            ydotool_key = key_map.get(key.lower())
            if ydotool_key:
                try:
                    subprocess.run(
                        ["ydotool", "key", ydotool_key],
                        capture_output=True,
                        check=False
                    )
                except:
                    pass
        else:
            # Use pynput
            key_map = {
                "enter": Key.enter,
                "tab": Key.tab,
                "space": Key.space,
                "backspace": Key.backspace,
                "delete": Key.delete,
                "escape": Key.esc,
                "up": Key.up,
                "down": Key.down,
                "left": Key.left,
                "right": Key.right
            }
            
            pynput_key = key_map.get(key.lower(), key)
            self.keyboard_controller.tap(pynput_key)
    
    def list_windows(self):
        """List all remembered windows."""
        if not self.window_map:
            print("No windows remembered yet.")
            print("Use: python window_control.py add <name>")
            return
        
        print(f"\nRemembered windows ({len(self.window_map)}):")
        print("-" * 60)
        
        # Sort by last used
        sorted_windows = sorted(
            self.window_map.items(),
            key=lambda x: x[1].last_used or 0,
            reverse=True
        )
        
        for name, info in sorted_windows:
            marker = "→" if name == self.last_used_window else " "
            print(f"{marker} {name}")
            if info.title:
                print(f"    Title: {info.title}")
            if info.wm_class:
                print(f"    Class: {info.wm_class}")
            if info.last_used:
                last_used = datetime.fromtimestamp(info.last_used)
                print(f"    Last used: {last_used.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"    Position: {info.position}")
            print()
    
    def remove_window(self, name: str) -> bool:
        """Remove a window from the map."""
        if name not in self.window_map:
            print(f"Error: No window named '{name}'")
            return False
        
        del self.window_map[name]
        if self.last_used_window == name:
            self.last_used_window = None
        
        self.save_cache()
        print(f"Removed window '{name}'")
        return True


def main():
    """CLI utility for managing window memory."""
    parser = argparse.ArgumentParser(
        description="Window control utility with memory management"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # List command
    subparsers.add_parser("list", help="List all saved windows")
    
    # Add/update command
    add_parser = subparsers.add_parser("add", help="Add or update a window")
    add_parser.add_argument("name", help="Name for the window")
    add_parser.add_argument("-w", "--wait", type=int, default=3,
                           help="Seconds to wait before capture (default: 3)")
    
    # Remove command
    remove_parser = subparsers.add_parser("remove", help="Remove a saved window")
    remove_parser.add_argument("name", help="Name of window to remove")
    
    # Focus command
    focus_parser = subparsers.add_parser("focus", help="Focus a saved window")
    focus_parser.add_argument("name", nargs="?", 
                             help="Window name (optional, defaults to last used)")
    
    # Test command
    test_parser = subparsers.add_parser("test", help="Test sending text to a window")
    test_parser.add_argument("name", nargs="?",
                            help="Window name (optional, defaults to last used)")
    
    args = parser.parse_args()
    
    # Create controller
    controller = WindowController()
    controller.set_verbose(True)
    
    # Execute command
    if not args.command:
        controller.list_windows()
    
    elif args.command == "list":
        controller.list_windows()
    
    elif args.command == "add":
        controller.remember_window(args.name, args.wait)
    
    elif args.command == "remove":
        controller.remove_window(args.name)
    
    elif args.command == "focus":
        if controller.focus_window(args.name):
            print(f"Focused window: {args.name or controller.last_used_window}")
    
    elif args.command == "test":
        print(f"Testing window: {args.name or controller.last_used_window or 'default'}")
        controller.send_keystrokes_to_window("Hello from window control! ", args.name)
        time.sleep(0.5)
        controller.send_key_to_window("enter", args.name, restore_mouse=True)
        print("Test complete!")


if __name__ == "__main__":
    main()