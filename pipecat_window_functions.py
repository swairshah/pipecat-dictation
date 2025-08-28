"""
Pipecat-compatible window control functions.
Provides clean interfaces for LLM tool calls to control application windows.
"""

from typing import Dict, List, Optional
from window_control import WindowController
from pipecat.adapters.schemas.function_schema import FunctionSchema


# Global controller instance (singleton)
_controller = None


def _get_controller() -> WindowController:
    """Get or create the global window controller."""
    global _controller
    if _controller is None:
        _controller = WindowController()
    return _controller


# ============================================================================
# Pipecat Tool Functions
# ============================================================================


def list_windows() -> Dict[str, List[Dict[str, str]]]:
    """
    Get the list of all remembered windows.

    Returns:
        Dictionary with 'windows' key containing list of window info
    """
    controller = _get_controller()

    windows = []
    for name, info in controller.window_map.items():
        window_dict = {
            "name": name,
            "title": info.title or "Unknown",
            "class": info.wm_class or "Unknown",
            "is_last_used": name == controller.last_used_window,
        }
        windows.append(window_dict)

    # Sort by last used (most recent first)
    windows.sort(key=lambda x: x["is_last_used"], reverse=True)

    return {
        "success": True,
        "windows": windows,
        "count": len(windows),
        "last_used": controller.last_used_window or "none",
    }


def remember_window(name: str, wait_seconds: int = 3) -> Dict[str, any]:
    """
    Remember/save the currently focused window with a given name.

    Args:
        name: Name to save this window as
        wait_seconds: Seconds to wait before capturing (default: 3)

    Returns:
        Dictionary with success status and window info
    """
    controller = _get_controller()

    # Sanitize the name
    name = name.strip()
    if not name:
        return {"success": False, "error": "Window name cannot be empty"}

    try:
        # The remember_window method will handle the countdown
        success = controller.remember_window(name, wait_seconds)

        if success and name in controller.window_map:
            info = controller.window_map[name]
            return {
                "success": True,
                "name": name,
                "window": {
                    "title": info.title or "Unknown",
                    "class": info.wm_class or "Unknown",
                    "position": list(info.position) if info.position else None,
                },
                "message": f"Successfully saved window '{name}'",
            }
        else:
            return {"success": False, "error": "Failed to capture window information"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def send_text_to_window(
    text: str, window_name: Optional[str] = None, send_newline: bool = True
) -> Dict[str, any]:
    """
    Send text to a specific remembered window.

    Args:
        text: The text to send to the window
        window_name: Name of the window to send to (None for last used)
        send_newline: Whether to send Enter key after the text (default: True)

    Returns:
        Dictionary with success status
    """
    controller = _get_controller()

    # Check if we have any windows
    if not controller.window_map:
        return {"success": False, "error": "No windows remembered. Use remember_window first."}

    # Validate window name if provided
    if window_name and window_name not in controller.window_map:
        return {
            "success": False,
            "error": f"Window '{window_name}' not found",
            "available_windows": list(controller.window_map.keys()),
        }

    try:
        # Send the text
        controller.send_keystrokes_to_window(text, window_name)

        # Send newline if requested
        if send_newline:
            controller.send_key_to_window("enter", window_name)

        # Determine which window was used
        target_window = window_name or controller.last_used_window or "default"

        return {
            "success": True,
            "message": f"Sent text to window '{target_window}'",
            "window_used": target_window,
            "text_length": len(text),
            "newline_sent": send_newline,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def focus_window(window_name: Optional[str] = None) -> Dict[str, any]:
    """
    Focus a specific window or the last used window.

    Args:
        window_name: Name of window to focus (None for last used)

    Returns:
        Dictionary with success status
    """
    controller = _get_controller()

    if not controller.window_map:
        return {"success": False, "error": "No windows remembered"}

    if window_name and window_name not in controller.window_map:
        return {
            "success": False,
            "error": f"Window '{window_name}' not found",
            "available_windows": list(controller.window_map.keys()),
        }

    try:
        success = controller.focus_window(window_name)
        target = window_name or controller.last_used_window or "default"

        if success:
            return {
                "success": True,
                "message": f"Focused window '{target}'",
                "window_focused": target,
            }
        else:
            return {"success": False, "error": f"Failed to focus window '{target}'"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================================
# Pipecat Function Schemas
# ============================================================================


list_windows_schema = FunctionSchema(
    name="list_windows",
    description="Get the list of all remembered windows that can receive text input",
    properties={},
    required=[],
)


remember_window_schema = FunctionSchema(
    name="remember_window",
    description="Remember/save the currently focused window with a custom name for later use",
    properties={
        "name": {
            "type": "string",
            "description": "A memorable name for this window (e.g., 'editor', 'terminal', 'browser')",
        },
        "wait_seconds": {
            "type": "integer",
            "description": "Seconds to wait before capturing the window (default: 3)",
            "default": 3,
            "minimum": 1,
            "maximum": 10,
        },
    },
    required=["name"],
)


send_text_to_window_schema = FunctionSchema(
    name="send_text_to_window",
    description="Send text to a specific remembered window",
    properties={
        "text": {"type": "string", "description": "The text to type into the window"},
        "window_name": {
            "type": "string",
            "description": "Name of the window to send text to (omit to use last focused window)",
        },
        "send_newline": {
            "type": "boolean",
            "description": "Whether to press Enter after sending the text (default: true)",
            "default": True,
        },
    },
    required=["text"],
)


focus_window_schema = FunctionSchema(
    name="focus_window",
    description="Focus/activate a specific remembered window",
    properties={
        "window_name": {
            "type": "string",
            "description": "Name of the window to focus (omit to focus last used window)",
        }
    },
    required=[],
)


# ============================================================================
# Function Registry for Pipecat
# ============================================================================

WINDOW_CONTROL_FUNCTIONS = {
    "list_windows": (list_windows, list_windows_schema),
    "remember_window": (remember_window, remember_window_schema),
    "send_text_to_window": (send_text_to_window, send_text_to_window_schema),
    "focus_window": (focus_window, focus_window_schema),
}


def get_window_control_schemas() -> List[FunctionSchema]:
    """Get all window control function schemas for Pipecat."""
    return [schema for _, schema in WINDOW_CONTROL_FUNCTIONS.values()]


def get_window_control_handlers() -> Dict[str, callable]:
    """Get all window control function handlers for Pipecat."""
    return {name: func for name, (func, _) in WINDOW_CONTROL_FUNCTIONS.items()}
