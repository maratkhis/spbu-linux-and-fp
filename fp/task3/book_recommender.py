# -*- coding: utf-8 -*-
import sys
import json
import csv
from pathlib import Path
from functools import reduce
from typing import Dict, List, Set, Iterable, Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QCheckBox,
    QSpinBox,
    QComboBox,
    QMessageBox,
)


# --------- Типы и данные ---------
Book = Dict[str, object]
Prefs = Dict[str, Set[str]]

DATA_PATH = Path(__file__).with_name("books.json")


# --------- Работа с данными ---------
def read_books(path: Path) -> List[Book]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return list(data)


def _parse_line(line: str) -> List[str]:
    return [t.strip() for t in line.split(",") if t.strip()]


def make_prefs(genres_text: str, authors_text: str, keywords_text: str) -> Prefs:
    return {
        "genres": set(map(str.lower, _parse_line(genres_text))),
        "authors": set(_parse_line(authors_text)),
        "keywords": set(map(str.lower, _parse_line(keywords_text))),
    }


# --------- Функциональный пайплайн рекомендаций ---------
def stream(iterable: Iterable[Book]) -> Iterable[Book]:
    for x in iterable:
        yield x


def normalize_book(book: Book) -> Book:
    return {
        "title": book.get("title", ""),
        "author": book.get("author", ""),
        "genre": (book.get("genre", "") or "").lower(),
        "description": book.get("description", ""),
        "year": int(book.get("year", 0)),
    }


def score_book(prefs: Prefs, book: Book) -> int:
    score = 0
    if prefs["genres"] and book["genre"] in prefs["genres"]:
        score += 3
    if prefs["authors"] and book["author"] in prefs["authors"]:
        score += 3
    if prefs["keywords"]:
        hay = f'{str(book["title"]).lower()} {str(book["description"]).lower()}'
        score += sum(1 for kw in prefs["keywords"] if kw in hay)
    return score


def annotate_scores(prefs: Prefs) -> Callable[[Iterable[Book]], Iterable[Book]]:
    def _inner(books: Iterable[Book]) -> Iterable[Book]:
        for b in books:
            bb = dict(b)
            bb["score"] = score_book(prefs, bb)
            yield bb

    return _inner


def filter_only_genres(prefs: Prefs, enabled: bool) -> Callable[[Iterable[Book]], Iterable[Book]]:
    def _inner(books: Iterable[Book]) -> Iterable[Book]:
        for b in books:
            if not enabled or not prefs["genres"] or b["genre"] in prefs["genres"]:
                yield b

    return _inner


def filter_after_year(year_threshold: int) -> Callable[[Iterable[Book]], Iterable[Book]]:
    def _inner(books: Iterable[Book]) -> Iterable[Book]:
        if year_threshold <= 0:
            yield from books
        else:
            for b in books:
                try:
                    year = int(b.get("year", 0))
                except (TypeError, ValueError):
                    year = 0
                if year > year_threshold:
                    yield b

    return _inner


def filter_positive_scores() -> Callable[[Iterable[Book]], Iterable[Book]]:
    def _inner(books: Iterable[Book]) -> Iterable[Book]:
        for b in books:
            if b.get("score", 0) > 0:
                yield b
    return _inner


def _sorter(mode: str):
    if mode == "alpha":
        return lambda items: sorted(items, key=lambda b: str(b.get("title", "")))
    if mode == "year":
        return lambda items: sorted(items, key=lambda b: int(b.get("year", 0)), reverse=True)
    # по умолчанию — по рейтингу, затем по году
    return lambda items: sorted(
        items,
        key=lambda b: (int(b.get("score", 0)), int(b.get("year", 0))),
        reverse=True,
    )


def _compose(*funcs: Callable):
    def _composed(x):
        return reduce(lambda acc, f: f(acc), funcs, x)

    return _composed


def recommend(books: List[Book], prefs: Prefs, only_genres: bool, year_after: int, sort_mode: str) -> List[Book]:
    pipeline = _compose(
        stream,
        lambda it: (normalize_book(b) for b in it),
        filter_only_genres(prefs, only_genres),
        filter_after_year(year_after),
        annotate_scores(prefs),
        filter_positive_scores(),  # Фильтруем книги с рейтингом > 0
        list,
        _sorter(sort_mode),
    )
    return pipeline(books)


