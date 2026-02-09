# Codey.py
# A lightweight multi-language editor using PyQt6 with optional CodeyLinter.
# Enhanced version with icons, better styling, and bug fixes.

import os
import shutil
import sqlite3
import sys
import threading
import time

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    HAS_PYQT = True
except Exception as exc:
    HAS_PYQT = False
    sys.stderr.write("PyQt6 is required. Install PyQt6 and try again.\n")
    sys.stderr.write("Error: %s\n" % exc)
    raise

# CodeyLinter integration (local module)
try:
    import CodeyLinter  # type: ignore
    HAS_CODEY_LINTER = True
except Exception:
    CodeyLinter = None
    HAS_CODEY_LINTER = False


class FallbackLinter(object):
    """Minimal fallback linter when CodeyLinter is unavailable."""

    def lint(self, text, language):
        diagnostics = []
        if language == 'python':
            try:
                compile(text, '<buffer>', 'exec')
            except SyntaxError as exc:
                diagnostics.append({
                    'line': exc.lineno or 1,
                    'col': exc.offset or 1,
                    'message': exc.msg,
                    'severity': 'error',
                })
        return diagnostics


class LineNumberArea(QtWidgets.QWidget):
    def __init__(self, editor):
        super(LineNumberArea, self).__init__(editor)
        self.code_editor = editor

    def sizeHint(self):
        return QtCore.QSize(self.code_editor.lineNumberAreaWidth(), 0)

    def paintEvent(self, event):
        self.code_editor.lineNumberAreaPaintEvent(event)


class CodeEditor(QtWidgets.QPlainTextEdit):
    def __init__(self, parent=None):
        super(CodeEditor, self).__init__(parent)
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self.updateLineNumberAreaWidth)
        self.updateRequest.connect(self.updateLineNumberArea)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self.updateLineNumberAreaWidth(0)

    def lineNumberAreaWidth(self):
        digits = len(str(max(1, self.blockCount())))
        space = 3 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def updateLineNumberAreaWidth(self, _):
        self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

    def updateLineNumberArea(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.updateLineNumberAreaWidth(0)

    def resizeEvent(self, event):
        super(CodeEditor, self).resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(
            QtCore.QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height())
        )

    def lineNumberAreaPaintEvent(self, event):
        painter = QtGui.QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QtGui.QColor('#0f1115'))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QtGui.QColor('#6b7089'))
                painter.drawText(
                    0,
                    int(top),
                    self.line_number_area.width() - 4,
                    int(self.fontMetrics().height()),
                    QtCore.Qt.AlignmentFlag.AlignRight,
                    number,
                )
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1

    def _highlight_current_line(self):
        extra_selections = []
        if not self.isReadOnly():
            selection = QtWidgets.QTextEdit.ExtraSelection()
            selection.format.setBackground(QtGui.QColor('#151a22'))
            selection.format.setProperty(QtGui.QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)


class EditorTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(EditorTab, self).__init__(parent)
        self.editor = CodeEditor()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.editor)

        self.path = None
        self.lang = 'python'
        self.is_modified = False
        self.highlighter = None


class LintWorker(QtCore.QThread):
    result = QtCore.pyqtSignal(list)
    error = QtCore.pyqtSignal(str)

    def __init__(self, text, language, file_path=None, parent=None):
        super(LintWorker, self).__init__(parent)
        self._text = text
        self._language = language
        self._file_path = file_path

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            if HAS_CODEY_LINTER and hasattr(CodeyLinter, 'lint'):
                diagnostics = CodeyLinter.lint(self._text, self._language, self._file_path)
            else:
                diagnostics = FallbackLinter().lint(self._text, self._language)
            if self.isInterruptionRequested():
                return
            self.result.emit(diagnostics)
        except Exception as exc:
            self.error.emit(str(exc))


class CodeyHighlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, document, language):
        super(CodeyHighlighter, self).__init__(document)
        self.language = language
        self.rules = []
        self._build_rules()

    def _fmt(self, color, bold=False, italic=False):
        fmt = QtGui.QTextCharFormat()
        fmt.setForeground(QtGui.QColor(color))
        if bold:
            fmt.setFontWeight(QtGui.QFont.Weight.Bold)
        if italic:
            fmt.setFontItalic(True)
        return fmt

    def _build_rules(self):
        # Shared formats
        keyword_fmt = self._fmt('#7aa2f7', bold=True)
        type_fmt = self._fmt('#bb9af7', bold=True)
        string_fmt = self._fmt('#9ece6a')
        comment_fmt = self._fmt('#6b7089', italic=True)
        number_fmt = self._fmt('#ff9e64')

        if self.language == 'python':
            keywords = [
                'and', 'as', 'assert', 'break', 'class', 'continue', 'def',
                'del', 'elif', 'else', 'except', 'False', 'finally', 'for',
                'from', 'global', 'if', 'import', 'in', 'is', 'lambda',
                'None', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return',
                'True', 'try', 'while', 'with', 'yield',
            ]
            builtins = [
                'print', 'len', 'range', 'str', 'int', 'float', 'dict', 'list',
                'set', 'tuple', 'open', 'type', 'isinstance',
            ]
            for word in keywords:
                self.rules.append((r'\b%s\b' % word, keyword_fmt))
            for word in builtins:
                self.rules.append((r'\b%s\b' % word, type_fmt))
            self.rules.append((r'#.*', comment_fmt))
            self.rules.append((r'\".*?\"', string_fmt))
            self.rules.append((r"\'.*?\'", string_fmt))
            self.rules.append((r'\b[0-9]+(\.[0-9]+)?\b', number_fmt))
        else:
            keywords = [
                'auto', 'break', 'case', 'char', 'const', 'continue', 'default',
                'do', 'double', 'else', 'enum', 'extern', 'float', 'for',
                'goto', 'if', 'inline', 'int', 'long', 'register', 'return',
                'short', 'signed', 'sizeof', 'static', 'struct', 'switch',
                'typedef', 'union', 'unsigned', 'void', 'volatile', 'while',
                'class', 'public', 'private', 'protected', 'template', 'typename',
                'namespace', 'using', 'new', 'delete', 'this', 'virtual', 'override',
                'nullptr', 'bool', 'true', 'false',
            ]
            for word in keywords:
                self.rules.append((r'\b%s\b' % word, keyword_fmt))
            self.rules.append((r'//.*', comment_fmt))
            self.rules.append((r'/\*.*\*/', comment_fmt))
            self.rules.append((r'\".*?\"', string_fmt))
            self.rules.append((r"\'.*?\'", string_fmt))
            self.rules.append((r'\b[0-9]+(\.[0-9]+)?\b', number_fmt))

        self.rules = [(QtCore.QRegularExpression(pat), fmt) for pat, fmt in self.rules]

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


