"""Interface desktop Qt. O motor roda separado e fornece apenas snapshots."""
from __future__ import annotations

import json
import math
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPropertyAnimation, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGraphicsOpacityEffect, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QStyledItemDelegate, QTableView, QVBoxLayout, QWidget,
)


STATES = {"queued": "Na fila", "planning": "Verificando acesso", "running": "Baixando",
          "paused": "Pausado", "cancelled": "Cancelado", "completed": "Concluído", "error": "Interrompido",
          "verifying": "Verificando integridade", "retrying": "Tentando novamente", "pausing": "Pausando…", "cancelling": "Cancelando…", "checking": "Verificando…"}


def official_icon() -> QIcon:
    """Localiza o PNG tanto no código-fonte como no bundle PyInstaller."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return QIcon(str(root / "assets" / "icons" / "driver_downloader_icon.png"))


def byte_text(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = max(0, float(value))
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if number < 1024 or unit == "TiB":
            return f"{number:.1f} {unit}"
        number /= 1024
    return "—"


def eta_text(value) -> str:
    try:
        if value is None or not math.isfinite(float(value)) or float(value) < 0:
            return "—"
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return "—"
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}min"
    return f"{seconds // 60}min {seconds % 60:02d}s"


def progress_percent(row: dict) -> int | None:
    if row.get("state") == "completed":
        return 100
    size = row.get("size")
    if not size or size < 0:
        return None
    return max(0, min(99, int(row.get("done", 0) * 100 / size)))


def icon(name: str, color: str = "#a8b3cb", size: int = 24) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24, size / 24)
    painter.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if name == "home":
        path = QPainterPath(); path.moveTo(3, 11); path.lineTo(12, 3); path.lineTo(21, 11)
        painter.drawPath(path); painter.drawRoundedRect(5, 10, 14, 11, 2, 2)
        painter.drawRect(10, 15, 4, 6)
    elif name in ("download", "logo"):
        painter.drawLine(12, 3, 12, 15); painter.drawLine(7, 10, 12, 15); painter.drawLine(17, 10, 12, 15)
        path = QPainterPath(); path.moveTo(4, 15); path.lineTo(4, 20); path.lineTo(20, 20); path.lineTo(20, 15)
        painter.drawPath(path)
    elif name == "history":
        painter.drawArc(4, 4, 16, 16, 30 * 16, 300 * 16)
        painter.drawLine(12, 7, 12, 12); painter.drawLine(12, 12, 16, 14)
        painter.drawLine(3, 4, 3, 9); painter.drawLine(3, 9, 8, 9)
    elif name == "settings":
        for y, x in ((6, 8), (12, 16), (18, 10)):
            painter.drawLine(4, y, 20, y)
            painter.setBrush(QColor("#101827")); painter.drawEllipse(x - 2, y - 2, 4, 4)
    elif name == "folder":
        path = QPainterPath(); path.moveTo(3, 7); path.lineTo(3, 20); path.lineTo(21, 20); path.lineTo(21, 8); path.lineTo(11, 8); path.lineTo(9, 4); path.lineTo(3, 4); path.closeSubpath()
        painter.drawPath(path)
    elif name == "pause":
        painter.drawLine(8, 5, 8, 19); painter.drawLine(16, 5, 16, 19)
    elif name == "play":
        path = QPainterPath(); path.moveTo(7, 4); path.lineTo(20, 12); path.lineTo(7, 20); path.closeSubpath(); painter.drawPath(path)
    elif name == "stop":
        painter.drawRoundedRect(5, 5, 14, 14, 2, 2)
    elif name == "shield":
        path = QPainterPath(); path.moveTo(12, 3); path.lineTo(20, 6); path.lineTo(19, 15); path.quadTo(16, 20, 12, 22); path.quadTo(8, 20, 5, 15); path.lineTo(4, 6); path.closeSubpath(); painter.drawPath(path)
        painter.drawLine(8, 12, 11, 15); painter.drawLine(11, 15, 16, 9)
    painter.end()
    return QIcon(pixmap)


def stylesheet(light: bool = False) -> str:
    palette = ({"bg": "#f2f5fb", "sidebar": "#e9eef8", "card": "#ffffff", "border": "#dce3ef",
                "text": "#14213c", "muted": "#64718a", "field": "#f8faff", "hover": "#e4e9fc", "selected": "#e3e7fd"}
               if light else {"bg": "#0b101b", "sidebar": "#101725", "card": "#131c2d", "border": "#253149",
                              "text": "#edf1fa", "muted": "#8e9eb9", "field": "#0f1726", "hover": "#1e2a43", "selected": "#26345a"})
    return """
    QWidget {{ color: {text}; font-family: 'Segoe UI'; font-size: 13px; }}
    QMainWindow, QWidget#page {{ background: {bg}; }}
    QFrame#sidebar {{ background: {sidebar}; border-right: 1px solid {border}; }}
    QFrame#card {{ background: {card}; border: 1px solid {border}; border-radius: 14px; }}
    QFrame#hero {{ background: {card}; border: 1px solid #3b4380; border-radius: 16px; }}
    QLabel#eyebrow {{ color: #a3a9ff; font-weight: 700; font-size: 11px; letter-spacing: 1px; }}
    QLabel#pageTitle {{ font-size: 28px; font-weight: 700; }}
    QLabel#heroTitle {{ font-size: 25px; font-weight: 700; }}
    QLabel#section {{ font-size: 15px; font-weight: 600; }}
    QLabel#muted {{ color: {muted}; }}
    QLabel#metric {{ font-size: 25px; font-weight: 700; }}
    QLabel#brand {{ font-size: 17px; font-weight: 700; }}
    QLabel#badge {{ color: #b6bdff; background: #283052; border-radius: 7px; padding: 5px 8px; font-size: 10px; font-weight: 700; }}
    QLabel#notice {{ background: {field}; border: 1px solid {border}; border-radius: 8px; padding: 10px; color: {muted}; }}
    QLabel#status {{ color: #70d8b7; font-size: 12px; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {field}; border: 1px solid {border}; border-radius: 8px; padding: 10px; selection-background-color: #5d61d8; }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: #8185f6; }}
    QComboBox::drop-down {{ border: 0; width: 25px; }}
    QComboBox QAbstractItemView {{ background: {card}; color: {text}; selection-background-color: {selected}; border: 1px solid {border}; padding: 5px; }}
    QPushButton {{ background: {card}; border: 1px solid {border}; border-radius: 8px; padding: 10px 15px; font-weight: 600; }}
    QPushButton:hover {{ background: {hover}; border-color: #6472a1; }}
    QPushButton:disabled {{ color: {muted}; background: {field}; }}
    QPushButton#primary {{ background: #7276ee; color: white; border: 0; padding: 13px 20px; }}
    QPushButton#primary:hover {{ background: #8589fa; }}
    QPushButton#nav {{ text-align: left; border: 0; background: transparent; color: {muted}; padding: 13px 15px; }}
    QPushButton#nav:checked {{ background: {selected}; color: {text}; }}
    QPushButton#nav:hover {{ background: {hover}; }}
    QPushButton#quiet {{ border: 0; background: transparent; color: #a0a7fa; padding: 5px; }}
    QTableView {{ background: {card}; alternate-background-color: {card}; border: 0; selection-background-color: {selected}; gridline-color: {border}; }}
    QTableView::item {{ border: 0; padding: 7px; }}
    QHeaderView::section {{ background: {card}; color: {muted}; border: 0; border-bottom: 1px solid {border}; padding: 12px 7px; font-size: 11px; font-weight: 600; }}
    QScrollArea {{ border: 0; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: #3b4961; border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QCheckBox {{ spacing: 9px; }}
    QToolTip {{ background: {card}; color: {text}; border: 1px solid {border}; padding: 5px; }}
    """.format(**palette)


class DataTable(QAbstractTableModel):
    def __init__(self, history: bool = False):
        super().__init__()
        self.rows: list[dict] = []
        self.history = history
        self.headers = (["Arquivo / pasta", "Estado", "Tamanho", "Destino"] if history else
                        ["Arquivo", "Estado", "Progresso", "Tamanho", "Velocidade", "Restante"])

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.ToolTipRole:
            parts = [row.get("relative_path") or str(row.get("destination", "")), row.get("error"), row.get("note")]
            return "\n\n".join(str(part) for part in parts if part)
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 1:
            return QColor({"completed": "#70d8b7", "error": "#fb9c9c", "paused": "#e5bf70"}.get(row.get("state"), "#a6b1d2"))
        if role == Qt.ItemDataRole.DisplayRole:
            name = row.get("relative_path") or row.get("name") or "Preparando…"
            state = STATES.get(row.get("state"), row.get("state", "—"))
            size = byte_text(row.get("size"))
            if self.history:
                return [name, state, size, str(row.get("destination", ""))][index.column()]
            percent = progress_percent(row)
            speed = byte_text(row.get("speed", 0)) + "/s" if row.get("state") == "running" else "—"
            return [name, state, f"{percent}%" if percent is not None else "—", size, speed, eta_text(row.get("eta"))][index.column()]

    def replace(self, rows: list[dict]):
        if self.rows == rows:
            return
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole) or {}
        percent = progress_percent(row)
        painter.save()
        bounds = option.rect.adjusted(9, 13, -9, -13)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#29354d"))
        painter.drawRoundedRect(bounds, 4, 4)
        if percent is not None:
            fill = bounds.adjusted(0, 0, 0, 0)
            fill.setWidth(max(0, int(bounds.width() * percent / 100)))
            painter.setBrush(QColor("#6dd3b1" if percent == 100 else "#8185f6"))
            painter.drawRoundedRect(fill, 4, 4)
        painter.setPen(QColor("#edf1fa"))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, f"{percent}%" if percent is not None else "—")
        painter.restore()


class Operation(QThread):
    result = Signal(bool, str)

    def __init__(self, action):
        super().__init__()
        self.action = action

    def run(self):
        try:
            self.action()
            self.result.emit(True, "Operação concluída.")
        except Exception as exc:
            # Backend fornece mensagens públicas sem tokens/cabeçalhos.
            self.result.emit(False, str(exc))


class Window(QMainWindow):
    def __init__(self, root: Path, manager, auth=None):
        super().__init__()
        self.root, self.manager, self.auth = Path(root), manager, auth
        self.preferences = self._preferences()
        self.snapshot_data = {"jobs": [], "files": [], "history": []}
        self.auth_thread = None
        self.shutdown_thread = None
        self.auth_cancel = threading.Event()
        self.closing = False
        self.can_close = False
        self._jobs_signature = None
        self.setWindowTitle("Driver Downloader")
        self.resize(1240, 850)
        self.setMinimumSize(940, 680)
        self.setWindowIcon(official_icon())
        container = QWidget(objectName="page")
        self.setCentralWidget(container)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._sidebar())
        content = QWidget()
        content_box = QVBoxLayout(content)
        content_box.setContentsMargins(32, 27, 32, 22)
        content_box.setSpacing(19)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.page_title = QLabel("Seu próximo download começa aqui", objectName="pageTitle")
        self.page_subtitle = QLabel("Arquivos do Drive, direto para o seu computador.", objectName="muted")
        self.page_title.setWordWrap(True)
        self.page_subtitle.setWordWrap(True)
        titles.addWidget(self.page_title); titles.addWidget(self.page_subtitle)
        top.addLayout(titles, 1)
        self.account_status = QLabel("●  Acesso de leitura", objectName="status")
        top.addWidget(self.account_status)
        content_box.addLayout(top)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._home())
        self.stack.addWidget(self._downloads())
        self.stack.addWidget(self._history())
        self.stack.addWidget(self._settings())
        content_box.addWidget(self.stack, 1)
        self.footer = QLabel("Seus arquivos permanecem no seu computador. O Drive é acessado somente para leitura.", objectName="muted")
        self.footer.setWordWrap(True)
        content_box.addWidget(self.footer)
        layout.addWidget(content, 1)
        self.effect = QGraphicsOpacityEffect(self.stack)
        self.effect.setOpacity(1.0)
        self.stack.setGraphicsEffect(self.effect)
        self.animation = QPropertyAnimation(self.effect, b"opacity", self)
        self.animation.setDuration(170)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.apply_theme(self.preferences.get("theme", "dark"))
        self.refresh()

    def _preferences(self):
        try:
            return json.loads((self.root / "preferences.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    def _save_preferences(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "preferences.json"
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps(self.preferences, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(path)

    def _sidebar(self):
        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(214)
        box = QVBoxLayout(sidebar)
        box.setContentsMargins(20, 30, 20, 22)
        box.setSpacing(8)
        brand_row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(official_icon().pixmap(32, 32))
        brand_row.addWidget(logo)
        brand_row.addWidget(QLabel("Driver Downloader", objectName="brand"), 1)
        box.addLayout(brand_row)
        tagline = QLabel("DRIVE  /  DOWNLOADS", objectName="eyebrow")
        box.addWidget(tagline)
        box.addSpacing(33)
        self.nav = []
        for number, (label, glyph) in enumerate((("Início", "home"), ("Downloads", "download"), ("Histórico", "history"), ("Configurações", "settings"))):
            button = QPushButton(label, objectName="nav")
            button.setIcon(icon(glyph))
            button.setIconSize(QSize(21, 21))
            button.setCheckable(True)
            button.setChecked(number == 0)
            button.clicked.connect(lambda checked=False, page=number: self.navigate(page))
            box.addWidget(button)
            self.nav.append(button)
        box.addStretch()
        security = QLabel()
        security.setPixmap(icon("shield", "#6ed4b2", 26).pixmap(26, 26))
        box.addWidget(security)
        box.addWidget(QLabel("Seu Drive protegido", objectName="section"))
        description = QLabel("Sem ZIP. Sem alterações\nnos arquivos da origem.", objectName="muted")
        description.setWordWrap(True)
        box.addWidget(description)
        box.addSpacing(15)
        box.addWidget(QLabel("VERSÃO 2.0", objectName="muted"))
        return sidebar

    @staticmethod
    def card(hero=False):
        frame = QFrame(objectName="hero" if hero else "card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(23, 21, 23, 21)
        layout.setSpacing(13)
        return frame, layout

    @staticmethod
    def scroll(body):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        return scroll

    @staticmethod
    def page():
        page = QWidget(objectName="page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        return page, layout

    def _home(self):
        page, box = self.page()
        hero, body = self.card(True)
        line = QHBoxLayout()
        line.addWidget(QLabel("DOWNLOADS SEM COMPLICAÇÃO", objectName="eyebrow"))
        line.addStretch()
        line.addWidget(QLabel("GOOGLE DRIVE", objectName="badge"))
        body.addLayout(line)
        body.addWidget(QLabel("Grandes arquivos.\nUm caminho mais simples.", objectName="heroTitle"))
        description = QLabel("Cole um link de arquivo ou pasta. A estrutura original é preservada,\ne cada arquivo vai direto para o destino que você escolher.", objectName="muted")
        description.setWordWrap(True)
        body.addWidget(description)
        body.addSpacing(3)
        body.addWidget(QLabel("Link do Google Drive", objectName="section"))
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://drive.google.com/drive/folders/…")
        self.url.setAccessibleName("Link de arquivo ou pasta do Google Drive")
        self.url.setClearButtonEnabled(True)
        self.url.returnPressed.connect(self.start_download)
        body.addWidget(self.url)
        body.addWidget(QLabel("Pasta de destino", objectName="section"))
        row = QHBoxLayout()
        self.destination = QLineEdit(str(self.preferences.get("destination", "")))
        self.destination.setPlaceholderText("Escolha onde salvar seus arquivos")
        self.destination.setAccessibleName("Pasta de destino")
        browse = QPushButton("Escolher pasta")
        browse.setIcon(icon("folder"))
        browse.clicked.connect(self.browse_destination)
        row.addWidget(self.destination, 1); row.addWidget(browse)
        body.addLayout(row)
        action = QHBoxLayout()
        note = QLabel("Retomada segura · Subpastas incluídas · Sem compactação", objectName="muted")
        note.setWordWrap(True)
        action.addWidget(note, 1)
        self.start_button = QPushButton("Iniciar download", objectName="primary")
        self.start_button.setIcon(icon("download", "#ffffff"))
        self.start_button.clicked.connect(self.start_download)
        action.addWidget(self.start_button)
        body.addLayout(action)
        box.addWidget(hero)
        stats = QHBoxLayout()
        stats.setSpacing(14)
        self.home_active = self.make_metric(stats, "EM ANDAMENTO", "0")
        self.home_completed = self.make_metric(stats, "CONCLUÍDOS", "0")
        self.home_speed = self.make_metric(stats, "VELOCIDADE TOTAL", "0 B/s")
        box.addLayout(stats)
        self.home_notice = QLabel("Pronto para começar. O acesso e o espaço disponível serão verificados antes de baixar.", objectName="notice")
        self.home_notice.setWordWrap(True)
        box.addWidget(self.home_notice)
        box.addStretch()
        return self.scroll(page)

    def make_metric(self, row, label, value):
        card, body = self.card()
        body.addWidget(QLabel(label, objectName="muted"))
        metric = QLabel(value, objectName="metric")
        body.addWidget(metric)
        row.addWidget(card, 1)
        return metric

    def _downloads(self):
        page, box = self.page()
        totals = QHBoxLayout()
        totals.setSpacing(14)
        self.download_volume = self.make_metric(totals, "TRANSFERIDO", "0 B")
        self.download_speed = self.make_metric(totals, "VELOCIDADE", "0 B/s")
        self.download_eta = self.make_metric(totals, "TEMPO RESTANTE", "—")
        box.addLayout(totals)
        card, body = self.card()
        row = QHBoxLayout()
        row.addWidget(QLabel("Fila de downloads", objectName="section"))
        row.addStretch()
        self.job_selector = QComboBox()
        self.job_selector.setMinimumWidth(220)
        self.job_selector.setAccessibleName("Selecionar download")
        self.job_selector.currentIndexChanged.connect(self.refresh_files)
        row.addWidget(self.job_selector)
        body.addLayout(row)
        actions = QHBoxLayout()
        self.pause_button = QPushButton("Pausar")
        self.pause_button.setIcon(icon("pause"))
        self.pause_button.clicked.connect(lambda: self.job_action("pause"))
        self.resume_button = QPushButton("Continuar")
        self.resume_button.setIcon(icon("play"))
        self.resume_button.clicked.connect(lambda: self.job_action("resume"))
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.setIcon(icon("stop"))
        self.cancel_button.clicked.connect(lambda: self.job_action("cancel"))
        self.open_button = QPushButton("Abrir destino")
        self.open_button.setIcon(icon("folder"))
        self.open_button.clicked.connect(self.open_selected_destination)
        for button in (self.pause_button, self.resume_button, self.cancel_button, self.open_button):
            actions.addWidget(button)
        actions.addStretch()
        body.addLayout(actions)
        self.files_model = DataTable()
        self.files_table = self.make_table(self.files_model)
        self.files_table.setItemDelegateForColumn(2, ProgressDelegate(self.files_table))
        self.files_table.selectionModel().currentRowChanged.connect(self.update_file_detail)
        body.addWidget(self.files_table, 1)
        self.download_notice = QLabel("Adicione um link na tela Início para começar.", objectName="muted")
        self.download_notice.setWordWrap(True)
        body.addWidget(self.download_notice)
        self.file_detail = QLabel("", objectName="notice")
        self.file_detail.setWordWrap(True)
        self.file_detail.hide()
        body.addWidget(self.file_detail)
        resume_hint = QLabel("Arquivos compatíveis retomam do ponto salvo. Documentos Google exportados reiniciam a exportação ao continuar.", objectName="muted")
        resume_hint.setWordWrap(True)
        body.addWidget(resume_hint)
        box.addWidget(card, 1)
        return page

    def _history(self):
        page, box = self.page()
        card, body = self.card()
        row = QHBoxLayout()
        row.addWidget(QLabel("Seus downloads anteriores", objectName="section"))
        row.addStretch()
        open_button = QPushButton("Abrir pasta selecionada")
        open_button.setIcon(icon("folder"))
        open_button.clicked.connect(self.open_history_destination)
        row.addWidget(open_button)
        body.addLayout(row)
        self.history_model = DataTable(history=True)
        self.history_table = self.make_table(self.history_model)
        self.history_table.doubleClicked.connect(lambda index: self.open_history_destination())
        body.addWidget(self.history_table, 1)
        body.addWidget(QLabel("O histórico e a fila são preservados ao fechar o aplicativo.", objectName="muted"))
        box.addWidget(card, 1)
        return page

    @staticmethod
    def make_table(model):
        table = QTableView()
        table.setModel(model)
        table.setShowGrid(False)
        table.setAlternatingRowColors(False)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(43)
        table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, model.columnCount()):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        if not model.history:
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(2, 115)
        table.setMinimumHeight(200)
        return table

    def _settings(self):
        page, box = self.page()
        general, body = self.card()
        body.addWidget(QLabel("Preferências de download", objectName="section"))
        body.addWidget(QLabel("Pasta padrão", objectName="muted"))
        row = QHBoxLayout()
        self.default_destination = QLineEdit(str(self.preferences.get("destination", "")))
        row.addWidget(self.default_destination, 1)
        choose = QPushButton("Escolher pasta")
        choose.clicked.connect(self.browse_default)
        row.addWidget(choose)
        body.addLayout(row)
        controls = QHBoxLayout()
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 8)
        self.concurrency.setValue(int(self.preferences.get("concurrency", 4)))
        self.concurrency.setAccessibleName("Downloads simultâneos")
        self.bandwidth = QDoubleSpinBox()
        self.bandwidth.setRange(0, 10240)
        self.bandwidth.setDecimals(1)
        self.bandwidth.setValue(float(self.preferences.get("bandwidth_mib", 0)))
        self.bandwidth.setSpecialValueText("Sem limite")
        self.bandwidth.setSuffix(" MiB/s")
        self.bandwidth.setAccessibleName("Limite de velocidade total")
        for label, widget in (("Downloads simultâneos", self.concurrency), ("Limite total de velocidade", self.bandwidth)):
            group = QVBoxLayout(); group.addWidget(QLabel(label, objectName="muted")); group.addWidget(widget); controls.addLayout(group, 1)
        body.addLayout(controls)
        hint = QLabel("Concorrência controlada e gravação em blocos de 64 KiB. Arquivos grandes não são carregados inteiros na memória.", objectName="muted")
        hint.setWordWrap(True); body.addWidget(hint)
        body.addWidget(QLabel("Aparência", objectName="muted"))
        self.theme = QComboBox()
        self.theme.addItem("Escuro", "dark"); self.theme.addItem("Claro", "light")
        self.theme.setCurrentIndex(1 if self.preferences.get("theme") == "light" else 0)
        self.theme.currentIndexChanged.connect(lambda: self.apply_theme(self.theme.currentData()))
        body.addWidget(self.theme)
        save_row = QHBoxLayout(); save_row.addStretch()
        save = QPushButton("Salvar preferências", objectName="primary")
        save.clicked.connect(self.save_settings); save_row.addWidget(save); body.addLayout(save_row)
        box.addWidget(general)
        account, account_body = self.card()
        account_body.addWidget(QLabel("Conta Google", objectName="section"))
        self.account_description = QLabel("A autorização será solicitada no navegador ao iniciar um download.", objectName="muted")
        self.account_description.setWordWrap(True); account_body.addWidget(self.account_description)
        buttons = QHBoxLayout()
        self.connect_button = QPushButton("Conectar com Google")
        self.connect_button.clicked.connect(self.connect_account)
        self.forget_button = QPushButton("Desconectar conta")
        self.forget_button.clicked.connect(self.forget_account)
        buttons.addWidget(self.connect_button); buttons.addWidget(self.forget_button); buttons.addStretch()
        account_body.addLayout(buttons)
        notice = QLabel("O login acontece no navegador. O aplicativo solicita somente leitura; os tokens ficam protegidos pela conta do Windows.", objectName="muted")
        notice.setWordWrap(True); account_body.addWidget(notice)
        box.addWidget(account)
        log_button = QPushButton("Abrir pasta de registros", objectName="quiet")
        log_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root))))
        box.addWidget(log_button, alignment=Qt.AlignmentFlag.AlignLeft)
        box.addStretch()
        return self.scroll(page)

    def navigate(self, page):
        self.stack.setCurrentIndex(page)
        for index, button in enumerate(self.nav):
            button.setChecked(index == page)
        titles = [("Seu próximo download começa aqui", "Arquivos do Drive, direto para o seu computador."),
                  ("Tudo sob controle", "Acompanhe, pause e retome suas transferências."),
                  ("Um histórico organizado", "Consulte seus downloads e encontre os arquivos salvos."),
                  ("Do seu jeito", "Ajuste o desempenho, a aparência e o acesso à conta.")]
        self.page_title.setText(titles[page][0]); self.page_subtitle.setText(titles[page][1])
        self.animation.stop(); self.animation.setStartValue(0.4); self.animation.setEndValue(1.0); self.animation.start()

    def apply_theme(self, theme):
        dark = theme != "light"
        palette = QPalette()
        colors = {
            QPalette.ColorRole.Window: "#0b101b" if dark else "#f2f5fb",
            QPalette.ColorRole.WindowText: "#edf1fa" if dark else "#14213c",
            QPalette.ColorRole.Base: "#131c2d" if dark else "#ffffff",
            QPalette.ColorRole.Text: "#edf1fa" if dark else "#14213c",
            QPalette.ColorRole.Button: "#131c2d" if dark else "#ffffff",
            QPalette.ColorRole.ButtonText: "#edf1fa" if dark else "#14213c",
            QPalette.ColorRole.Highlight: "#7276ee",
            QPalette.ColorRole.HighlightedText: "#ffffff",
            QPalette.ColorRole.PlaceholderText: "#8e9eb9" if dark else "#64718a",
        }
        for role, color in colors.items():
            palette.setColor(role, QColor(color))
        self.setPalette(palette)
        self.setStyleSheet(stylesheet(theme == "light"))

    def browse_destination(self):
        path = QFileDialog.getExistingDirectory(self, "Escolha a pasta de destino", self.destination.text())
        if path: self.destination.setText(path)

    def browse_default(self):
        path = QFileDialog.getExistingDirectory(self, "Escolha a pasta padrão", self.default_destination.text())
        if path: self.default_destination.setText(path)

    def start_download(self):
        if self.closing: return
        url, destination = self.url.text().strip(), self.destination.text().strip()
        if not url or not destination:
            self.home_notice.setText("Informe o link do Drive e escolha uma pasta de destino antes de iniciar.")
            (self.url if not url else self.destination).setFocus()
            return
        try:
            job_id = self.manager.add(url, str(Path(destination).absolute()))
            self.preferences["destination"] = destination
            self.default_destination.setText(destination)
            self._save_preferences()
            self.home_notice.setText("Download adicionado. O acesso e os arquivos serão verificados em segundo plano.")
            self.refresh()
            index = self.job_selector.findData(job_id)
            if index >= 0: self.job_selector.setCurrentIndex(index)
            self.navigate(1)
        except Exception as exc:
            self.home_notice.setText(str(exc))

    def selected_job(self):
        selected = self.job_selector.currentData()
        return next((j for j in self.snapshot_data.get("jobs", []) if j.get("id") == selected), None)

    def job_action(self, action):
        job = self.selected_job()
        if not job: return
        try:
            getattr(self.manager, action)(job["id"])
            self.refresh()
        except Exception as exc:
            self.download_notice.setText(str(exc))

    def refresh(self):
        if self.closing: return
        try:
            self.snapshot_data = self.manager.snapshot()
        except Exception:
            self.footer.setText("Não foi possível atualizar a fila. Consulte os registros do aplicativo.")
            return
        jobs = self.snapshot_data.get("jobs", [])
        running = [j for j in jobs if j.get("state") in ("running", "planning", "verifying", "queued", "retrying", "pausing", "cancelling", "checking")]
        completed = [j for j in jobs if j.get("state") == "completed"]
        speed = sum(j.get("speed", 0) or 0 for j in jobs)
        self.home_active.setText(str(len(running)))
        self.home_completed.setText(str(len(completed)))
        self.home_speed.setText(byte_text(speed) + "/s")
        self.download_volume.setText(byte_text(sum(j.get("done", 0) or 0 for j in jobs)))
        self.download_speed.setText(byte_text(speed) + "/s")
        etas = [j.get("eta") for j in running if j.get("eta") is not None]
        self.download_eta.setText(eta_text(max(etas)) if etas else "—")
        signature = [(j.get("id"), j.get("name"), j.get("state")) for j in jobs]
        if signature != self._jobs_signature:
            previous = self.job_selector.currentData()
            self.job_selector.blockSignals(True); self.job_selector.clear()
            for job in jobs:
                label = f"{job.get('name') or 'Preparando…'} · {STATES.get(job.get('state'), 'Na fila')}"
                self.job_selector.addItem(label, job.get("id"))
            index = self.job_selector.findData(previous)
            self.job_selector.setCurrentIndex(index if index >= 0 else max(0, len(jobs) - 1))
            self.job_selector.blockSignals(False)
            self._jobs_signature = signature
        self.refresh_files()
        history = self.snapshot_data.get("history")
        if history is None:
            history = [j for j in jobs if j.get("state") in ("completed", "cancelled", "error")]
        self.history_model.replace(list(history))
        connected = bool(self.auth and self.auth.connected)
        self.account_status.setText("●  Google conectado" if connected else "●  Acesso de leitura")
        self.account_description.setText("Conta autorizada para ler os arquivos acessíveis no Google Drive." if connected else "A autorização será solicitada no navegador ao iniciar um download.")
        active_auth = self.auth_thread is not None
        self.connect_button.setEnabled(self.auth is not None and not active_auth)
        self.forget_button.setEnabled(connected and not active_auth and not running)
        self.forget_button.setToolTip("Pause os downloads antes de desconectar." if running else "Remove apenas a autorização salva neste computador.")

    def refresh_files(self):
        job = self.selected_job()
        state = job.get("state") if job else ""
        self.pause_button.setEnabled(state in ("queued", "planning", "running", "retrying", "verifying", "checking"))
        self.resume_button.setEnabled(state in ("paused", "error", "cancelled"))
        self.cancel_button.setEnabled(bool(job) and state not in ("completed", "cancelled", "pausing", "cancelling"))
        self.open_button.setEnabled(bool(job and job.get("destination")))
        rows = [f for f in self.snapshot_data.get("files", []) if not job or f.get("job_id") == job.get("id")]
        current = self.files_table.currentIndex()
        selected_id = self.files_model.rows[current.row()].get("id") if current.isValid() and current.row() < len(self.files_model.rows) else None
        self.files_model.replace(rows)
        if selected_id is not None:
            selected_row = next((index for index, row in enumerate(rows) if row.get("id") == selected_id), None)
            if selected_row is not None:
                self.files_table.setCurrentIndex(self.files_model.index(selected_row, 0))
        self.update_file_detail()
        if not job:
            self.download_notice.setText("Adicione um link na tela Início para começar.")
        elif job.get("error"):
            self.download_notice.setText(str(job["error"]))
        elif not rows:
            self.download_notice.setText("Verificando permissões e preparando a lista de arquivos…" if state == "planning" else STATES.get(state, "Pronto"))
        else:
            complete = job.get("completed_files", sum(f.get("state") == "completed" for f in rows))
            total = job.get("file_count", len(rows))
            visible = f" · {len(rows)} arquivos exibidos nesta lista" if total > len(rows) else ""
            self.download_notice.setText(f"{complete} de {total} arquivos concluídos{visible} · " +
                                         ("Pausado. Clique em Continuar para retomar com segurança." if state == "paused" else "Selecione uma linha para consultar os detalhes do arquivo."))

    def update_file_detail(self, *_):
        current = self.files_table.currentIndex()
        text = ""
        if current.isValid() and current.row() < len(self.files_model.rows):
            row = self.files_model.rows[current.row()]
            note = row.get("note")
            if not note and row.get("resumable") is False:
                note = "Este arquivo reinicia do início ao continuar; a origem não oferece retomada por bytes."
            text = "\n".join(str(part) for part in (row.get("error"), note) if part)
        self.file_detail.setText(text)
        self.file_detail.setVisible(bool(text))

    def open_selected_destination(self):
        job = self.selected_job()
        if job: self.open_destination(job.get("destination"))

    def open_history_destination(self):
        index = self.history_table.currentIndex()
        if index.isValid():
            self.open_destination(self.history_model.rows[index.row()].get("destination"))

    @staticmethod
    def open_destination(path):
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def save_settings(self):
        try:
            self.preferences.update(destination=self.default_destination.text().strip(),
                                    concurrency=self.concurrency.value(), bandwidth_mib=self.bandwidth.value(), theme=self.theme.currentData())
            self.manager.set_limits(self.concurrency.value(), int(self.bandwidth.value() * 1024 * 1024))
            self._save_preferences()
            self.destination.setText(self.preferences["destination"])
            self.footer.setText("Preferências salvas. Os novos limites serão aplicados pelo gerenciador de downloads.")
        except Exception as exc:
            self.footer.setText(str(exc))

    def connect_account(self):
        if not self.auth or self.auth_thread is not None: return
        self.auth_cancel.clear()
        self.account_description.setText("Conclua a autorização no navegador. O aplicativo solicita apenas leitura.")
        self.auth_thread = Operation(lambda: self.auth.connect(self.auth_cancel))
        self.auth_thread.result.connect(self.auth_result)
        self.auth_thread.finished.connect(self.auth_finished)
        self.connect_button.setEnabled(False)
        self.auth_thread.start()

    def auth_result(self, success, message):
        self.footer.setText("Autorização Google concluída." if success else message)

    def auth_finished(self):
        if self.auth_thread:
            self.auth_thread.deleteLater(); self.auth_thread = None
        if self.closing:
            self.finish_close()
        else:
            self.refresh()

    def forget_account(self):
        if not self.auth: return
        try:
            self.auth.forget()
            self.footer.setText("Autorização local removida. Seu histórico e seus arquivos foram preservados.")
            self.refresh()
        except Exception as exc:
            self.footer.setText(str(exc))

    def closeEvent(self, event):
        if self.can_close:
            event.accept(); return
        event.ignore()
        if self.closing: return
        self.closing = True
        self.timer.stop()
        self.auth_cancel.set()
        self.centralWidget().setEnabled(False)
        self.footer.setText("Pausando transferências e salvando a fila para a próxima abertura…")
        self.shutdown_thread = Operation(self.manager.shutdown)
        self.shutdown_thread.result.connect(lambda success, message: self.footer.setText("Fila salva." if success else message))
        self.shutdown_thread.finished.connect(self.finish_close)
        self.shutdown_thread.start()

    def finish_close(self):
        shutdown_done = self.shutdown_thread is None or not self.shutdown_thread.isRunning()
        auth_done = self.auth_thread is None or not self.auth_thread.isRunning()
        if self.closing and shutdown_done and auth_done:
            self.can_close = True
            QTimer.singleShot(0, self.close)
