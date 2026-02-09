# CodeyLinter.py
# Enhanced linter facade for Codey with improved error handling and reporting.
# Supports Python (pylint), C (gcc), and C++ (g++) syntax checking.

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import fnmatch
import hashlib
from typing import List, Dict, Optional, Tuple


class LinterError(Exception):
    """Custom exception for linter-related errors."""
    pass


_CACHE: Dict[Tuple[str, str], List[Dict]] = {}
_CACHE_MAX = 128


def _run_process(argv: List[str], cwd: Optional[str] = None) -> Tuple[int, str, str, Optional[str]]:
    """
    Run a subprocess with error handling.
    
    Args:
        argv: Command and arguments as list
        cwd: Optional working directory
        
    Returns:
        Tuple of (return_code, stdout, stderr, error_message)
    """
    try:
        result = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            text=True,
            check=False,
            timeout=30  # Add timeout to prevent hanging
        )
        return result.returncode, result.stdout, result.stderr, None
    except subprocess.TimeoutExpired:
        return 1, '', '', f'Command timed out: {" ".join(argv)}'
    except FileNotFoundError:
        return 127, '', '', f'Tool not found: {argv[0]}'
    except Exception as exc:
        return 1, '', '', f'Process error: {str(exc)}'


def _load_ignore_patterns(base_dir: Optional[str]) -> List[Tuple[str, str]]:
    if not base_dir:
        return []
    patterns: List[Tuple[str, str]] = []
    current = os.path.abspath(base_dir)
    prev = None
    while current and current != prev:
        path = os.path.join(current, '.codeyignore')
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        patterns.append((current, line))
            except Exception:
                pass
        prev = current
        current = os.path.dirname(current)
    return patterns


def _is_ignored(file_path: Optional[str]) -> bool:
    if not file_path:
        return False
    base_dir = os.path.dirname(os.path.abspath(file_path))
    patterns = _load_ignore_patterns(base_dir)
    if not patterns:
        return False
    abs_path = os.path.abspath(file_path)
    for base_dir, pattern in patterns:
        rel = os.path.relpath(abs_path, base_dir)
        pat = pattern
        if pat.endswith('/'):
            prefix = pat.rstrip('/')
            if rel.startswith(prefix + os.sep) or rel == prefix:
                return True
            continue
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(os.path.basename(rel), pat):
            return True
    return False


def _cache_get(key: Tuple[str, str]) -> Optional[List[Dict]]:
    return _CACHE.get(key)