class CodeyApp(QtWidgets.QMainWindow):
    LANG_PY = 'python'
    LANG_JS = 'javascript'
    LANG_C = 'c'
    LANG_CPP = 'cpp'

    def __init__(self):
        super(CodeyApp, self).__init__()
        self.setWindowTitle('Codey - Code Editor')
        self.resize(1100, 750)
        
        # Set window icon
        self._set_window_icon()

        self.current_lang = self.LANG_PY

        self._lint_timer = QtCore.QTimer(self)
        self._lint_timer.setSingleShot(True)
        self._lint_timer.timeout.connect(self.run_lint)
        self._diagnostics = []
        self._run_process = None
        self._terminal_process = None
        self._lint_worker = None
        self._lint_pending = None
        self._is_closing = False
        self._pending_close = False

        self._autosave_timer = QtCore.QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave_draft)

        self._build_editor()
        self._build_status()
        self._build_menu()
        self._build_toolbar()
        self._build_sidebar()
        self._build_bottom_panel()
        self._build_terminal_panel()
        self._build_top_bar()
        self._apply_styles()

        self.linter = self._init_linter()

        self._init_storage()
        self._init_freeze_handler()

        app = QtWidgets.QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._shutdown_threads)
        
        self._update_window_title()
        self._apply_syntax_highlighting()

    def _set_window_icon(self):
        """Set a simple window icon using built-in style."""
        icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_FileIcon)
        self.setWindowIcon(icon)

    def _create_icon(self, standard_pixmap):
        """Helper to create icons from standard pixmaps."""
        return self.style().standardIcon(standard_pixmap)

    def _init_linter(self):
        if HAS_CODEY_LINTER:
            try:
                return CodeyLinter
            except Exception:
                return FallbackLinter()
        return FallbackLinter()

    def _build_menu(self):
        menubar = self.menuBar()

        # File Menu with icons
        file_menu = menubar.addMenu('📁 File')
        
        open_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_DialogOpenButton), '&Open', self)
        open_action.setShortcut('Ctrl+O')
        open_action.triggered.connect(self.on_open)
        
        save_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton), '&Save', self)
        save_action.setShortcut('Ctrl+S')
        save_action.triggered.connect(self.on_save)
        
        save_as_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_DriveFDIcon), 'Save &As...', self)
        save_as_action.setShortcut('Ctrl+Shift+S')
        save_as_action.triggered.connect(self.on_save_as)
        
        new_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_FileIcon), '&New', self)
        new_action.setShortcut('Ctrl+N')
        new_action.triggered.connect(self.on_new)
        
        quit_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_DialogCloseButton), '&Quit', self)
        quit_action.setShortcut('Ctrl+Q')
        quit_action.triggered.connect(self.close)
        
        file_menu.addAction(new_action)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        file_menu.addAction(save_action)
        file_menu.addAction(save_as_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        # Edit Menu
        edit_menu = menubar.addMenu('✏️ Edit')
        
        undo_action = QtGui.QAction('Undo', self)
        undo_action.setShortcut('Ctrl+Z')
        undo_action.triggered.connect(lambda: self._current_editor().undo() if self._current_editor() else None)
        
        redo_action = QtGui.QAction('Redo', self)
        redo_action.setShortcut('Ctrl+Y')
        redo_action.triggered.connect(lambda: self._current_editor().redo() if self._current_editor() else None)
        
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        edit_menu.addSeparator()
        
        find_action = QtGui.QAction('Find', self)
        find_action.setShortcut('Ctrl+F')
        find_action.triggered.connect(lambda: self.search_input.setFocus())
        edit_menu.addAction(find_action)

        # Language Menu with icons
        lang_menu = menubar.addMenu('🔧 Language')
        
        py_action = QtGui.QAction('🐍 Python', self)
        js_action = QtGui.QAction('🟨 JavaScript', self)
        c_action = QtGui.QAction('C', self)
        cpp_action = QtGui.QAction('C++', self)
        
        py_action.triggered.connect(lambda: self.set_language(self.LANG_PY))
        js_action.triggered.connect(lambda: self.set_language(self.LANG_JS))
        c_action.triggered.connect(lambda: self.set_language(self.LANG_C))
        cpp_action.triggered.connect(lambda: self.set_language(self.LANG_CPP))
        
        lang_menu.addAction(py_action)
        lang_menu.addAction(js_action)
        lang_menu.addAction(c_action)
        lang_menu.addAction(cpp_action)

        # Lint Menu with icons
        lint_menu = menubar.addMenu('🔍 Lint')
        
        run_lint_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_BrowserReload), 'Run Lint', self)
        run_lint_action.setShortcut('F5')
        run_lint_action.triggered.connect(self.run_lint)
        
        clear_lint_action = QtGui.QAction('Clear Results', self)
        clear_lint_action.triggered.connect(self._clear_diagnostics)
        
        lint_menu.addAction(run_lint_action)
        lint_menu.addAction(clear_lint_action)
        
        # Run Menu
        run_menu = menubar.addMenu('▶ Run')
        run_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_MediaPlay), 'Run', self)
        run_action.setShortcut('F6')
        run_action.triggered.connect(self.run_file)
        run_menu.addAction(run_action)

        # Tools Menu
        tools_menu = menubar.addMenu('🧰 Tools')
        screenshot_action = QtGui.QAction('Screenshot', self)
        screenshot_action.setShortcut('Ctrl+Shift+P')
        screenshot_action.triggered.connect(self.take_screenshot)
        tools_menu.addAction(screenshot_action)
        
        # Help Menu
        help_menu = menubar.addMenu('❓ Help')
        
        about_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation), 'About', self)
        about_action.triggered.connect(self._show_about)
        
        help_menu.addAction(about_action)

    def _build_editor(self):
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        self._new_tab()

    def _current_tab(self):
        return self.tabs.currentWidget()

    def _current_editor(self):
        tab = self._current_tab()
        return tab.editor if tab else None

    def _new_tab(self, path=None, content=''):
        tab = EditorTab()
        tab.editor.setTabStopDistance(4 * tab.editor.fontMetrics().horizontalAdvance(' '))
        tab.editor.setFont(QtWidgets.QApplication.font())
        tab.editor.textChanged.connect(lambda: self._on_buffer_changed())
        tab.editor.cursorPositionChanged.connect(self._update_cursor_position)
        tab.path = path
        tab.lang = self._infer_language_from_path(path) if path else self.LANG_PY
        tab.is_modified = False
        tab.editor.setPlainText(content)
        tab.highlighter = CodeyHighlighter(tab.editor.document(), tab.lang)

        title = os.path.basename(path) if path else 'Untitled'
        index = self.tabs.addTab(tab, title)
        self.tabs.setCurrentIndex(index)
        self.current_lang = tab.lang
        self._update_window_title()

    def _close_tab(self, index):
        tab = self.tabs.widget(index)
        if not tab:
            return
        if tab.is_modified:
            reply = QtWidgets.QMessageBox.question(
                self, 'Unsaved Changes',
                'Do you want to save changes before closing this tab?',
                QtWidgets.QMessageBox.StandardButton.Save |
                QtWidgets.QMessageBox.StandardButton.Discard |
                QtWidgets.QMessageBox.StandardButton.Cancel
            )
            if reply == QtWidgets.QMessageBox.StandardButton.Save:
                self.tabs.setCurrentIndex(index)
                self.on_save()
            elif reply == QtWidgets.QMessageBox.StandardButton.Cancel:
                return
        if tab.path:
            self._clear_draft_for_path(tab.path)
        self.tabs.removeTab(index)
        if self.tabs.count() == 0:
            self._new_tab()

    def _on_tab_changed(self, _index):
        tab = self._current_tab()
        if not tab:
            return
        self.current_lang = tab.lang
        if hasattr(self, 'lang_combo'):
            lang_map_reverse = {self.LANG_PY: 'Python', self.LANG_JS: 'JavaScript', self.LANG_C: 'C', self.LANG_CPP: 'C++'}
            if tab.lang in lang_map_reverse:
                self.lang_combo.blockSignals(True)
                self.lang_combo.setCurrentText(lang_map_reverse[tab.lang])
                self.lang_combo.blockSignals(False)
        self._update_window_title()
        self._update_cursor_position()
        self._apply_syntax_highlighting()

    def _build_toolbar(self):
        toolbar = QtWidgets.QToolBar('Main')
        toolbar.setMovable(False)
        toolbar.setIconSize(QtCore.QSize(24, 24))
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)

        # New file action
        new_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_FileIcon), 'New', self)
        new_action.setToolTip('New File (Ctrl+N)')
        new_action.triggered.connect(self.on_new)
        
        # Open action
        open_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_DialogOpenButton), 'Open', self)
        open_action.setToolTip('Open File (Ctrl+O)')
        open_action.triggered.connect(self.on_open)
        
        # Save action
        save_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton), 'Save', self)
        save_action.setToolTip('Save File (Ctrl+S)')
        save_action.triggered.connect(self.on_save)
        
        # Lint action
        lint_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_BrowserReload), 'Lint', self)
        lint_action.setToolTip('Run Linter (F5)')
        lint_action.triggered.connect(self.run_lint)
        
        # Run action
        run_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_MediaPlay), 'Run', self)
        run_action.setToolTip('Run File (F6)')
        run_action.triggered.connect(self.run_file)

        # Screenshot action
        screenshot_action = QtGui.QAction('Screenshot', self)
        screenshot_action.setToolTip('Save Screenshot (Ctrl+Shift+P)')
        screenshot_action.triggered.connect(self.take_screenshot)

        toolbar.addAction(new_action)
        toolbar.addAction(open_action)
        toolbar.addAction(save_action)
        toolbar.addSeparator()
        toolbar.addAction(lint_action)
        toolbar.addAction(run_action)
        toolbar.addAction(screenshot_action)
        
        # Add language selector to toolbar
        toolbar.addSeparator()
        lang_label = QtWidgets.QLabel(' Language: ')
        lang_label.setStyleSheet('color: #a6b0bb; padding: 0 5px;')
        toolbar.addWidget(lang_label)
        
        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.addItems(['Python', 'JavaScript', 'C', 'C++'])
        self.lang_combo.currentTextChanged.connect(self._on_lang_combo_changed)
        self.lang_combo.setMinimumWidth(100)
        toolbar.addWidget(self.lang_combo)

    def _on_lang_combo_changed(self, text):
        """Handle language combo box changes."""
        lang_map = {'Python': self.LANG_PY, 'JavaScript': self.LANG_JS, 'C': self.LANG_C, 'C++': self.LANG_CPP}
        if text in lang_map:
            self.set_language(lang_map[text])

    def _build_sidebar(self):
        if hasattr(QtWidgets, 'QFileSystemModel'):
            self.fs_model = QtWidgets.QFileSystemModel()
            self.fs_model.setRootPath(QtCore.QDir.currentPath())

            self.file_tree = QtWidgets.QTreeView()
            self.file_tree.setModel(self.fs_model)
            self.file_tree.setRootIndex(self.fs_model.index(QtCore.QDir.currentPath()))
            self.file_tree.setHeaderHidden(True)
            for col in range(1, 4):
                self.file_tree.hideColumn(col)
            self.file_tree.doubleClicked.connect(self._open_from_tree)
            
            # Add file tree context menu
            self.file_tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
            self.file_tree.customContextMenuRequested.connect(self._show_file_context_menu)
        else:
            # Fallback for older PyQt6 builds missing QFileSystemModel
            self.fs_model = None
            self.file_tree = QtWidgets.QTreeWidget()
            self.file_tree.setHeaderHidden(True)
            self._populate_tree_widget()
            self.file_tree.itemDoubleClicked.connect(self._open_from_tree_widget)

        dock = QtWidgets.QDockWidget('📂 Files', self)
        dock.setWidget(self.file_tree)
        dock.setObjectName('FilesDock')
        self.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def _show_file_context_menu(self, position):
        """Show context menu for file tree."""
        menu = QtWidgets.QMenu()
        
        refresh_action = menu.addAction('🔄 Refresh')
        refresh_action.triggered.connect(self._refresh_file_tree)
        
        menu.exec(self.file_tree.viewport().mapToGlobal(position))

    def _refresh_file_tree(self):
        """Refresh the file tree."""
        if self.fs_model:
            self.fs_model.setRootPath(QtCore.QDir.currentPath())
            self.file_tree.setRootIndex(self.fs_model.index(QtCore.QDir.currentPath()))

    def _build_bottom_panel(self):
        self.diagnostics_list = QtWidgets.QListWidget()
        self.diagnostics_list.itemActivated.connect(self._jump_to_diagnostic)
        self.diagnostics_list.itemDoubleClicked.connect(self._jump_to_diagnostic)

        dock = QtWidgets.QDockWidget('🔍 Lint Output', self)
        dock.setWidget(self.diagnostics_list)
        dock.setObjectName('LintDock')
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, dock)
    
    def _build_terminal_panel(self):
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)

        self.terminal_output = QtWidgets.QPlainTextEdit()
        self.terminal_output.setReadOnly(True)
        self.terminal_output.setMaximumBlockCount(2000)

        self.terminal_input = QtWidgets.QLineEdit()
        self.terminal_input.setPlaceholderText('pwsh command... (Enter to run)')
        self.terminal_input.returnPressed.connect(self._send_terminal_command)

        layout.addWidget(self.terminal_output, 1)
        layout.addWidget(self.terminal_input, 0)

        dock = QtWidgets.QDockWidget('🖥 Terminal (pwsh.exe)', self)
        dock.setWidget(container)
        dock.setObjectName('TerminalDock')
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, dock)

        self._start_terminal()

    def _start_terminal(self):
        if self._terminal_process:
            return
        proc = QtCore.QProcess(self)
        proc.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: self.terminal_output.appendPlainText(
                bytes(proc.readAllStandardOutput()).decode(errors='replace')
            )
        )
        proc.readyReadStandardError.connect(
            lambda: self.terminal_output.appendPlainText(
                bytes(proc.readAllStandardError()).decode(errors='replace')
            )
        )
        proc.start('pwsh.exe', ['-NoLogo'])
        self._terminal_process = proc

    def _send_terminal_command(self):
        cmd = self.terminal_input.text().strip()
        if not cmd:
            return
        self.terminal_input.clear()
        if not self._terminal_process or self._terminal_process.state() != QtCore.QProcess.ProcessState.Running:
            self.terminal_output.appendPlainText('Terminal not running. Restarting...')
            self._terminal_process = None
            self._start_terminal()
        if self._terminal_process:
            self.terminal_output.appendPlainText('> ' + cmd)
            self._terminal_process.write((cmd + '\n').encode('utf-8'))

    def _build_top_bar(self):
        container = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 6)

        # Search section with icon
        search_label = QtWidgets.QLabel('🔍')
        search_label.setStyleSheet('color: #a6b0bb; font-size: 14px;')
        layout.addWidget(search_label)
        
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText('Search in file...')
        self.search_input.returnPressed.connect(self._search_next)
        self.search_input.setClearButtonEnabled(True)

        # Command section with icon
        command_label = QtWidgets.QLabel('⚡')
        command_label.setStyleSheet('color: #a6b0bb; font-size: 14px;')
        
        self.command_input = QtWidgets.QLineEdit()
        self.command_input.setPlaceholderText('Command: open | save | lint | goto:line')
        self.command_input.returnPressed.connect(self._run_command)
        self.command_input.setClearButtonEnabled(True)

        layout.addWidget(self.search_input, 2)
        layout.addWidget(command_label)
        layout.addWidget(self.command_input, 3)

        dock = QtWidgets.QDockWidget('', self)
        dock.setTitleBarWidget(QtWidgets.QWidget())
        dock.setWidget(container)
        dock.setObjectName('TopBarDock')
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.TopDockWidgetArea, dock)

    def _build_status(self):
        self.statusbar = QtWidgets.QStatusBar()
        self.setStatusBar(self.statusbar)
        
        # Add permanent widgets to status bar
        self.line_col_label = QtWidgets.QLabel('Line: 1, Col: 1')
        self.line_col_label.setStyleSheet('color: #a6b0bb; padding: 0 10px;')
        self.statusbar.addPermanentWidget(self.line_col_label)
        
        self.encoding_label = QtWidgets.QLabel('UTF-8')
        self.encoding_label.setStyleSheet('color: #a6b0bb; padding: 0 10px;')
        self.statusbar.addPermanentWidget(self.encoding_label)
        
        self.set_status('Ready')

    def _update_cursor_position(self):
        """Update cursor position in status bar."""
        editor = self._current_editor()
        if not editor:
            return
        cursor = editor.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        if hasattr(self, 'line_col_label'):
            self.line_col_label.setText(f'Line: {line}, Col: {col}')

    def _apply_styles(self):
        app_font = QtGui.QFont('JetBrains Mono', 12)
        QtWidgets.QApplication.setFont(app_font)
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if tab:
                tab.editor.setFont(app_font)

        # Enhanced dark UI with better teal accents and improved readability
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor('#1a1d23'))
        palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor('#e8eaed'))
        palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor('#13151a'))
        palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor('#e8eaed'))
        palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor('#1f2228'))
        palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor('#e8eaed'))
        palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor('#2cb5ad'))
        palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor('#0f1115'))
        self.setPalette(palette)

        # Enhanced stylesheet with better visual hierarchy
        self.setStyleSheet(
            "QMainWindow { background: #1a1d23; }"
            "QPlainTextEdit {"
            "  background: #13151a;"
            "  color: #e8eaed;"
            "  border: 1px solid #2a2f3a;"
            "  border-radius: 8px;"
            "  padding: 10px;"
            "  selection-background-color: #2cb5ad;"
            "  selection-color: #0f1115;"
            "}"
            "QMenuBar { background: #1a1d23; color: #e8eaed; border-bottom: 1px solid #2a2f3a; }"
            "QMenuBar::item { padding: 6px 12px; background: transparent; border-radius: 6px; }"
            "QMenuBar::item:selected { background: #252a35; border-radius: 3px; }"
            "QMenu { background: #1f2430; color: #e8eaed; border: 1px solid #2a2f3a; }"
            "QMenu::item { padding: 6px 30px 6px 20px; border-radius: 6px; }"
            "QMenu::item:selected { background: #2a2f3a; border-radius: 6px; }"
            "QTabWidget::pane { border: 1px solid #2a2f3a; }"
            "QTabBar::tab { background: #1f2228; color: #e8eaed; padding: 6px 10px; border-radius: 6px; }"
            "QTabBar::tab:selected { background: #252a35; }"
            "QStatusBar { background: #1a1d23; color: #a6b0bb; border-top: 1px solid #2a2f3a; }"
            "QToolBar { background: #1a1d23; border-bottom: 1px solid #2a2f3a; spacing: 5px; padding: 4px; }"
            "QToolButton { "
            "  color: #d7dde3; "
            "  padding: 6px 12px; "
            "  border-radius: 8px; "
            "  background: transparent; "
            "}"
            "QToolButton:hover { background: #252a35; }"
            "QToolButton:pressed { background: #2cb5ad; color: #0f1115; }"
            "QDockWidget { background: #1a1d23; color: #e8eaed; border: 1px solid #2a2f3a; }"
            "QDockWidget::title { "
            "  background: #1f2228; "
            "  padding: 8px; "
            "  border-bottom: 1px solid #2a2f3a; "
            "  font-weight: bold; "
            "  border-radius: 8px; "
            "}"
            "QListWidget { "
            "  background: #13151a; "
            "  color: #e8eaed; "
            "  border: 1px solid #2a2f3a; "
            "  border-radius: 8px; "
            "  padding: 6px; "
            "}"
            "QListWidget::item { "
            "  padding: 6px; "
            "  border-radius: 6px; "
            "}"
            "QListWidget::item:hover { background: #1f2430; }"
            "QListWidget::item:selected { background: #2cb5ad; color: #0f1115; }"
            "QTreeView { "
            "  background: #13151a; "
            "  color: #e8eaed; "
            "  border: 1px solid #2a2f3a; "
            "  border-radius: 8px; "
            "}"
            "QTreeView::item:hover { background: #1f2430; }"
            "QTreeView::item:selected { background: #2cb5ad; color: #0f1115; }"
            "QLineEdit { "
            "  background: #13151a; "
            "  color: #e8eaed; "
            "  border: 1px solid #2a2f3a; "
            "  border-radius: 8px; "
            "  padding: 10px; "
            "}"
            "QLineEdit:focus { border: 1px solid #2cb5ad; }"
            "QComboBox { "
            "  background: #13151a; "
            "  color: #e8eaed; "
            "  border: 1px solid #2a2f3a; "
            "  border-radius: 8px; "
            "  padding: 8px; "
            "}"
            "QComboBox:hover { border: 1px solid #2cb5ad; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView { "
            "  background: #1f2430; "
            "  color: #e8eaed; "
            "  selection-background-color: #2cb5ad; "
            "  selection-color: #0f1115; "
            "}"
        )

    def _init_storage(self):
        app_dir = os.path.join(os.path.expanduser('~'), '.codey')
        if not os.path.isdir(app_dir):
            try:
                os.makedirs(app_dir, exist_ok=True)
            except Exception:
                app_dir = os.getcwd()
        self._db_path = os.path.join(app_dir, 'codey.db')
        try:
            self._db = sqlite3.connect(self._db_path)
            cur = self._db.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS drafts ("
                " key TEXT PRIMARY KEY,"
                " path TEXT,"
                " content TEXT,"
                " updated_at INTEGER"
                ")"
            )
            self._db.commit()
        except Exception:
            self._db = None

    def _init_freeze_handler(self):
        self._last_heartbeat = time.time()
        self._freeze_log_path = os.path.join(
            os.path.expanduser('~'),
            '.codey',
            'codey_freeze.log'
        )
        self._heartbeat_timer = QtCore.QTimer(self)
        self._heartbeat_timer.setInterval(250)
        self._heartbeat_timer.timeout.connect(self._update_heartbeat)
        self._heartbeat_timer.start()

        def watcher():
            while True:
                time.sleep(1.0)
                if time.time() - self._last_heartbeat > 3.0:
                    try:
                        os.makedirs(os.path.dirname(self._freeze_log_path), exist_ok=True)
                        with open(self._freeze_log_path, 'a', encoding='utf-8') as f:
                            f.write('Freeze detected at %s\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
                    except Exception:
                        pass
        thread = threading.Thread(target=watcher, daemon=True)
        thread.start()

    def _update_heartbeat(self):
        self._last_heartbeat = time.time()

    def set_status(self, text):
        self.statusbar.showMessage(text, 5000)  # Show for 5 seconds

    def _on_buffer_changed(self, *_):
        tab = self._current_tab()
        if not tab:
            return
        if not tab.is_modified:
            tab.is_modified = True
            self._update_window_title()
        
        # Debounce linting during active edits
        if hasattr(self, '_lint_timer'):
            self._lint_timer.start(600)
        if hasattr(self, '_autosave_timer'):
            self._autosave_timer.start(800)

    def _update_window_title(self):
        """Update window title with file name and modified status."""
        title = 'Codey - '
        tab = self._current_tab()
        if tab and tab.path:
            title += os.path.basename(tab.path)
        else:
            title += 'Untitled'
        
        if tab and tab.is_modified:
            title += ' *'
        
        self.setWindowTitle(title)
        if tab:
            self._update_tab_title(tab)

    def _update_tab_title(self, tab):
        title = os.path.basename(tab.path) if tab.path else 'Untitled'
        if tab.is_modified:
            title += ' *'
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.setTabText(index, title)

    def set_language(self, lang):
        tab = self._current_tab()
        if not tab:
            return
        tab.lang = lang
        self.current_lang = lang
        
        # Update combo box without triggering signal
        lang_map_reverse = {self.LANG_PY: 'Python', self.LANG_JS: 'JavaScript', self.LANG_C: 'C', self.LANG_CPP: 'C++'}
        if lang in lang_map_reverse:
            self.lang_combo.blockSignals(True)
            self.lang_combo.setCurrentText(lang_map_reverse[lang])
            self.lang_combo.blockSignals(False)
        
        self.set_status(f'Language set to {lang_map_reverse.get(lang, lang)}')
        self._apply_syntax_highlighting()

    def _get_text(self):
        editor = self._current_editor()
        return editor.toPlainText() if editor else ''

    def _set_text(self, text):
        editor = self._current_editor()
        if not editor:
            return
        editor.setPlainText(text)
        tab = self._current_tab()
        if tab:
            tab.is_modified = False
        self._update_window_title()

    def _infer_language_from_path(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.py',):
            return self.LANG_PY
        if ext in ('.js', '.mjs', '.cjs', '.jsx'):
            return self.LANG_JS
        if ext in ('.c', '.h'):
            return self.LANG_C
        if ext in ('.cpp', '.cc', '.cxx', '.hpp', '.hh'):
            return self.LANG_CPP
        return self.current_lang

    def on_new(self, *_):
        """Create a new file."""
        tab = self._current_tab()
        if tab and tab.is_modified:
            reply = QtWidgets.QMessageBox.question(
                self, 'Unsaved Changes',
                'Do you want to save changes before creating a new file?',
                QtWidgets.QMessageBox.StandardButton.Save |
                QtWidgets.QMessageBox.StandardButton.Discard |
                QtWidgets.QMessageBox.StandardButton.Cancel
            )
            
            if reply == QtWidgets.QMessageBox.StandardButton.Save:
                self.on_save()
            elif reply == QtWidgets.QMessageBox.StandardButton.Cancel:
                return
        
        self._new_tab()
        self._clear_diagnostics()
        self.set_status('New file created')
        self._restore_draft_for_path(None)

    def on_open(self, *_):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Open File', os.getcwd(), 
            'All Supported Files (*.py *.c *.cpp *.h *.hpp *.cc *.cxx);;'
            'Python Files (*.py);;C/C++ Files (*.c *.cpp *.h *.hpp *.cc *.cxx);;'
            'All Files (*)')
        if not path:
            self.set_status('Open canceled')
            return
        if not os.path.exists(path):
            QtWidgets.QMessageBox.warning(self, 'Error', 'File not found!')
            self.set_status('Open failed: file not found')
            return
        self._open_path(path, new_tab=True)

    def on_save(self, *_):
        tab = self._current_tab()
        if not tab or not tab.path:
            return self.on_save_as()
        try:
            with open(tab.path, 'w', encoding='utf-8') as f:
                f.write(self._get_text())
            tab.is_modified = False
            self._update_window_title()
            self.set_status(f'✓ Saved: {os.path.basename(tab.path)}')
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, 'Save Error', str(exc))
            self.set_status(f'Save failed: {exc}')

    def on_save_as(self, *_):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save File', '', 
            'Python Files (*.py);;C Files (*.c);;C++ Files (*.cpp);;'
            'Header Files (*.h *.hpp);;All Files (*)')
        if path:
            tab = self._current_tab()
            if tab:
                tab.path = path
            self.set_language(self._infer_language_from_path(path))
            self.on_save()

    def _open_from_tree(self, index):
        path = self.fs_model.filePath(index)
        if os.path.isdir(path):
            return
        self._open_path(path, new_tab=True)

    def _populate_tree_widget(self):
        root_path = os.getcwd()
        root_item = QtWidgets.QTreeWidgetItem([os.path.basename(root_path) or root_path])
        root_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, root_path)
        self.file_tree.addTopLevelItem(root_item)
        self._add_tree_children(root_item, root_path)
        root_item.setExpanded(True)

    def _add_tree_children(self, parent_item, path):
        try:
            entries = os.listdir(path)
        except Exception:
            return

        dirs = []
        files = []
        for name in entries:
            if name.startswith('.'):
                continue
            full = os.path.join(path, name)
            if os.path.isdir(full):
                dirs.append(name)
            else:
                files.append(name)

        for dirname in sorted(dirs):
            full = os.path.join(path, dirname)
            child = QtWidgets.QTreeWidgetItem([f'📁 {dirname}'])
            child.setData(0, QtCore.Qt.ItemDataRole.UserRole, full)
            parent_item.addChild(child)
            self._add_tree_children(child, full)

        for filename in sorted(files):
            full = os.path.join(path, filename)
            icon = '📄'
            if filename.endswith('.py'):
                icon = '🐍'
            elif filename.endswith(('.c', '.cpp', '.h', '.hpp')):
                icon = '⚙️'
            child = QtWidgets.QTreeWidgetItem([f'{icon} {filename}'])
            child.setData(0, QtCore.Qt.ItemDataRole.UserRole, full)
            parent_item.addChild(child)

    def _open_from_tree_widget(self, item, _col):
        path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not path or os.path.isdir(path):
            return
        self._open_path(path, new_tab=True)

    def _open_path(self, path, new_tab=False):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            if new_tab:
                self._new_tab(path=path, content=content)
                tab = self._current_tab()
            else:
                tab = self._current_tab()
                if not tab:
                    return
                self._set_text(content)
                tab.path = path
            self.set_language(self._infer_language_from_path(path))
            self.set_status(f'✓ Opened: {os.path.basename(path)}')
            self._clear_diagnostics()
            self._restore_draft_for_path(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, 'Open Error', str(exc))
            self.set_status(f'Open failed: {exc}')

    def _search_next(self):
        term = self.search_input.text()
        if not term:
            return
        editor = self._current_editor()
        if not editor:
            return
        doc = editor.document()
        cursor = editor.textCursor()
        found = doc.find(term, cursor)
        if found.isNull():
            found = doc.find(term)
        if not found.isNull():
            editor.setTextCursor(found)
            self.set_status(f'Found: "{term}"')
        else:
            self.set_status(f'Not found: "{term}"')

    def _run_command(self):
        cmd = self.command_input.text().strip()
        if not cmd:
            return
        self.command_input.clear()
        
        if cmd == 'open':
            self.on_open()
            return
        if cmd == 'save':
            self.on_save()
            return
        if cmd == 'lint':
            self.run_lint()
            return
        if cmd == 'run':
            self.run_file()
            return
        if cmd.startswith('goto:'):
            try:
                line_no = int(cmd.split(':', 1)[1])
                editor = self._current_editor()
                if not editor:
                    return
                cursor = editor.textCursor()
                cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
                cursor.movePosition(
                    QtGui.QTextCursor.MoveOperation.Down,
                    QtGui.QTextCursor.MoveMode.MoveAnchor,
                    max(0, line_no - 1)
                )
                editor.setTextCursor(cursor)
                self.set_status(f'Jumped to line {line_no}')
            except Exception:
                self.set_status('Command error: invalid line number')
            return
        
        self.set_status(f'Unknown command: {cmd}')

    def run_lint(self):
        if self._is_closing:
            return
        text = self._get_text()
        tab = self._current_tab()
        lang = tab.lang if tab else self.current_lang
        
        try:
            if self._lint_worker and self._lint_worker.isRunning():
                self._lint_pending = (text, lang)
                return
            self._lint_worker = LintWorker(text, lang, file_path=tab.path if tab else None, parent=self)
            self._lint_worker.result.connect(self._on_lint_result)
            self._lint_worker.error.connect(self._on_lint_error)
            self._lint_worker.finished.connect(self._on_lint_finished)
            self.set_status('Linting...')
            self._lint_worker.start()
            return
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, 'Lint Error', str(exc))
            self.set_status(f'Lint error: {exc}')
            return

    def _on_lint_result(self, diagnostics):
        self._apply_lint_results(diagnostics)
        if self._lint_pending:
            if self._is_closing:
                self._lint_pending = None
                return
            text, lang = self._lint_pending
            self._lint_pending = None
            tab = self._current_tab()
            self._lint_worker = LintWorker(text, lang, file_path=tab.path if tab else None, parent=self)
            self._lint_worker.result.connect(self._on_lint_result)
            self._lint_worker.error.connect(self._on_lint_error)
            self._lint_worker.finished.connect(self._on_lint_finished)
            self._lint_worker.start()

    def _on_lint_error(self, message):
        QtWidgets.QMessageBox.warning(self, 'Lint Error', message)
        self.set_status(f'Lint error: {message}')

    def _on_lint_finished(self):
        self._lint_worker = None
        if self._pending_close:
            self._pending_close = False
            self.close()

    def _shutdown_threads(self):
        self._is_closing = True
        if self._lint_worker and self._lint_worker.isRunning():
            self._lint_worker.requestInterruption()
            self._lint_worker.wait(5000)
            if self._lint_worker.isRunning():
                self._lint_worker.terminate()
                self._lint_worker.wait(1000)
            if not self._lint_worker.isRunning():
                self._lint_worker = None
        if self._terminal_process:
            self._terminal_process.terminate()
            self._terminal_process.waitForFinished(1000)
            self._terminal_process = None
        return not (self._lint_worker and self._lint_worker.isRunning())

    def _apply_lint_results(self, diagnostics):
        self._diagnostics = diagnostics
        self.diagnostics_list.clear()

        for item in diagnostics:
            line = item.get('line', 1)
            col = item.get('col', 1)
            msg = item.get('message', 'issue')
            sev = item.get('severity', 'warning')

            icon = '❌' if sev == 'error' else '⚠️' if sev == 'warning' else 'ℹ️'
            text = f'{icon} [{sev.upper()}] Line {line}:{col} - {msg}'

            list_item = QtWidgets.QListWidgetItem(text)
            list_item.setData(QtCore.Qt.ItemDataRole.UserRole, item)

            if sev == 'error':
                list_item.setForeground(QtGui.QColor('#ff6b6b'))
            elif sev == 'warning':
                list_item.setForeground(QtGui.QColor('#ffd93d'))
            else:
                list_item.setForeground(QtGui.QColor('#6bcfff'))

            self.diagnostics_list.addItem(list_item)

        if not diagnostics:
            self.set_status('✓ Lint: No issues found')
            return

        error_count = sum(1 for d in diagnostics if d.get('severity') == 'error')
        warning_count = sum(1 for d in diagnostics if d.get('severity') == 'warning')

        msg = f'Lint: {error_count} error(s), {warning_count} warning(s)'
        self.set_status(msg)
    
    def run_file(self):
        tab = self._current_tab()
        if not tab:
            return
        if tab.is_modified or not tab.path:
            reply = QtWidgets.QMessageBox.question(
                self, 'Save Required',
                'Please save the file before running. Save now?',
                QtWidgets.QMessageBox.StandardButton.Save |
                QtWidgets.QMessageBox.StandardButton.Cancel
            )
            if reply == QtWidgets.QMessageBox.StandardButton.Save:
                self.on_save()
            else:
                return
        if not tab.path:
            return

        lang = tab.lang
        if lang == self.LANG_PY:
            self._run_python(tab.path)
        elif lang == self.LANG_JS:
            if not shutil.which('node'):
                QtWidgets.QMessageBox.warning(
                    self, 'No Node.js',
                    'No Node.js. Install Node.js to run JavaScript.'
                )
                self.set_status('Run: node not found')
                return
            self._run_command_process(['node', tab.path], 'node')
        elif lang == self.LANG_C:
            self._run_c_family(tab.path, is_cpp=False)
        elif lang == self.LANG_CPP:
            self._run_c_family(tab.path, is_cpp=True)
        else:
            self.set_status('Run: unsupported language')

    def _run_python(self, path):
        python = sys.executable or 'python'
        self._run_command_process([python, path], 'python')

    def _run_c_family(self, path, is_cpp):
        compiler = 'g++' if is_cpp else 'gcc'
        if not shutil.which(compiler):
            QtWidgets.QMessageBox.warning(
                self, 'No Compiler',
                'No Compiler. Install the compiler first.'
            )
            self.set_status('Run: compiler not found')
            return
        out_path = os.path.join(
            os.path.dirname(path),
            'codey_run.exe' if os.name == 'nt' else 'codey_run'
        )
        cmd = [compiler, path, '-o', out_path]
        self._run_command_process(cmd, compiler, run_after=out_path)

    def _run_command_process(self, cmd, label, run_after=None):
        if hasattr(self, 'terminal_output'):
            self.terminal_output.clear()
            self.terminal_output.appendPlainText('> ' + ' '.join(cmd))

        try:
            proc = QtCore.QProcess(self)
            proc.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
            proc.readyReadStandardOutput.connect(
                lambda: self.terminal_output.appendPlainText(
                    bytes(proc.readAllStandardOutput()).decode(errors='replace')
                ) if hasattr(self, 'terminal_output') else None
            )
            proc.finished.connect(
                lambda exit_code, _status: self._on_run_finished(
                    exit_code, label, run_after
                )
            )
            proc.start(cmd[0], cmd[1:])
            self._run_process = proc
            self.set_status(f'Running {label}...')
        except Exception as exc:
            if hasattr(self, 'terminal_output'):
                self.terminal_output.appendPlainText(str(exc))
            self.set_status('Run failed')

    def _on_run_finished(self, exit_code, label, run_after):
        if run_after and exit_code == 0:
            if hasattr(self, 'terminal_output'):
                self.terminal_output.appendPlainText('> ' + run_after)
            proc = QtCore.QProcess(self)
            proc.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
            proc.readyReadStandardOutput.connect(
                lambda: self.terminal_output.appendPlainText(
                    bytes(proc.readAllStandardOutput()).decode(errors='replace')
                ) if hasattr(self, 'terminal_output') else None
            )
            proc.finished.connect(
                lambda _code, _status: self.set_status('Run finished')
            )
            proc.start(run_after)
            self._run_process = proc
            return

        if exit_code == 0:
            self.set_status(f'{label} finished successfully')
        else:
            self.set_status(f'{label} failed (exit {exit_code})')

    def _clear_diagnostics(self):
        """Clear all diagnostic messages."""
        self._diagnostics = []
        self.diagnostics_list.clear()
        self.set_status('Diagnostics cleared')

    def _draft_key_for_path(self, path):
        if not path:
            return '__untitled__'
        return os.path.abspath(path)

    def _autosave_draft(self):
        if not self._db:
            return
        tab = self._current_tab()
        path = tab.path if tab else None
        key = self._draft_key_for_path(path)
        content = self._get_text()
        try:
            cur = self._db.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO drafts(key, path, content, updated_at) "
                "VALUES (?, ?, ?, strftime('%s','now'))",
                (key, path, content),
            )
            self._db.commit()
        except Exception:
            return

    def _restore_draft_for_path(self, path):
        if not self._db:
            return
        key = self._draft_key_for_path(path)
        try:
            cur = self._db.cursor()
            cur.execute("SELECT content FROM drafts WHERE key = ?", (key,))
            row = cur.fetchone()
        except Exception:
            return
        if not row:
            return
        if row[0] and row[0] != self._get_text():
            reply = QtWidgets.QMessageBox.question(
                self, 'Restore Draft',
                'A draft was found for this file. Restore it?',
                QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.No
            )
            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                self._set_text(row[0])
                self.set_status('Draft restored')

    def _clear_draft_for_path(self, path):
        if not self._db:
            return
        key = self._draft_key_for_path(path)
        try:
            cur = self._db.cursor()
            cur.execute("DELETE FROM drafts WHERE key = ?", (key,))
            self._db.commit()
        except Exception:
            return

    def _jump_to_diagnostic(self, item):
        data = item.data(QtCore.Qt.ItemDataRole.UserRole) or {}
        line_no = int(data.get('line', 1))
        col_no = int(data.get('col', 1))
        
        editor = self._current_editor()
        if not editor:
            return
        cursor = editor.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
        cursor.movePosition(
            QtGui.QTextCursor.MoveOperation.Down,
            QtGui.QTextCursor.MoveMode.MoveAnchor,
            max(0, line_no - 1)
        )
        cursor.movePosition(
            QtGui.QTextCursor.MoveOperation.Right,
            QtGui.QTextCursor.MoveMode.MoveAnchor,
            max(0, col_no - 1)
        )
        editor.setTextCursor(cursor)
        editor.setFocus()

    def _show_about(self):
        """Show about dialog."""
        about_text = (
            "<h2>Codey Code Editor</h2>"
            "<p>A lightweight multi-language code editor with linting support.</p>"
            "<p><b>Supported Languages:</b> Python, JavaScript, C, C++</p>"
            "<p><b>Features:</b></p>"
            "<ul>"
            "<li>Syntax checking with CodeyLinter</li>"
            "<li>File browser</li>"
            "<li>Search functionality</li>"
            "<li>Command palette</li>"
            "</ul>"
            "<p><b>Keyboard Shortcuts:</b></p>"
            "<ul>"
            "<li>Ctrl+N - New File</li>"
            "<li>Ctrl+O - Open File</li>"
            "<li>Ctrl+S - Save File</li>"
            "<li>Ctrl+F - Find</li>"
            "<li>F5 - Run Lint</li>"
            "</ul>"
        )
        QtWidgets.QMessageBox.about(self, 'About Codey', about_text)

    def take_screenshot(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save Screenshot', '', 'PNG Image (*.png);;JPEG Image (*.jpg *.jpeg)'
        )
        if not path:
            return
        screen = QtWidgets.QApplication.primaryScreen()
        if not screen:
            self.set_status('Screenshot failed: no screen')
            return
        pixmap = screen.grabWindow(self.winId())
        if not pixmap.save(path):
            self.set_status('Screenshot failed')
            return
        self.set_status(f'Screenshot saved: {os.path.basename(path)}')

    def _apply_syntax_highlighting(self):
        tab = self._current_tab()
        if not tab:
            return
        if tab.highlighter:
            tab.highlighter.setDocument(None)
            tab.highlighter = None
        tab.highlighter = CodeyHighlighter(tab.editor.document(), tab.lang)

    def closeEvent(self, event):
        """Handle window close event."""
        if not self._shutdown_threads():
            self._pending_close = True
            self.set_status('Waiting for linter to stop...')
            event.ignore()
            return
        modified_tabs = []
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if tab and tab.is_modified:
                modified_tabs.append(i)

        if modified_tabs:
            reply = QtWidgets.QMessageBox.question(
                self, 'Unsaved Changes',
                'Do you want to save changes before closing?',
                QtWidgets.QMessageBox.StandardButton.Save |
                QtWidgets.QMessageBox.StandardButton.Discard |
                QtWidgets.QMessageBox.StandardButton.Cancel
            )
            
            if reply == QtWidgets.QMessageBox.StandardButton.Save:
                for index in modified_tabs:
                    self.tabs.setCurrentIndex(index)
                    self.on_save()
                    current = self._current_tab()
                    if current and current.is_modified:
                        event.ignore()
                        return
                    if current and current.path:
                        self._clear_draft_for_path(current.path)
                event.accept()
            elif reply == QtWidgets.QMessageBox.StandardButton.Discard:
                self._autosave_draft()
                event.accept()
            else:
                event.ignore()
        else:
            self._autosave_draft()
            event.accept()


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName('Codey')
    app.setOrganizationName('Codey')
    
    window = CodeyApp()
    window.show()
    sys.exit(app.exec())
