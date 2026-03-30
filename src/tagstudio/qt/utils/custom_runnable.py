# SPDX-FileCopyrightText: (c) TagStudio Contributors
# SPDX-License-Identifier: GPL-3.0-only


import traceback

from PySide6.QtCore import QObject, QRunnable, Signal


class CustomRunnable(QRunnable, QObject):  # pyright: ignore[reportUnsafeMultipleInheritance]
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
