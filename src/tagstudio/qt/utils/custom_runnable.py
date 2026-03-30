# Copyright (C) 2024 Travis Abendshien (CyanVoxel).
# Licensed under the GPL-3.0 License.
# Created for TagStudio: https://github.com/CyanVoxel/TagStudio


import traceback

from PySide6.QtCore import QObject, QRunnable, Signal


class CustomRunnable(QRunnable, QObject):
    done = Signal()
    error = Signal(object)

    def __init__(self, function) -> None:
        QRunnable.__init__(self)
        QObject.__init__(self)
        self.function = function

    def run(self):
        try:
            self.function()
        except Exception as exc:
            # Preserve traceback in stderr for debugging and notify listeners.
            traceback.print_exc()
            self.error.emit(exc)
        finally:
            self.done.emit()
