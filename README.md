# The-fisrt-project-Student-Journal-
На русском языке
Техническое описание GradeBook (версия альфа 1.0)
В этом разделе описана внутренняя логика приложения и ключевые особенности реализации.
Архитектура и логика данных
Приложение разделено на два независимых модуля: ядро (core.py) и интерфейс (GUI.py). Это позволяет изменять логику расчетов без правки кода окон.
 * Данные студента хранятся в объектах Dataclass. Это упрощает работу с полями и позволяет быстро конвертировать данные в словари для сохранения.
 * Средний балл вычисляется динамически через свойство @property внутри класса Student, что исключает ошибки при обновлении оценок.
Хранение и валидация
 * Данные сохраняются в формате JSON. При записи используется параметр ensure_ascii=False, чтобы кириллица в именах студентов отображалась корректно.
 * Встроена жесткая валидация: программа не примет оценки ниже 1 или выше 5, а также не позволит добавить студента, если количество его оценок не совпадает с настройками текущей таблицы.
Работа с Excel
Экспорт реализован через библиотеку openpyxl. Основные фишки:
 * Динамическое формирование колонок в зависимости от количества заданий.
 * Использование стилей (PatternFill, Border) для создания профессионального вида отчета.
 * Условное форматирование на уровне файла: правила для подсветки ячеек прописываются прямо в код Excel, поэтому цвета сохраняются при любом открытии файла.
Особенности интерфейса
Графическая оболочка написана на PySide6.
 * Таблица в GUI автоматически перестраивает заголовки при создании новой сессии.
 * Сортировка по среднему баллу реализована через метод sort в Python, после чего таблица полностью перерисовывается. Это работает быстрее, чем встроенная сортировка виджета при больших списках.
 * Контекстное меню позволяет редактировать данные любого студента «на лету» с автоматическим сохранением в файл.
English
Technical Overview (Alpha 1.0)
This section explains how the GradeBook application works under the hood and highlights specific coding techniques.
Architecture and Data Logic
The project follows a clean separation between business logic (core.py) and the user interface (GUI.py).
 * Student records are managed using Python Dataclasses. This makes data handling efficient and allows for easy serialization into dictionaries.
 * The average score is calculated via a @property method within the Student class, ensuring the value is always up-to-date and consistent.
Persistence and Validation
 * Data is stored locally in JSON format. The serialization process uses ensure_ascii=False to properly support Cyrillic characters.
 * Strict validation is implemented: the app rejects any grades outside the 1-5 range and ensures the number of grades matches the initial configuration of the group.
Excel Generation Nuances
The export functionality is powered by the openpyxl library:
 * Column headers are generated dynamically based on the number of assignments set by the user.
 * Professional styling: the code applies custom borders, fills, and alignment to the header row.
* Native conditional formatting: rules for cell highlighting (green for high scores, red for low) are embedded directly into the Excel file, making the reports interactive.
GUI Implementation
The interface is built with PySide6 (Qt for Python):
 * The main table view is dynamic and updates its structure based on the current data model.
 * Sorting by average grade is handled via Python's built-in sorting on the student list, which provides better performance than standard UI-based sorting.
 * Context menus provide quick access to record editing, with changes being saved to the JSON database immediately.
