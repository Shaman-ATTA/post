"""Handlers package"""
from .commands import register_commands
from .posts import register_post_handlers
from .templates import register_template_handlers
from .callbacks import register_callback_handlers

__all__ = [
    'register_commands',
    'register_post_handlers', 
    'register_template_handlers',
    'register_callback_handlers'
]