def _cache_set(key: Tuple[str, str], value: List[Dict]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        # simple eviction: remove one arbitrary item
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = value


def _run_pylint(tmp_path: str) -> Tuple[int, str, str, Optional[str]]:
    """
    Run pylint on a temporary file.
    
    Args:
        tmp_path: Path to temporary Python file
        
    Returns:
        Tuple of (return_code, stdout, stderr, error_message)
    """
    # Try direct pylint command first
    argv = [
        'pylint',
        '--output-format=json',
        '--score=n',
        '--reports=n',
        '--max-line-length=500',
        '--disable=missing-module-docstring,missing-function-docstring,missing-class-docstring',
        tmp_path,
    ]
    code, out, err, err_msg = _run_process(argv)
    
    if err_msg and 'not found' in err_msg.lower():
        # Fallback to module invocation if pylint isn't on PATH
        argv = [
            sys.executable,
            '-m',
            'pylint',
            '--output-format=json',
            '--score=n',
            '--reports=n',
            '--max-line-length=500',
            '--disable=missing-module-docstring,missing-function-docstring,missing-class-docstring',
            tmp_path,
        ]
        return _run_process(argv)
    
    return code, out, err, err_msg


def _normalize_severity(raw: Optional[str]) -> str:
    """
    Normalize severity level to standard values.
    
    Args:
        raw: Raw severity string
        
    Returns:
        Normalized severity: 'error', 'warning', or 'info'
    """
    if not raw:
        return 'warning'
    
    raw = raw.lower()
    if raw in ('fatal', 'error'):
        return 'error'
    if raw in ('warning', 'refactor', 'convention'):
        return 'warning'
    if raw in ('info', 'information'):
        return 'info'
    return 'warning'


def _create_diagnostic(line: int, col: int, message: str, severity: str = 'warning') -> Dict:
    """
    Create a standardized diagnostic dictionary.
    
    Args:
        line: Line number (1-indexed)
        col: Column number (1-indexed)
        message: Diagnostic message
        severity: Severity level
        
    Returns:
        Diagnostic dictionary
    """
    return {
        'line': max(1, line),
        'col': max(1, col),
        'message': message.strip() if message else 'Unknown issue',
        'severity': severity,
    }


def _lint_python_pylint(text: str) -> List[Dict]:
    """
    Lint Python code using pylint.
    
    Args:
        text: Python source code
        
    Returns:
        List of diagnostic dictionaries
    """
    diagnostics = []
    
    # Create temporary file
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8') as tmp:
            tmp.write(text)
            tmp_path = tmp.name
    except Exception as exc:
        return [_create_diagnostic(1, 1, f'Failed to create temp file: {exc}', 'error')]

    try:
        # Run pylint
        code, out, err, err_msg = _run_pylint(tmp_path)
        
        if err_msg:
            diagnostics.append(_create_diagnostic(1, 1, err_msg, 'error'))
            return diagnostics

        # Parse JSON output
        try:
            items = json.loads(out or '[]')
        except json.JSONDecodeError as exc:
            # If JSON parsing fails, try to extract useful info from stderr
            if err.strip():
                diagnostics.append(_create_diagnostic(1, 1, err.strip(), 'error'))
            else:
                diagnostics.append(_create_diagnostic(
                    1, 1, f'Failed to parse pylint output: {exc}', 'error'))
            return diagnostics

        # Process diagnostics
        for item in items:
            try:
                diagnostics.append(_create_diagnostic(
                    line=item.get('line', 1) or 1,
                    col=item.get('column', 1) or 1,
                    message=item.get('message', 'Unknown issue'),
                    severity=_normalize_severity(item.get('type'))
                ))
            except Exception:
                # Skip malformed diagnostic items
                continue

        # Add stderr messages if pylint failed with non-standard exit code
        if code not in (0, 1, 2, 4, 8, 16, 32) and err.strip():
            diagnostics.append(_create_diagnostic(1, 1, err.strip(), 'error'))
            
    except Exception as exc:
        diagnostics.append(_create_diagnostic(1, 1, f'Linting error: {exc}', 'error'))
    finally:
        # Clean up temporary file
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return diagnostics


def _parse_compiler_output(output: str) -> List[Dict]:
    """
    Parse GCC/G++/Clang compiler output into diagnostics.
    
    Args:
        output: Compiler output (stderr)
        
    Returns:
        List of diagnostic dictionaries
    """
    diagnostics = []
    
    # Compiler output pattern: file.c:10:5: error: expected ';' before '}'
    pattern = re.compile(
        r'^(?:.*?):(\d+):(\d+):\s*(warning|error|fatal error|note):\s*(.*)$',
        re.MULTILINE
    )
    
    for match in pattern.finditer(output):
        line_no, col_no, sev, msg = match.groups()
        
        try:
            diagnostics.append(_create_diagnostic(
                line=int(line_no),
                col=int(col_no),
                message=msg.strip(),
                severity='error' if 'error' in sev.lower() else 'warning'
            ))
        except Exception:
            # Skip malformed matches
            continue
    
    return diagnostics


def _run_eslint(tmp_path: str) -> Tuple[int, str, str, Optional[str]]:
    argv = [
        'eslint',
        '-f',
        'json',
        tmp_path,
    ]
    return _run_process(argv)


def _lint_javascript_eslint(text: str) -> List[Dict]:
    diagnostics = []
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.js', delete=False, encoding='utf-8') as tmp:
            tmp.write(text)
            tmp_path = tmp.name
    except Exception as exc:
        return [_create_diagnostic(1, 1, f'Failed to create temp file: {exc}', 'error')]

    try:
        code, out, err, err_msg = _run_eslint(tmp_path)
        if err_msg:
            diagnostics.append(_create_diagnostic(1, 1, err_msg, 'error'))
            return diagnostics

        try:
            items = json.loads(out or '[]')
        except json.JSONDecodeError as exc:
            if err.strip():
                diagnostics.append(_create_diagnostic(1, 1, err.strip(), 'error'))
            else:
                diagnostics.append(_create_diagnostic(
                    1, 1, f'Failed to parse eslint output: {exc}', 'error'))
            return diagnostics

        for file_item in items:
            for msg in file_item.get('messages', []):
                diagnostics.append(_create_diagnostic(
                    line=msg.get('line', 1) or 1,
                    col=msg.get('column', 1) or 1,
                    message=msg.get('message', 'Unknown issue'),
                    severity='error' if msg.get('severity', 1) == 2 else 'warning'
                ))

        if code not in (0, 1, 2) and err.strip():
            diagnostics.append(_create_diagnostic(1, 1, err.strip(), 'error'))
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return diagnostics


def _pick_compiler(is_cpp: bool) -> Optional[str]:
    candidates = ['clang++', 'g++'] if is_cpp else ['clang', 'gcc']
    for tool in candidates:
        if shutil.which(tool):
            return tool
    return None


def _lint_c_compiler(text: str, is_cpp: bool) -> List[Dict]:
    """
    Lint C/C++ code using GCC/G++.
    
    Args:
        text: C/C++ source code
        is_cpp: True for C++, False for C
        
    Returns:
        List of diagnostic dictionaries
    """
    diagnostics = []
    suffix = '.cpp' if is_cpp else '.c'
    
    # Create temporary file
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix=suffix, delete=False, encoding='utf-8') as tmp:
            tmp.write(text)
            tmp_path = tmp.name
    except Exception as exc:
        return [_create_diagnostic(1, 1, f'Failed to create temp file: {exc}', 'error')]

    try:
        # Prepare GCC command
        compiler = _pick_compiler(is_cpp)
        if not compiler:
            return [_create_diagnostic(1, 1, 'No compiler found (clang/gcc). Install a compiler first.', 'error')]
        argv = [
            compiler,
            '-fsyntax-only',
            '-Wall',
            '-Wextra',
            '-pedantic',
            '-pipe',
        ]
        
        if is_cpp:
            argv.extend(['-std=c++11'])
        else:
            argv.extend(['-std=c11'])
        
        argv.append(tmp_path)

        # Run GCC
        code, out, err, err_msg = _run_process(argv)
        
        if err_msg:
            diagnostics.append(_create_diagnostic(1, 1, err_msg, 'error'))
            return diagnostics

        # Parse GCC output
        parsed_diagnostics = _parse_compiler_output(err)
        diagnostics.extend(parsed_diagnostics)

        # If compilation failed but no diagnostics were parsed, add generic error
        if code not in (0, 1) and not diagnostics:
            error_msg = err.strip() if err.strip() else f'{compiler} compilation failed'
            diagnostics.append(_create_diagnostic(1, 1, error_msg, 'error'))
            
    except Exception as exc:
        diagnostics.append(_create_diagnostic(1, 1, f'Linting error: {exc}', 'error'))
    finally:
        # Clean up temporary file
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return diagnostics


