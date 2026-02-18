import sys
from PySide6.QtWidgets import QApplication
from GUI import GradeBookApp

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = GradeBookApp()
    window.show()
    sys.exit(app.exec())
