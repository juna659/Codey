# Codey.py
# A lightweight multi-language editor using PyQt6 with optional CodeyLinter.
# Enhanced version with icons, better styling, and bug fixes.

import os
import shutil
import sqlite3
import sys
import threading
import time
import fnmatch
import json

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


def _ensure_logo_png():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    logo_path = os.path.join(base_dir, 'codey_logo.png')
    if os.path.isfile(logo_path):
        return logo_path
    try:
        size = 512
        img = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
        img.fill(QtGui.QColor('#0f1115'))

        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        # Subtle background circle
        bg = QtGui.QRadialGradient(QtCore.QPointF(size * 0.5, size * 0.45), size * 0.6)
        bg.setColorAt(0.0, QtGui.QColor('#151a22'))
        bg.setColorAt(1.0, QtGui.QColor('#0f1115'))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(QtCore.QRectF(32, 32, size - 64, size - 64))

        # Snake "C" body
        margin = 84
        rect = QtCore.QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
        path = QtGui.QPainterPath()
        path.arcMoveTo(rect, 40)
        path.arcTo(rect, 40, 280)
        pen = QtGui.QPen(QtGui.QColor('#3ddc84'), 58)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # Snake head + eye
        head = path.pointAtPercent(0.02)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor('#3ddc84'))
        painter.drawEllipse(QtCore.QPointF(head.x(), head.y()), 30, 30)
        painter.setBrush(QtGui.QColor('#0b0d12'))
        painter.drawEllipse(QtCore.QPointF(head.x() + 8, head.y() - 6), 6, 6)

        # Inner "++"
        painter.setPen(QtGui.QColor('#ff9e64'))
        font = QtGui.QFont('Segoe UI', 120, QtGui.QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QtCore.QRectF(0, 0, size, size),
                         QtCore.Qt.AlignmentFlag.AlignCenter, '++')

        painter.end()
        img.save(logo_path, 'PNG')
        return logo_path
    except Exception:
        return None


