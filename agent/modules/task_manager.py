"""
task_manager.py — Re-export module for TaskManager and concurrency exception classes.
Provides clean modular imports from agent.modules.task_manager.
"""
from agent.modules.orchestration import (
    TaskManager,
    TaskCollisionError,
    StaleLockTokenError,
    InvalidStateTransitionError,
)

__all__ = [
    "TaskManager",
    "TaskCollisionError",
    "StaleLockTokenError",
    "InvalidStateTransitionError",
]
