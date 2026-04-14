# src/routes package
"""HTTP route modules for the Flask application.

Each module registers routes for a specific domain:
- core_routes: Index, static pages, basic API
- lines_routes: Line management (/api/lines/*)
- trades_routes: Trade management (/api/trades/*)
- admin_routes: Admin dashboard (/admin, /api/admin/*)
- debug_routes: Debug/test helpers (/api/debug/*, /__reset_all, /__shutdown)
- socketio_handlers: WebSocket event handlers
"""

from .core_routes import register_core_routes
from .lines_routes import register_lines_routes
from .trades_routes import register_trades_routes
from .admin_routes import register_admin_routes
from .debug_routes import register_debug_routes
from .socketio_handlers import register_socketio_handlers

__all__ = [
    "register_core_routes",
    "register_lines_routes",
    "register_trades_routes",
    "register_admin_routes",
    "register_debug_routes",
    "register_socketio_handlers",
]