def _path_matches_ignore(abs_path, workspace_root, ignore_patterns):
    if not abs_path or not workspace_root:
        return False
    try:
        rel = os.path.relpath(abs_path, workspace_root)
    except Exception:
        return False
    if rel == '.':
        return False
    rel = rel.replace('\\', '/')
    basename = os.path.basename(abs_path)
    for pattern in ignore_patterns or []:
        pat = (pattern or '').strip()
        if not pat:
            continue
        if pat.endswith('/'):
            prefix = pat.rstrip('/').replace('\\', '/')
            if rel == prefix or rel.startswith(prefix + '/'):
                return True
            continue
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(basename, pat):
            return True
    return False


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
        elif language == 'json':
            try:
                import json
                json.loads(text)
            except Exception as exc:
                line_no = getattr(exc, 'lineno', 1) or 1
                col_no = getattr(exc, 'colno', 1) or 1
                diagnostics.append({
                    'line': line_no,
                    'col': col_no,
                    'message': str(exc),
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


class FindInFilesWorker(QtCore.QThread):
    result = QtCore.pyqtSignal(list)
    error = QtCore.pyqtSignal(str)

    def __init__(self, workspace_root, query, case_sensitive, max_results, ignore_patterns, parent=None):
        super(FindInFilesWorker, self).__init__(parent)
        self._workspace_root = workspace_root
        self._query = query
        self._case_sensitive = case_sensitive
        self._max_results = max_results
        self._ignore_patterns = ignore_patterns or []

    def run(self):
        if not self._workspace_root or not self._query:
            self.result.emit([])
            return
        results = []
        needle = self._query if self._case_sensitive else self._query.lower()
        try:
            for root, dirs, files in os.walk(self._workspace_root):
                if self.isInterruptionRequested():
                    return
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith('.') and not _path_matches_ignore(
                        os.path.join(root, d), self._workspace_root, self._ignore_patterns
                    )
                ]
                for filename in files:
                    if filename.startswith('.'):
                        continue
                    full = os.path.join(root, filename)
                    if _path_matches_ignore(full, self._workspace_root, self._ignore_patterns):
                        continue
                    try:
                        with open(full, 'r', encoding='utf-8', errors='replace') as f:
                            for line_no, line in enumerate(f, start=1):
                                hay = line if self._case_sensitive else line.lower()
                                if needle in hay:
                                    snippet = line.strip()
                                    results.append({
                                        'path': full,
                                        'line': line_no,
                                        'text': snippet[:240],
                                    })
                                    if len(results) >= self._max_results:
                                        self.result.emit(results)
                                        return
                    except Exception:
                        continue
            self.result.emit(results)
        except Exception as exc:
            self.error.emit(str(exc))


class CodeyHighlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, document, language):
        super(CodeyHighlighter, self).__init__(document)
        self.language = language
        self.rules = []
        self._string_fmt = None
        self._comment_fmt = None
        self._triple_double = QtCore.QRegularExpression('\"\"\"')
        self._triple_single = QtCore.QRegularExpression("\'\'\'")
        self._block_comment_start = QtCore.QRegularExpression('/\\*')
        self._block_comment_end = QtCore.QRegularExpression('\\*/')
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
        func_fmt = self._fmt('#7dcfff')
        class_fmt = self._fmt('#2ac3de', bold=True)
        decorator_fmt = self._fmt('#f7768e')
        preproc_fmt = self._fmt('#e0af68', bold=True)
        const_fmt = self._fmt('#ff9e64', bold=True)
        operator_fmt = self._fmt('#89ddff')
        brace_fmt = self._fmt('#c0caf5')
        attr_fmt = self._fmt('#73daca')
        self._string_fmt = string_fmt
        self._comment_fmt = comment_fmt

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
            self.rules.append((r'==|!=|<=|>=|\+=|-=|\*=|/=|%=|//=|\*\*=|->|:=|[-+/*%=<>!&|^~]+', operator_fmt))
            self.rules.append((r'[\{\}\[\]\(\)]', brace_fmt))
            self.rules.append((r'#.*', comment_fmt))
            self.rules.append((r'\".*?\"', string_fmt))
            self.rules.append((r"\'.*?\'", string_fmt))
            self.rules.append((r'\"\"\".*?\"\"\"', string_fmt))
            self.rules.append((r"\'\'\'.*?\'\'\'", string_fmt))
            self.rules.append((r'\b0[xX][0-9a-fA-F]+\b|\b0[bB][01]+\b|\b[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?\b', number_fmt))
            self.rules.append((r'\bTrue\b|\bFalse\b|\bNone\b', const_fmt))
            self.rules.append((r'@\w+', decorator_fmt))
            self.rules.append((r'\bclass\s+([A-Za-z_][A-Za-z0-9_]*)', class_fmt))
            self.rules.append((r'\bdef\s+([A-Za-z_][A-Za-z0-9_]*)', func_fmt))
            self.rules.append((r'\b([A-Za-z_][A-Za-z0-9_]*)\s*(?=\()', func_fmt))
            self.rules.append((r'(?<=\.)[A-Za-z_][A-Za-z0-9_]*', attr_fmt))
        elif self.language == 'javascript':
            keywords = [
                'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger',
                'default', 'delete', 'do', 'else', 'export', 'extends', 'finally',
                'for', 'function', 'if', 'import', 'in', 'instanceof', 'let',
                'new', 'return', 'super', 'switch', 'this', 'throw', 'try',
                'typeof', 'var', 'void', 'while', 'with', 'yield', 'await', 'async',
            ]
            builtins = [
                'Array', 'Boolean', 'Date', 'Error', 'Function', 'JSON', 'Math',
                'Number', 'Object', 'Promise', 'RegExp', 'Set', 'String', 'Map',
                'WeakMap', 'WeakSet', 'Symbol', 'BigInt', 'console', 'window',
                'document', 'undefined', 'null', 'NaN', 'Infinity',
            ]
            for word in keywords:
                self.rules.append((r'\b%s\b' % word, keyword_fmt))
            for word in builtins:
                self.rules.append((r'\b%s\b' % word, type_fmt))
            self.rules.append((r'===|!==|==|!=|<=|>=|\+\+|--|\+=|-=|\*=|/=|%=|&&|\|\||=>|[-+/*%=<>!&|^~?:]+', operator_fmt))
            self.rules.append((r'[\{\}\[\]\(\)]', brace_fmt))
            self.rules.append((r'//.*', comment_fmt))
            self.rules.append((r'/\*.*\*/', comment_fmt))
            self.rules.append((r'\".*?\"', string_fmt))
            self.rules.append((r"\'.*?\'", string_fmt))
            self.rules.append((r'`[^`]*`', string_fmt))
            self.rules.append((r'\b0[xX][0-9a-fA-F]+\b|\b0[bB][01]+\b|\b[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?\b', number_fmt))
            self.rules.append((r'\b(true|false|null|undefined|NaN|Infinity)\b', const_fmt))
            self.rules.append((r'\bclass\s+([A-Za-z_][A-Za-z0-9_]*)', class_fmt))
            self.rules.append((r'\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)', func_fmt))
            self.rules.append((r'\b([A-Za-z_][A-Za-z0-9_]*)\s*(?=\()', func_fmt))
            self.rules.append((r'(?<=\.)[A-Za-z_][A-Za-z0-9_]*', attr_fmt))
        elif self.language == 'json':
            self.rules.append((r'//.*', comment_fmt))
            self.rules.append((r'/\*.*\*/', comment_fmt))
            self.rules.append((r'"(?:\\.|[^"\\])*"(?=\s*:)', class_fmt))
            self.rules.append((r'"(?:\\.|[^"\\])*"', string_fmt))
            self.rules.append((r'\b(true|false|null)\b', const_fmt))
            self.rules.append((r'-?\b[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?\b', number_fmt))
            self.rules.append((r'[\{\}\[\]\(\):,]', brace_fmt))
        elif self.language == 'log':
            # LOG files are treated as plain text for faster rendering.
            pass
        elif self.language == 'text':
            # Plain text mode intentionally keeps highlighting minimal.
            self.rules.append((r'$', self._fmt('#c0caf5')))
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
            self.rules.append((r'::|==|!=|<=|>=|\+\+|--|\+=|-=|\*=|/=|%=|&&|\|\||->|<<|>>|[-+/*%=<>!&|^~?:]+', operator_fmt))
            self.rules.append((r'[\{\}\[\]\(\)]', brace_fmt))
            self.rules.append((r'//.*', comment_fmt))
            self.rules.append((r'/\*.*\*/', comment_fmt))
            self.rules.append((r'\".*?\"', string_fmt))
            self.rules.append((r"\'.*?\'", string_fmt))
            self.rules.append((r'\b0[xX][0-9a-fA-F]+\b|\b0[bB][01]+\b|\b[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?\b', number_fmt))
            self.rules.append((r'^\s*#\s*(include|define|ifdef|ifndef|endif|pragma).*$', preproc_fmt))
            self.rules.append((r'\b(true|false|nullptr)\b', const_fmt))
            self.rules.append((r'\b(this|nullptr|NULL)\b', const_fmt))
            self.rules.append((r'\b(std|string|vector|map|unordered_map|set|shared_ptr|unique_ptr)\b', type_fmt))
            self.rules.append((r'\bclass\s+([A-Za-z_][A-Za-z0-9_]*)', class_fmt))
            self.rules.append((r'\b([A-Za-z_][A-Za-z0-9_]*)\s*(?=\()', func_fmt))
            self.rules.append((r'(?<=\.)[A-Za-z_][A-Za-z0-9_]*', attr_fmt))
            self.rules.append((r'(?<=->)[A-Za-z_][A-Za-z0-9_]*', attr_fmt))

        self.rules = [(QtCore.QRegularExpression(pat), fmt) for pat, fmt in self.rules]

    def highlightBlock(self, text):
        self.setCurrentBlockState(0)
        for pattern, fmt in self.rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

        if self.language == 'python':
            if self._string_fmt:
                if self._apply_multiline(text, self._triple_double, self._triple_double, 1, self._string_fmt):
                    return
                self._apply_multiline(text, self._triple_single, self._triple_single, 2, self._string_fmt)
            return

        if self.language in ('javascript', 'c', 'cpp'):
            if self._comment_fmt:
                self._apply_multiline(text, self._block_comment_start, self._block_comment_end, 3, self._comment_fmt)

    def _apply_multiline(self, text, start_pat, end_pat, state_id, fmt):
        if self.previousBlockState() == state_id:
            start = 0
        else:
            match = start_pat.match(text)
            start = match.capturedStart() if match.hasMatch() else -1

        while start >= 0:
            end_match = end_pat.match(text, start + 1)
            if end_match.hasMatch():
                end = end_match.capturedEnd()
                self.setFormat(start, end - start, fmt)
                next_match = start_pat.match(text, end)
                start = next_match.capturedStart() if next_match.hasMatch() else -1
            else:
                self.setFormat(start, len(text) - start, fmt)
                self.setCurrentBlockState(state_id)
                return True
        return False