def lint(text: str, language: str, file_path: Optional[str] = None) -> List[Dict]:
    """
    Main linting function that dispatches to appropriate linter.
    
    Args:
        text: Source code to lint
        language: Programming language ('python', 'c', or 'cpp')
        
    Returns:
        List of diagnostic dictionaries, each containing:
        - line: Line number (1-indexed)
        - col: Column number (1-indexed)
        - message: Diagnostic message
        - severity: 'error', 'warning', or 'info'
    
    Raises:
        LinterError: If an invalid language is specified
    """
    if not text or not text.strip():
        return []
    if _is_ignored(file_path):
        return []

    language = language.lower().strip()
    cache_key = (language, hashlib.sha256(text.encode('utf-8')).hexdigest())
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    
    if language == 'python':
        result = _lint_python_pylint(text)
        _cache_set(cache_key, result)
        return result
    elif language == 'javascript':
        result = _lint_javascript_eslint(text)
        _cache_set(cache_key, result)
        return result
    elif language == 'c':
        result = _lint_c_compiler(text, False)
        _cache_set(cache_key, result)
        return result
    elif language == 'cpp':
        result = _lint_c_compiler(text, True)
        _cache_set(cache_key, result)
        return result
    else:
        raise LinterError(f'Unsupported language: {language}')


def get_supported_languages() -> List[str]:
    """
    Get list of supported programming languages.
    
    Returns:
        List of supported language identifiers
    """
    return ['python', 'javascript', 'c', 'cpp']


def check_tool_availability() -> Dict[str, bool]:
    """
    Check which linting tools are available on the system.
    
    Returns:
        Dictionary mapping tool names to availability status
    """
    tools = {}
    
    # Check pylint
    code, _, _, _ = _run_process(['pylint', '--version'])
    tools['pylint'] = (code == 0)
    
    # Check gcc / g++
    code, _, _, _ = _run_process(['gcc', '--version'])
    tools['gcc'] = (code == 0)
    code, _, _, _ = _run_process(['g++', '--version'])
    tools['g++'] = (code == 0)

    # Check clang / clang++
    code, _, _, _ = _run_process(['clang', '--version'])
    tools['clang'] = (code == 0)
    code, _, _, _ = _run_process(['clang++', '--version'])
    tools['clang++'] = (code == 0)

    # Check eslint
    code, _, _, _ = _run_process(['eslint', '--version'])
    tools['eslint'] = (code == 0)
    
    return tools


# Module-level utility - can be imported and used directly
if __name__ == '__main__':
    print("CodeyLinter - Linting Tool Facade")
    print("=" * 50)
    print("\nThis module provides linting support for:")
    print(f"  Languages: {', '.join(get_supported_languages())}")
    print("\nTo use this module, import it in your Python code:")
    print("  from CodeyLinter import lint")
    print("  diagnostics = lint(your_code, 'python')")
    print("\n" + "=" * 50)