# --------- Виджет карточки книги ---------
class BookCard(QWidget):
    def __init__(self, book: Book, index: int):
        super().__init__()
        self.book = book

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        title = QLabel(f"{index}. {book.get('title', '')}")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")

        info_parts = []
        if book.get("author"):
            info_parts.append(str(book.get("author", "")))
        if book.get("year"):
            info_parts.append(str(book.get("year", "")))
        if book.get("genre"):
            info_parts.append(str(book.get("genre", "")))

        info = QLabel(" • ".join(info_parts))
        info.setStyleSheet("color: #555;")

        desc = QLabel(str(book.get("description", "")))
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #444;")

        score_lbl = QLabel(f"Рейтинг: {book.get('score', 0)}")
        score_lbl.setAlignment(Qt.AlignRight)
        score_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")

        layout.addWidget(title)
        layout.addWidget(info)
        layout.addWidget(desc)
        layout.addWidget(score_lbl)


# --------- Главное окно ---------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Рекомендательная система книг")
        self.resize(900, 650)

        self.books_db: List[Book] = read_books(DATA_PATH)
        self.recommendations: List[Book] = []
        self.to_read: List[Book] = []

        # ---- поля ввода предпочтений ----
        self.genres_edit = QLineEdit()
        self.authors_edit = QLineEdit()
        self.keywords_edit = QLineEdit()

        self.only_genres_cb = QCheckBox("Только указанные жанры")
        self.year_spin = QSpinBox()
        self.year_spin.setRange(0, 2100)
        self.year_spin.setValue(0)

        self.sort_combo = QComboBox()
        self.sort_combo.addItem("По рейтингу", userData="score")
        self.sort_combo.addItem("По алфавиту", userData="alpha")
        self.sort_combo.addItem("По году публикации (новые сначала)", userData="year")
        self.sort_combo.setCurrentIndex(0)

        self.recommend_btn = QPushButton("Показать рекомендации")
        self.add_to_read_btn = QPushButton("Добавить в список «прочитать»")
        self.save_btn = QPushButton("Сохранить рекомендации...")
        self.save_to_read_btn = QPushButton("Сохранить список «прочитать»...")  # Новая кнопка

        # ---- списки ----
        self.cards = QListWidget()
        self.cards.setSelectionMode(QListWidget.ExtendedSelection)
        self.cards.setSpacing(6)

        self.to_read_list = QListWidget()

        # ---- компоновка ----
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        def row(label_text: str, widget: QWidget) -> QHBoxLayout:
            h = QHBoxLayout()
            h.addWidget(QLabel(label_text))
            h.addWidget(widget, stretch=1)
            return h

        root.addLayout(row("Любимые жанры (через запятую):", self.genres_edit))
        root.addLayout(row("Любимые авторы (через запятую):", self.authors_edit))
        root.addLayout(row("Ключевые слова (через запятую):", self.keywords_edit))

        filters = QHBoxLayout()
        filters.addWidget(self.only_genres_cb)
        filters.addWidget(QLabel("Год после:"))
        filters.addWidget(self.year_spin)
        filters.addStretch(1)
        filters.addWidget(QLabel("Сортировка:"))
        filters.addWidget(self.sort_combo)
        root.addLayout(filters)

        buttons = QHBoxLayout()
        buttons.addWidget(self.recommend_btn)
        buttons.addWidget(self.add_to_read_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.save_to_read_btn)  # Добавляем новую кнопку
        root.addLayout(buttons)

        lists_row = QHBoxLayout()
        lists_row.addWidget(self.cards, stretch=3)

        side = QVBoxLayout()
        side.addWidget(QLabel("Список «прочитать»:"))
        side.addWidget(self.to_read_list)
        lists_row.addLayout(side, stretch=2)

        root.addLayout(lists_row)

        # ---- сигналы ----
        self.recommend_btn.clicked.connect(self.on_recommend)
        self.add_to_read_btn.clicked.connect(self.on_add_to_read)
        self.save_btn.clicked.connect(self.on_save)
        self.save_to_read_btn.clicked.connect(self.on_save_to_read)  # Новый обработчик

        # стартовая выдача
        self.on_recommend()

    # ---- вспомогательные методы ----
    def fill_cards(self, items: List[Book]) -> None:
        self.cards.clear()
        for i, book in enumerate(items, start=1):
            card = BookCard(book, i)
            item = QListWidgetItem(self.cards)
            item.setSizeHint(card.sizeHint())
            item.setData(Qt.UserRole, book)
            self.cards.addItem(item)
            self.cards.setItemWidget(item, card)

    def selected_books_from_cards(self) -> List[Book]:
        return [dict(it.data(Qt.UserRole)) for it in self.cards.selectedItems()]

    def show_error(self, message: str):
        QMessageBox.warning(self, "Ошибка", message)

    def show_info(self, message: str):
        QMessageBox.information(self, "Информация", message)

    # ---- обработчики ----
    def on_recommend(self) -> None:
        # Проверяем, что хотя бы одно поле заполнено
        genres_text = self.genres_edit.text().strip()
        authors_text = self.authors_edit.text().strip()
        keywords_text = self.keywords_edit.text().strip()
        
        if not genres_text and not authors_text and not keywords_text:
            self.show_error("Пожалуйста, заполните хотя бы одно поле: жанры, авторы или ключевые слова.")
            return
        
        prefs = make_prefs(genres_text, authors_text, keywords_text)
        only_genres = self.only_genres_cb.isChecked()
        year_after = int(self.year_spin.value())
        sort_mode = self.sort_combo.currentData()

        self.recommendations = recommend(self.books_db, prefs, only_genres, year_after, sort_mode)
        
        # Проверяем, есть ли рекомендации
        if not self.recommendations:
            self.show_info("По вашему запросу не найдено подходящих книг. Попробуйте изменить критерии поиска.")
            self.fill_cards([])
        else:
            self.fill_cards(self.recommendations)

    def on_add_to_read(self) -> None:
        selected_books = self.selected_books_from_cards()
        if not selected_books:
            self.show_info("Выберите книги для добавления в список «прочитать».")
            return
            
        added_count = 0
        for b in selected_books:
            if b not in self.to_read:
                self.to_read.append(b)
                self.to_read_list.addItem(f'{b.get("title", "")} — {b.get("author", "")} ({b.get("year", "")})')
                added_count += 1
        
        if added_count > 0:
            self.show_info(f"Добавлено {added_count} книг в список «прочитать».")

    def on_save(self) -> None:
        if not self.recommendations:
            self.show_error("Нет рекомендаций для сохранения.")
            return
            
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Сохранить рекомендации",
            "recommendations.json",
            "JSON (*.json);;CSV (*.csv)",
        )
        if not path:
            return
            
        try:
            if path.lower().endswith(".json") or "JSON" in selected_filter:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.recommendations, f, ensure_ascii=False, indent=2)
            else:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f, delimiter=";")
                    w.writerow(["title", "author", "genre", "year", "description", "score"])
                    for b in self.recommendations:
                        w.writerow(
                            [
                                b.get("title", ""),
                                b.get("author", ""),
                                b.get("genre", ""),
                                b.get("year", ""),
                                b.get("description", ""),
                                b.get("score", 0),
                            ]
                        )
            self.show_info(f"Рекомендации успешно сохранены в файл: {path}")
        except Exception as e:
            self.show_error(f"Ошибка при сохранении файла: {str(e)}")

    def on_save_to_read(self) -> None:
        if not self.to_read:
            self.show_error("Список «прочитать» пуст.")
            return
            
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Сохранить список «прочитать»",
            "to_read_list.json",
            "JSON (*.json);;CSV (*.csv)",
        )
        if not path:
            return
            
        try:
            if path.lower().endswith(".json") or "JSON" in selected_filter:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.to_read, f, ensure_ascii=False, indent=2)
            else:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    w = csv.writer(f, delimiter=";")
                    w.writerow(["title", "author", "genre", "year", "description"])
                    for b in self.to_read:
                        w.writerow(
                            [
                                b.get("title", ""),
                                b.get("author", ""),
                                b.get("genre", ""),
                                b.get("year", ""),
                                b.get("description", ""),
                            ]
                        )
            self.show_info(f"Список «прочитать» успешно сохранен в файл: {path}")
        except Exception as e:
            self.show_error(f"Ошибка при сохранении файла: {str(e)}")


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()