class IgnoreFilterProxyModel(QtCore.QSortFilterProxyModel):
    def __init__(self, parent=None):
        super(IgnoreFilterProxyModel, self).__init__(parent)
        self._workspace_root = None
        self._ignore_patterns = []
        self.setRecursiveFilteringEnabled(True)

    def set_ignore_data(self, workspace_root, ignore_patterns):
        self._workspace_root = os.path.abspath(workspace_root) if workspace_root else None
        self._ignore_patterns = list(ignore_patterns or [])
        self.invalidateFilter()

    def _is_ignored(self, abs_path):
        return _path_matches_ignore(abs_path, self._workspace_root, self._ignore_patterns)

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if model is None:
            return True
        idx = model.index(source_row, 0, source_parent)
        if not idx.isValid():
            return True
        path = model.filePath(idx) if hasattr(model, 'filePath') else None
        return not self._is_ignored(path)


class ImageViewerDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, path=None):
        super(ImageViewerDialog, self).__init__(parent)
        self.setWindowTitle('Image Viewer')
        self.setModal(False)
        self.resize(900, 600)
        self._path = path
        self._scale = 1.0
        self._pixmap = None
        self._build_ui()
        self._apply_styles()
        if path:
            self.load_image(path)

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        toolbar = QtWidgets.QHBoxLayout()
        self.path_label = QtWidgets.QLabel('No image loaded')
        self.path_label.setObjectName('imagePath')
        self.btn_open = QtWidgets.QPushButton('Open')
        self.btn_fit = QtWidgets.QPushButton('Fit')
        self.btn_actual = QtWidgets.QPushButton('100%')
        self.btn_zoom_in = QtWidgets.QPushButton('Zoom +')
        self.btn_zoom_out = QtWidgets.QPushButton('Zoom -')

        self.btn_open.clicked.connect(self._pick_image)
        self.btn_fit.clicked.connect(self._fit_to_view)
        self.btn_actual.clicked.connect(self._actual_size)
        self.btn_zoom_in.clicked.connect(lambda: self._zoom(1.25))
        self.btn_zoom_out.clicked.connect(lambda: self._zoom(0.8))

        toolbar.addWidget(self.btn_open)
        toolbar.addWidget(self.btn_fit)
        toolbar.addWidget(self.btn_actual)
        toolbar.addWidget(self.btn_zoom_in)
        toolbar.addWidget(self.btn_zoom_out)
        toolbar.addStretch(1)
        toolbar.addWidget(self.path_label)

        self.image_label = QtWidgets.QLabel()
        self.image_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.image_label.setObjectName('imageCanvas')

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.image_label)

        root.addLayout(toolbar)
        root.addWidget(self.scroll, 1)

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background: #0f1115;
                color: #e6e6e6;
            }
            #imagePath {
                color: #9aa4b2;
            }
            QPushButton {
                background: #1f2633;
                border: 1px solid #2a3345;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QPushButton:hover {
                background: #293145;
            }
            #imageCanvas {
                background: #0b0d12;
                border: 1px solid #202634;
                border-radius: 6px;
            }
        """)

    def _pick_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Open Image', '', 'Images (*.png *.jpg *.jpeg *.bmp *.gif)'
        )
        if not path:
            return
        self.load_image(path)

    def load_image(self, path):
        pixmap = QtGui.QPixmap(path)
        if pixmap.isNull():
            QtWidgets.QMessageBox.warning(self, 'Image Viewer', 'Failed to load image.')
            return
        self._path = path
        self._pixmap = pixmap
        self._scale = 1.0
        self.path_label.setText(os.path.basename(path))
        self._render()

    def _render(self):
        if not self._pixmap:
            return
        scaled = self._pixmap.scaled(
            self._pixmap.size() * self._scale,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation
        )
        self.image_label.setPixmap(scaled)

    def _zoom(self, factor):
        if not self._pixmap:
            return
        self._scale = max(0.1, min(6.0, self._scale * factor))
        self._render()

    def _fit_to_view(self):
        if not self._pixmap:
            return
        view = self.scroll.viewport().size()
        if view.width() <= 0 or view.height() <= 0:
            return
        scale_w = view.width() / self._pixmap.width()
        scale_h = view.height() / self._pixmap.height()
        self._scale = max(0.05, min(6.0, min(scale_w, scale_h)))
        self._render()

    def _actual_size(self):
        if not self._pixmap:
            return
        self._scale = 1.0
        self._render()


class CodeyApp(QtWidgets.QMainWindow):
    LANG_PY = 'python'
    LANG_JS = 'javascript'
    LANG_C = 'c'
    LANG_CPP = 'cpp'
    LANG_JSON = 'json'
    LANG_LOG = 'log'
    LANG_TEXT = 'text'

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
        self._workspace_path = None
        self._ignore_patterns = []
        self._find_worker = None
        self._file_watch_timer = QtCore.QTimer(self)
        self._file_watch_timer.setInterval(3000)
        self._file_watch_timer.timeout.connect(self._check_open_files_changed)
        self._file_mtimes = {}
        self._settings = {}
        self._app_dir = None
        self._db = None
        self._db_path = None
        self._settings_path = None

        self._autosave_timer = QtCore.QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave_draft)

        self._build_editor()
        self._build_status()
        self._build_menu()
        self._build_toolbar()
        self._build_sidebar()
        self._build_bottom_panel()
        self._build_find_panel()
        self._build_terminal_panel()
        self._build_top_bar()
        self._apply_styles()

        self.linter = self._init_linter()

        self._init_storage()
        self._load_settings()
        self._apply_settings()
        self._init_freeze_handler()
        self._file_watch_timer.start()

        app = QtWidgets.QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._shutdown_threads)
        
        self._update_window_title()
        self._apply_syntax_highlighting()
        if not self._restore_session():
            self._prompt_for_workspace(initial=True)

    def _set_window_icon(self):
        """Set window icon using Codey logo when available."""
        logo_path = _ensure_logo_png()
        if logo_path and os.path.isfile(logo_path):
            self.setWindowIcon(QtGui.QIcon(logo_path))
            return
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
        self.file_menu = file_menu
        
        open_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_DialogOpenButton), '&Open', self)
        open_action.setShortcut('Ctrl+O')
        open_action.triggered.connect(self.on_open)

        open_folder_action = QtGui.QAction(self._create_icon(
            QtWidgets.QStyle.StandardPixmap.SP_DirOpenIcon), 'Open &Folder...', self)
        open_folder_action.setShortcut('Ctrl+Shift+O')
        open_folder_action.triggered.connect(self._prompt_for_workspace)
        
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
        file_menu.addAction(open_folder_action)
        file_menu.addSeparator()
        self.recent_files_menu = file_menu.addMenu('Recent Files')
        self.recent_files_menu.aboutToShow.connect(self._populate_recent_files_menu)
        self.recent_workspaces_menu = file_menu.addMenu('Recent Workspaces')
        self.recent_workspaces_menu.aboutToShow.connect(self._populate_recent_workspaces_menu)
        file_menu.addSeparator()
        file_menu.addAction(save_action)
        file_menu.addAction(save_as_action)
        file_menu.addSeparator()
        open_settings_action = QtGui.QAction('Open Settings File', self)
        open_settings_action.triggered.connect(self._open_settings_file)
        file_menu.addAction(open_settings_action)
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
        find_in_files_action = QtGui.QAction('Find in Files', self)
        find_in_files_action.setShortcut('Ctrl+Shift+F')
        find_in_files_action.triggered.connect(self._focus_find_in_files)
        edit_menu.addAction(find_in_files_action)

        # Language Menu with icons
        lang_menu = menubar.addMenu('🔧 Language')
        
        py_action = QtGui.QAction('🐍 Python', self)
        js_action = QtGui.QAction('🟨 JavaScript', self)
        c_action = QtGui.QAction('C', self)
        cpp_action = QtGui.QAction('C++', self)
        json_action = QtGui.QAction('JSON', self)
        log_action = QtGui.QAction('LOG', self)
        text_action = QtGui.QAction('Plain Text', self)
        
        py_action.triggered.connect(lambda: self.set_language(self.LANG_PY))
        js_action.triggered.connect(lambda: self.set_language(self.LANG_JS))
        c_action.triggered.connect(lambda: self.set_language(self.LANG_C))
        cpp_action.triggered.connect(lambda: self.set_language(self.LANG_CPP))
        json_action.triggered.connect(lambda: self.set_language(self.LANG_JSON))
        log_action.triggered.connect(lambda: self.set_language(self.LANG_LOG))
        text_action.triggered.connect(lambda: self.set_language(self.LANG_TEXT))
        
        lang_menu.addAction(py_action)
        lang_menu.addAction(js_action)
        lang_menu.addAction(c_action)
        lang_menu.addAction(cpp_action)
        lang_menu.addAction(json_action)
        lang_menu.addAction(log_action)
        lang_menu.addAction(text_action)

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
        image_viewer_action = QtGui.QAction('Image Viewer', self)
        image_viewer_action.setShortcut('Ctrl+Shift+I')
        image_viewer_action.triggered.connect(self.open_image_viewer)
        tools_menu.addAction(image_viewer_action)
        
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
            lang_map_reverse = {
                self.LANG_PY: 'Python',
                self.LANG_JS: 'JavaScript',
                self.LANG_C: 'C',
                self.LANG_CPP: 'C++',
                self.LANG_JSON: 'JSON',
                self.LANG_LOG: 'LOG',
                self.LANG_TEXT: 'Plain Text',
            }
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
        self.lang_combo.addItems(['Python', 'JavaScript', 'C', 'C++', 'JSON', 'LOG', 'Plain Text'])
        self.lang_combo.currentTextChanged.connect(self._on_lang_combo_changed)
        self.lang_combo.setMinimumWidth(100)
        toolbar.addWidget(self.lang_combo)

    def _on_lang_combo_changed(self, text):
        """Handle language combo box changes."""
        lang_map = {
            'Python': self.LANG_PY,
            'JavaScript': self.LANG_JS,
            'C': self.LANG_C,
            'C++': self.LANG_CPP,
            'JSON': self.LANG_JSON,
            'LOG': self.LANG_LOG,
            'Plain Text': self.LANG_TEXT,
        }
        if text in lang_map:
            self.set_language(lang_map[text])

    def _build_sidebar(self):
        base = self._workspace_path or QtCore.QDir.currentPath()
        self._reload_ignore_patterns(base)
        if hasattr(QtWidgets, 'QFileSystemModel'):
            self.fs_model = QtWidgets.QFileSystemModel()
            self.fs_model.setRootPath(base)
            self.fs_proxy = IgnoreFilterProxyModel(self)
            self.fs_proxy.setSourceModel(self.fs_model)
            self.fs_proxy.set_ignore_data(base, self._ignore_patterns)

            self.file_tree = QtWidgets.QTreeView()
            self.file_tree.setModel(self.fs_proxy)
            source_root = self.fs_model.index(base)
            self.file_tree.setRootIndex(self.fs_proxy.mapFromSource(source_root))
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
            self.fs_proxy = None
            self.file_tree = QtWidgets.QTreeWidget()
            self.file_tree.setHeaderHidden(True)
            self._populate_tree_widget()
            self.file_tree.itemDoubleClicked.connect(self._open_from_tree_widget)
            self.file_tree.itemExpanded.connect(self._on_tree_item_expanded)

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
        base = self._workspace_path or QtCore.QDir.currentPath()
        self._reload_ignore_patterns(base)
        if self.fs_model:
            self.fs_model.setRootPath(base)
            if self.fs_proxy:
                self.fs_proxy.set_ignore_data(base, self._ignore_patterns)
                self.file_tree.setRootIndex(self.fs_proxy.mapFromSource(self.fs_model.index(base)))
            else:
                self.file_tree.setRootIndex(self.fs_model.index(base))
        else:
            self.file_tree.clear()
            self._populate_tree_widget()

    def _build_bottom_panel(self):
        self.diagnostics_list = QtWidgets.QListWidget()
        self.diagnostics_list.itemActivated.connect(self._jump_to_diagnostic)
        self.diagnostics_list.itemDoubleClicked.connect(self._jump_to_diagnostic)

        dock = QtWidgets.QDockWidget('🔍 Lint Output', self)
        dock.setWidget(self.diagnostics_list)
        dock.setObjectName('LintDock')
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, dock)

    def _build_find_panel(self):
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.find_files_results = QtWidgets.QListWidget()
        self.find_files_results.itemActivated.connect(self._open_find_result)
        self.find_files_results.itemDoubleClicked.connect(self._open_find_result)

        hint = QtWidgets.QLabel('Use the top search box, then press Ctrl+Shift+F')
        hint.setStyleSheet('color: #9aa4b2; padding: 2px;')
        layout.addWidget(hint)
        layout.addWidget(self.find_files_results, 1)

        dock = QtWidgets.QDockWidget('🔎 Find in Files', self)
        dock.setWidget(container)
        dock.setObjectName('FindFilesDock')
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)

    def _focus_find_in_files(self):
        if hasattr(self, 'search_input'):
            self.search_input.setFocus()

    def _start_find_in_files(self):
        query = self.search_input.text().strip() if hasattr(self, 'search_input') else ''
        if not query:
            self.set_status('Find in files: empty query')
            return
        workspace = self._workspace_path or os.getcwd()
        if self._find_worker and self._find_worker.isRunning():
            self._find_worker.requestInterruption()
            self._find_worker.wait(1000)
        max_results = int(self._settings.get('find_max_results', 500))
        self.find_files_results.clear()
        self._find_worker = FindInFilesWorker(
            workspace_root=workspace,
            query=query,
            case_sensitive=False,
            max_results=max_results,
            ignore_patterns=self._ignore_patterns,
            parent=self,
        )
        self._find_worker.result.connect(self._on_find_in_files_result)
        self._find_worker.error.connect(self._on_find_in_files_error)
        self._find_worker.finished.connect(self._on_find_in_files_finished)
        self._find_worker.start()
        self.set_status(f'Find in files: searching for "{query}"...')

    def _on_find_in_files_result(self, results):
        self.find_files_results.clear()
        for item in results:
            rel = os.path.relpath(item['path'], self._workspace_path) if self._workspace_path else item['path']
            text = f"{rel}:{item['line']}  {item['text']}"
            lw = QtWidgets.QListWidgetItem(text)
            lw.setData(QtCore.Qt.ItemDataRole.UserRole, item)
            self.find_files_results.addItem(lw)
        self.set_status(f'Find in files: {len(results)} result(s)')

    def _on_find_in_files_error(self, message):
        self.set_status(f'Find in files failed: {message}')

    def _on_find_in_files_finished(self):
        self._find_worker = None

    def _open_find_result(self, item):
        data = item.data(QtCore.Qt.ItemDataRole.UserRole) or {}
        path = data.get('path')
        line_no = int(data.get('line', 1))
        if not path:
            return
        self._open_path(path, new_tab=True)
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
        editor.setFocus()
    
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
        self._app_dir = app_dir
        self._settings_path = os.path.join(app_dir, 'codey.settings.json')
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
            cur.execute(
                "CREATE TABLE IF NOT EXISTS session_state ("
                " key TEXT PRIMARY KEY,"
                " value TEXT"
                ")"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS session_tabs ("
                " tab_index INTEGER,"
                " path TEXT,"
                " line INTEGER,"
                " col INTEGER"
                ")"
            )
            self._db.commit()
        except Exception:
            self._db = None

    def _default_settings(self):
        return {
            'font_size': 12,
            'autosave_delay_ms': 800,
            'lint_delay_ms': 600,
            'restore_last_session': True,
            'max_recent_items': 10,
            'find_max_results': 500,
            'recent_files': [],
            'recent_workspaces': [],
        }

    def _load_settings(self):
        defaults = self._default_settings()
        self._settings = dict(defaults)
        if not self._settings_path:
            return
        if not os.path.isfile(self._settings_path):
            self._save_settings()
            return
        try:
            with open(self._settings_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._settings.update(data)
        except Exception:
            self._settings = dict(defaults)
            self._save_settings()

    def _save_settings(self):
        if not self._settings_path:
            return
        try:
            with open(self._settings_path, 'w', encoding='utf-8') as f:
                json.dump(self._settings, f, indent=2)
        except Exception:
            return

    def _apply_settings(self):
        try:
            autosave_delay = int(self._settings.get('autosave_delay_ms', 800))
            lint_delay = int(self._settings.get('lint_delay_ms', 600))
            font_size = int(self._settings.get('font_size', 12))
        except Exception:
            autosave_delay, lint_delay, font_size = 800, 600, 12
        self._autosave_timer.setInterval(max(200, autosave_delay))
        self._lint_timer.setInterval(max(200, lint_delay))
        self._set_editor_font_size(max(8, min(28, font_size)))

    def _set_editor_font_size(self, size):
        font = QtGui.QFont('JetBrains Mono', size)
        QtWidgets.QApplication.setFont(font)
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if tab:
                tab.editor.setFont(font)

    def _open_settings_file(self):
        if not self._settings_path:
            return
        try:
            if not os.path.isfile(self._settings_path):
                self._save_settings()
            self._open_path(self._settings_path, new_tab=True)
        except Exception as exc:
            self.set_status(f'Open settings failed: {exc}')

    def _push_recent_value(self, key, value):
        if not value:
            return
        max_items = int(self._settings.get('max_recent_items', 10))
        items = list(self._settings.get(key, []))
        items = [x for x in items if x != value]
        items.insert(0, value)
        self._settings[key] = items[:max(1, max_items)]
        self._save_settings()

    def _add_recent_file(self, path):
        self._push_recent_value('recent_files', path)

    def _add_recent_workspace(self, path):
        self._push_recent_value('recent_workspaces', path)

    def _populate_recent_files_menu(self):
        self.recent_files_menu.clear()
        items = self._settings.get('recent_files', [])
        if not items:
            action = self.recent_files_menu.addAction('(empty)')
            action.setEnabled(False)
            return
        for path in items:
            action = self.recent_files_menu.addAction(path)
            action.triggered.connect(lambda _checked=False, p=path: self._open_recent_file(p))
        self.recent_files_menu.addSeparator()
        clear_action = self.recent_files_menu.addAction('Clear Recent Files')
        clear_action.triggered.connect(self._clear_recent_files)

    def _populate_recent_workspaces_menu(self):
        self.recent_workspaces_menu.clear()
        items = self._settings.get('recent_workspaces', [])
        if not items:
            action = self.recent_workspaces_menu.addAction('(empty)')
            action.setEnabled(False)
            return
        for path in items:
            action = self.recent_workspaces_menu.addAction(path)
            action.triggered.connect(lambda _checked=False, p=path: self._open_recent_workspace(p))
        self.recent_workspaces_menu.addSeparator()
        clear_action = self.recent_workspaces_menu.addAction('Clear Recent Workspaces')
        clear_action.triggered.connect(self._clear_recent_workspaces)

    def _open_recent_file(self, path):
        if path and os.path.isfile(path):
            self._open_path(path, new_tab=True)
        else:
            self.set_status('Recent file missing')

    def _open_recent_workspace(self, path):
        if path and os.path.isdir(path):
            self._set_workspace(path)
        else:
            self.set_status('Recent workspace missing')

    def _clear_recent_files(self):
        self._settings['recent_files'] = []
        self._save_settings()

    def _clear_recent_workspaces(self):
        self._settings['recent_workspaces'] = []
        self._save_settings()

    def _restore_session(self):
        if not self._db or not bool(self._settings.get('restore_last_session', True)):
            return False
        try:
            cur = self._db.cursor()
            cur.execute("SELECT value FROM session_state WHERE key = 'workspace'")
            row = cur.fetchone()
            workspace = row[0] if row else None
            cur.execute("SELECT tab_index, path, line, col FROM session_tabs ORDER BY tab_index ASC")
            tabs = cur.fetchall()
        except Exception:
            return False
        restored = False
        if workspace and os.path.isdir(workspace):
            self._set_workspace(workspace)
            restored = True
        valid_tabs = [t for t in tabs if t[1] and os.path.isfile(t[1])]
        if valid_tabs:
            while self.tabs.count() > 0:
                self.tabs.removeTab(0)
            for _idx, path, line, col in valid_tabs:
                self._open_path(path, new_tab=True)
                tab = self._current_tab()
                if tab:
                    cursor = tab.editor.textCursor()
                    cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
                    cursor.movePosition(
                        QtGui.QTextCursor.MoveOperation.Down,
                        QtGui.QTextCursor.MoveMode.MoveAnchor,
                        max(0, int(line or 1) - 1)
                    )
                    cursor.movePosition(
                        QtGui.QTextCursor.MoveOperation.Right,
                        QtGui.QTextCursor.MoveMode.MoveAnchor,
                        max(0, int(col or 1) - 1)
                    )
                    tab.editor.setTextCursor(cursor)
            restored = True
        return restored

    def _save_session(self):
        if not self._db:
            return
        try:
            cur = self._db.cursor()
            cur.execute("DELETE FROM session_tabs")
            workspace = self._workspace_path or ''
            cur.execute(
                "INSERT OR REPLACE INTO session_state(key, value) VALUES ('workspace', ?)",
                (workspace,)
            )
            for i in range(self.tabs.count()):
                tab = self.tabs.widget(i)
                if not tab or not tab.path:
                    continue
                cursor = tab.editor.textCursor()
                line = cursor.blockNumber() + 1
                col = cursor.columnNumber() + 1
                cur.execute(
                    "INSERT INTO session_tabs(tab_index, path, line, col) VALUES (?, ?, ?, ?)",
                    (i, tab.path, line, col)
                )
            self._db.commit()
        except Exception:
            return

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
        if self._is_closing:
            return
        try:
            if hasattr(self, 'statusbar') and self.statusbar:
                self.statusbar.showMessage(text, 5000)  # Show for 5 seconds
        except RuntimeError:
            # Async callbacks can fire while the main window is being destroyed.
            return

    def _on_buffer_changed(self, *_):
        tab = self._current_tab()
        if not tab:
            return
        if not tab.is_modified:
            tab.is_modified = True
            self._update_window_title()
        
        # Debounce linting during active edits
        if hasattr(self, '_lint_timer'):
            self._lint_timer.start()
        if hasattr(self, '_autosave_timer'):
            self._autosave_timer.start()

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
        lang_map_reverse = {
            self.LANG_PY: 'Python',
            self.LANG_JS: 'JavaScript',
            self.LANG_C: 'C',
            self.LANG_CPP: 'C++',
            self.LANG_JSON: 'JSON',
            self.LANG_LOG: 'LOG',
            self.LANG_TEXT: 'Plain Text',
        }
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
        if ext in ('.json',):
            return self.LANG_JSON
        if ext in ('.log',):
            return self.LANG_LOG
        if ext in ('.txt',):
            return self.LANG_TEXT
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
            'All Supported Files (*.py *.js *.mjs *.cjs *.jsx *.c *.cpp *.h *.hpp *.cc *.cxx *.json *.log *.txt);;'
            'JSON Files (*.json);;Log Files (*.log);;Text Files (*.txt);;'
            'JavaScript Files (*.js *.mjs *.cjs *.jsx);;'
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
            self._record_file_mtime(tab.path)
            self._add_recent_file(tab.path)
            self.set_status(f'✓ Saved: {os.path.basename(tab.path)}')
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, 'Save Error', str(exc))
            self.set_status(f'Save failed: {exc}')

    def on_save_as(self, *_):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save File', '', 
            'Python Files (*.py);;JavaScript Files (*.js);;JSON Files (*.json);;'
            'Log Files (*.log);;Text Files (*.txt);;C Files (*.c);;C++ Files (*.cpp);;'
            'Header Files (*.h *.hpp);;All Files (*)')
        if path:
            tab = self._current_tab()
            if tab:
                tab.path = path
            self.set_language(self._infer_language_from_path(path))
            self.on_save()

    def _open_from_tree(self, index):
        source_index = self.fs_proxy.mapToSource(index) if self.fs_proxy else index
        path = self.fs_model.filePath(source_index)
        if os.path.isdir(path):
            return
        self._open_path(path, new_tab=True)

    def _populate_tree_widget(self):
        root_path = self._workspace_path or os.getcwd()
        root_item = QtWidgets.QTreeWidgetItem([os.path.basename(root_path) or root_path])
        root_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, root_path)
        root_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, False)
        self.file_tree.addTopLevelItem(root_item)
        if self._dir_has_visible_children(root_path):
            root_item.addChild(QtWidgets.QTreeWidgetItem(['']))

    def _set_workspace(self, path):
        if not path or not os.path.isdir(path):
            return
        self._workspace_path = path
        self._add_recent_workspace(path)
        self._reload_ignore_patterns(path)
        if self.fs_model:
            self.fs_model.setRootPath(path)
            if self.fs_proxy:
                self.fs_proxy.set_ignore_data(path, self._ignore_patterns)
                self.file_tree.setRootIndex(self.fs_proxy.mapFromSource(self.fs_model.index(path)))
            else:
                self.file_tree.setRootIndex(self.fs_model.index(path))
        else:
            self.file_tree.clear()
            self._populate_tree_widget()
        self.set_status(f'Workspace: {path}')

    def _reload_ignore_patterns(self, workspace_root):
        self._ignore_patterns = []
        if not workspace_root:
            return
        ignore_file = os.path.join(workspace_root, '.codeyignore')
        if not os.path.isfile(ignore_file):
            return
        try:
            with open(ignore_file, 'r', encoding='utf-8', errors='replace') as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith('#'):
                        continue
                    self._ignore_patterns.append(line.replace('\\', '/'))
        except Exception:
            self._ignore_patterns = []

    def _is_ignored_path(self, abs_path):
        return _path_matches_ignore(abs_path, self._workspace_path, self._ignore_patterns)

    def _prompt_for_workspace(self, initial=False):
        base = self._workspace_path or os.getcwd()
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, 'Select Folder', base
        )
        if not path:
            if initial:
                self.set_status('No folder selected')
            return
        self._set_workspace(path)

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
            if self._is_ignored_path(full):
                continue
            if os.path.isdir(full):
                dirs.append(name)
            else:
                files.append(name)

        for dirname in sorted(dirs):
            full = os.path.join(path, dirname)
            child = QtWidgets.QTreeWidgetItem([f'📁 {dirname}'])
            child.setData(0, QtCore.Qt.ItemDataRole.UserRole, full)
            child.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, False)
            parent_item.addChild(child)
            if self._dir_has_visible_children(full):
                child.addChild(QtWidgets.QTreeWidgetItem(['']))

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

    def _dir_has_visible_children(self, path):
        try:
            for name in os.listdir(path):
                if name.startswith('.'):
                    continue
                full = os.path.join(path, name)
                if self._is_ignored_path(full):
                    continue
                if os.path.exists(full):
                    return True
        except Exception:
            return False
        return False

    def _on_tree_item_expanded(self, item):
        path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not path or not os.path.isdir(path):
            return
        loaded = bool(item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1))
        if loaded:
            return
        item.takeChildren()
        self._add_tree_children(item, path)
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, True)

    def _open_from_tree_widget(self, item, _col):
        path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if not path or os.path.isdir(path):
            return
        self._open_path(path, new_tab=True)

    def _is_probably_binary_file(self, path):
        try:
            with open(path, 'rb') as f:
                chunk = f.read(4096)
        except Exception:
            return False
        if not chunk:
            return False
        if b'\x00' in chunk:
            return True
        text_bytes = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x7F)))
        non_text = chunk.translate(None, text_bytes)
        return (len(non_text) / float(len(chunk))) > 0.30

    def _open_path(self, path, new_tab=False):
        try:
            if self._is_probably_binary_file(path):
                reply = QtWidgets.QMessageBox.warning(
                    self,
                    'Binary File Warning',
                    'Warning: This file appears to be binary and may freeze Codey if opened as text.\n\n'
                    'Do you want to continue anyway?',
                    QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.No
                )
                if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                    self.set_status('Open canceled: binary file warning')
                    return
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
            self._add_recent_file(path)
            self._record_file_mtime(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, 'Open Error', str(exc))
            self.set_status(f'Open failed: {exc}')

    def _record_file_mtime(self, path):
        if not path:
            return
        try:
            self._file_mtimes[path] = os.path.getmtime(path)
        except Exception:
            return

    def _check_open_files_changed(self):
        if self._is_closing:
            return
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if not tab or not tab.path:
                continue
            try:
                current_mtime = os.path.getmtime(tab.path)
            except Exception:
                continue
            old_mtime = self._file_mtimes.get(tab.path)
            if old_mtime is None:
                self._file_mtimes[tab.path] = current_mtime
                continue
            if current_mtime <= old_mtime:
                continue
            self._file_mtimes[tab.path] = current_mtime
            if tab.is_modified:
                continue
            current_tab = self.tabs.currentIndex()
            self.tabs.setCurrentIndex(i)
            reply = QtWidgets.QMessageBox.question(
                self, 'File Changed',
                f'{os.path.basename(tab.path)} changed on disk. Reload it?',
                QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.No
            )
            if reply == QtWidgets.QMessageBox.StandardButton.Yes:
                self._open_path(tab.path, new_tab=False)
            self.tabs.setCurrentIndex(current_tab)

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
        if self._file_watch_timer:
            self._file_watch_timer.stop()
        if self._find_worker and self._find_worker.isRunning():
            self._find_worker.requestInterruption()
            self._find_worker.wait(2000)
            if self._find_worker.isRunning():
                self._find_worker.terminate()
                self._find_worker.wait(500)
            self._find_worker = None
        if self._lint_worker and self._lint_worker.isRunning():
            self._lint_worker.requestInterruption()
            self._lint_worker.wait(5000)
            if self._lint_worker.isRunning():
                self._lint_worker.terminate()
                self._lint_worker.wait(1000)
            if not self._lint_worker.isRunning():
                self._lint_worker = None
        if self._run_process:
            self._run_process.terminate()
            self._run_process.waitForFinished(1000)
            self._run_process = None
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
        elif lang in (self.LANG_JSON, self.LANG_LOG, self.LANG_TEXT):
            self.set_status('Run: not supported for this file type')
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
        if self._is_closing:
            return
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
            "<p><b>Supported Languages:</b> Python, JavaScript, C, C++, JSON, LOG, Plain Text</p>"
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

    def open_image_viewer(self, path=None):
        dialog = ImageViewerDialog(self, path=path)
        dialog.show()

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
                self._save_session()
                event.accept()
            elif reply == QtWidgets.QMessageBox.StandardButton.Discard:
                self._autosave_draft()
                self._save_session()
                event.accept()
            else:
                event.ignore()
        else:
            self._autosave_draft()
            self._save_session()
            event.accept()


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName('Codey')
    app.setOrganizationName('Codey')
    
    window = CodeyApp()
    window.show()
    sys.exit(app.exec())
