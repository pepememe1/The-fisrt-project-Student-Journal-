import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QInputDialog,
    QMessageBox, QFileDialog, QHeaderView, QMenu
)

from core import GradeBook, Student, APP_VERSION

class GradeBookGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.book = GradeBook()
        self.sort_desc = True

        self.setWindowTitle(f"Журнал успеваемости — {APP_VERSION}")
        self.resize(1000, 600)

        menubar = self.menuBar()
        file_menu = menubar.addMenu("Файл")

        act_export = QAction("Экспорт в Excel", self)
        act_export.triggered.connect(self.export_excel)
        file_menu.addAction(act_export)

        act_reset = QAction("Новая таблица", self)
        act_reset.triggered.connect(self.reset_data)
        file_menu.addAction(act_reset)

        file_menu.addSeparator()
        file_menu.addAction("Выход", self.close)

        help_menu = menubar.addMenu("Справка")
        help_menu.addAction("О программе", self.show_about)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.table = QTableWidget()
        layout.addWidget(self.table)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)

        self.table.horizontalHeader().sectionClicked.connect(self.sort_by_average)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)

        btns = QHBoxLayout()
        layout.addLayout(btns)

        btn_add = QPushButton("Добавить студента")
        btn_add.clicked.connect(self.add_student)
        btns.addWidget(btn_add)

        btn_stats = QPushButton("Статистика")
        btn_stats.clicked.connect(self.show_stats)
        btns.addWidget(btn_stats)

        btns.addStretch()
        btns.addWidget(QPushButton("Выход", clicked=self.close))

        self.update_table()

    def update_table(self):
        headers = (
            ["№", "Имя", "Фамилия"]
            + [f"Задание {i+1}" for i in range(self.book.kol_zad)]
            + ["Средний балл"]
        )

        self.table.clear()
        self.table.setRowCount(len(self.book.spisok_stud))
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)

        for r, s in enumerate(self.book.spisok_stud):
            self.table.setItem(r, 0, QTableWidgetItem(str(r + 1)))
            self.table.setItem(r, 1, QTableWidgetItem(s.n))
            self.table.setItem(r, 2, QTableWidgetItem(s.f))
            for i, g in enumerate(s.stud_ball):
                self.table.setItem(r, 3 + i, QTableWidgetItem(str(g)))
            self.table.setItem(r, 3 + len(s.stud_ball), QTableWidgetItem(f"{s.average:.2f}"))

    def sort_by_average(self, col):
        if col != self.table.columnCount() - 1:
            return
        self.book.spisok_stud.sort(key=lambda s: s.average, reverse=self.sort_desc)
        self.sort_desc = not self.sort_desc
        self.update_table()

    def context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu()
        menu.addAction("Редактировать", lambda: self.edit_student(row))
        menu.exec(QCursor.pos())

    def edit_student(self, idx):
        s = self.book.spisok_stud[idx]
        name, ok = QInputDialog.getText(self, "Имя", "Имя:", text=s.n)
        if not ok:
            return
        surname, ok = QInputDialog.getText(self, "Фамилия", "Фамилия:", text=s.f)
        if not ok:
            return

        grades = []
        for i, g in enumerate(s.stud_ball):
            val, ok = QInputDialog.getInt(self, "Оценка", f"Задание {i+1}", g, 1, 5)
            if not ok:
                return
            grades.append(val)

        self.book.update_student(idx, Student(name, surname, grades))
        self.update_table()

    def add_student(self):
        if self.book.kol_zad == 0:
            k, ok = QInputDialog.getInt(self, "Настройка", "Количество заданий:", 2, 2)
            if not ok:
                return
            self.book.set_config(k)

        name, ok = QInputDialog.getText(self, "Имя", "Имя:")
        if not ok:
            return
        surname, ok = QInputDialog.getText(self, "Фамилия", "Фамилия:")
        if not ok:
            return

        grades = [
            QInputDialog.getInt(self, "Оценка", f"Задание {i+1}", 1, 1, 5)[0]
            for i in range(self.book.kol_zad)
        ]

        self.book.add_student(Student(name, surname, grades))
        self.update_table()

    def show_stats(self):
        s = self.book.spisok_stud
        if not s:
            return
        avg = sum(x.average for x in s) / len(s)
        best = max(s, key=lambda x: x.average)
        worst = min(s, key=lambda x: x.average)
        QMessageBox.information(
            self, "Статистика",
            f"Средний балл группы: {avg:.2f}\n"
            f"Лучший: {best.f} {best.n} ({best.average:.2f})\n"
            f"Худший: {worst.f} {worst.n} ({worst.average:.2f})"
        )

    def export_excel(self):
        file, _ = QFileDialog.getSaveFileName(self, "Экспорт", "gradebook.xlsx", "*.xlsx")
        if file:
            self.book.export_excel(file)

    def reset_data(self):
        if QMessageBox.question(self, "Сброс", "Очистить данные?") == QMessageBox.Yes:
            self.book.reset_data()
            self.update_table()

    def show_about(self):
        QMessageBox.information(self, "О программе", f"Журнал успеваемости\nВерсия {APP_VERSION}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = GradeBookGUI()
    window.show()
    sys.exit(app.exec())
