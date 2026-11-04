import sys

from PySide6.QtWidgets import QApplication

from . import __version__
from .icons import app_icon
from .main_window import MainWindow, APP_NAME


def run():
    if sys.platform == "win32":
        try:  # own taskbar group/icon instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.{__version__}")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        window.open_file(args[0])
    sys.exit(app.exec())
