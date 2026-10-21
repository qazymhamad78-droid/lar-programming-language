#!/usr/bin/env python3
"""
lar interpreter

Run a lar program:
    python lar.py file.lr
"""
import random
import re
import sys

TYPES = ('wrd', 'num', 'flt', 'bl')
CLOSER = re.compile(r'^[\)\}\]>;\s]+$')
ELSE = re.compile(r'^else\b')


class LarError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(msg)
        self.line = line


# ---------------------------------------------------------------- helpers

def is_word(ch):
    return bool(ch) and (ch.isalnum() or ch == '_')


def show(v):
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    return str(v)


def string_end(text, start):
    """Index of the closing quote of the string starting at `start`, or -1.
    A quote followed by a letter/digit (like the ' in can't) doesn't close."""
    q = text[start]
    i = start + 1
    while i < len(text) and text[i] != '\n':
        if text[i] == q and not is_word(text[i + 1] if i + 1 < len(text) else ''):
            return i
        i += 1
    return -1


def standalone(src, i, n):
    before = src[i - 1] if i > 0 else ''
    after = src[i + n] if i + n < len(src) else ''
    return not is_word(before) and not is_word(after) and before != '.' and after != '.'


# ---------------------------------------------------------------- preprocessing

def strip_comments(src):
    """Remove `@ ...` comments and `00 ... 11` comments."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in '\'"':
            j = string_end(src, i)
            if j < 0:
                out.append(c)
                i += 1
            else:
                out.append(src[i:j + 1])
                i = j + 1
        elif c == '@':
            while i < n and src[i] != '\n':
                i += 1
        elif c == '0' and src.startswith('00', i) and standalone(src, i, 2):
            end, j = n, i + 2
            while True:
                k = src.find('11', j)
                if k < 0:
                    break
                if standalone(src, k, 2):
                    end = k + 2
                    break
                j = k + 1
            out.append('\n' * src.count('\n', i, end))
            i = end
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def split_chain(line):
    """Split a line on `-` (the lar AND chain), ignoring dashes in strings."""
    parts, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c in '\'"':
            j = string_end(line, i)
            if j >= 0:
                cur.append(line[i:j + 1])
                i = j + 1
                continue
        if c == '-':
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append(''.join(cur))
    return [p.strip() for p in parts if p.strip()]


def prepare(src):
    """Turn source text into a list of (line_number, statement)."""
    lines = []
    for no, raw in enumerate(strip_comments(src).split('\n'), 1):
        raw = raw.strip()
        if not raw:
            continue
        if re.match(r'^(import|use|for|if|else|Fm)\b', raw) or CLOSER.match(raw):
            lines.append((no, raw))
        else:
            for part in split_chain(raw):
                lines.append((no, part))
    return lines


# ---------------------------------------------------------------- block parser

class BlockParser:
    def __init__(self, lines):
        self.lines = lines
        self.i = 0

    def block(self):
        nodes = []
        L = self.lines
        while self.i < len(L):
            no, ln = L[self.i]
            if CLOSER.match(ln):
                self.i += 1
                return nodes, 'close'
            if ELSE.match(ln):
                return nodes, 'else'
            self.i += 1

            if re.match(r'^(import|Fm)\b', ln):
                continue

            if re.match(r'^use\b', ln):
                if ln.endswith('{'):
                    body, why = self.block()
                    nodes.append(('block', no, body))
                    if why == 'else':
                        return nodes, 'else'
                continue

            m = re.match(r'^for\s+(\w+)\s*<\s*(.*?)\s*>\s*(.*)$', ln)
            if m:
                lo, sep, hi = m.group(2).partition(',')
                if not sep:
                    raise LarError('for needs a range like <1,6>', no)
                body, why = self.block()
                nodes.append(('for', no, m.group(1), lo.strip(), hi.strip(), body))
                if why == 'else':
                    return nodes, 'else'
                continue

            m = re.match(r'^if\b\s*(.*)$', ln)
            if m:
                nodes.append(self.if_chain(no, m.group(1)))
                continue

            m = re.match(r'^(print|fnint)\b\s*(.*)$', ln)
            if m:
                nodes.append(('print', no, m.group(2)))
                continue

            m = re.match(r'^([A-Za-z_]\w*)\s*(?::mean:|::)\s*(.*)$', ln)
            if m:
                nodes.append(('assign', no, m.group(1), m.group(2)))
                continue

            nodes.append(('expr', no, ln))
        return nodes, None

    def if_chain(self, no, condtext):
        def clean(c):
            return re.sub(r'\s*<\s*$', '', c).strip()

        L = self.lines
        branches, else_body = [], None
        cond = clean(condtext)
        while True:
            body, why = self.block()
            branches.append((cond, body))
            has_else = self.i < len(L) and ELSE.match(L[self.i][1])
            if why == 'else' or (why == 'close' and has_else):
                ln = L[self.i][1]
                self.i += 1
                m = re.match(r'^else\s+if\b\s*(.*)$', ln)
                if m:
                    cond = clean(m.group(1))
                    continue
                else_body, _ = self.block()
            break
        return ('if', no, branches, else_body)


def parse(lines):
    bp = BlockParser(lines)
    nodes = []
    while bp.i < len(lines):
        body, why = bp.block()
        nodes += body
        if why == 'else':
            bp.i += 1  # stray else
    return nodes


# ---------------------------------------------------------------- expressions

def lex(text):
    toks = []
    i, n = 0, len(text)
    ins = False
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith('(ins)', i):
            ins = True
            i += 5
        elif c in '\'"':
            j = string_end(text, i)
            if j < 0:
                raise LarError('unterminated string')
            s = text[i + 1:j]
            i = j + 1
            ol = False
            if text.startswith('/ol', i) and not is_word(text[i + 3:i + 4]):
                ol = True
                i += 3
            toks.append(('str', (s, ins, ol)))
            ins = False
        elif c.isdigit():
            m = re.compile(r'\d+(\.\d+)?').match(text, i)
            s = m.group()
            toks.append(('num', float(s) if '.' in s else int(s)))
            i = m.end()
        elif c.isalpha() or c == '_':
            m = re.compile(r'[A-Za-z_][\w.]*').match(text, i)
            toks.append(('name', m.group()))
            i = m.end()
        elif text.startswith(('::', '==', '!='), i):
            toks.append(('op', text[i:i + 2]))
            i += 2
        elif c == '|':
            toks.append(('bool', False))
            i += 1
        elif c == '/':
            toks.append(('bool', True))
            i += 1
        elif c in '^+*()[]{},':
            toks.append(('op', c))
            i += 1
        else:
            raise LarError(f"unexpected character {c!r}")
    return toks


class ExprParser:
    def __init__(self, toks):
        self.t = toks
        self.p = 0

    def peek(self):
        return self.t[self.p] if self.p < len(self.t) else (None, None)

    def next(self):
        tok = self.peek()
        self.p += 1
        return tok

    def is_op(self, *ops):
        k, v = self.peek()
        return k == 'op' and v in ops

    def parse(self):
        if not self.t:
            raise LarError('empty expression')
        node = self.cmp()
        if self.p < len(self.t):
            raise LarError(f'unexpected {self.peek()[1]!r}')
        return node

    def cmp(self):
        a = self.choice()
        if self.is_op('::', '==', '!='):
            op = self.next()[1]
            return ('cmp', op, a, self.choice())
        return a

    def choice(self):
        opts = [self.add()]
        while self.is_op('^'):
            self.next()
            opts.append(self.add())
        return opts[0] if len(opts) == 1 else ('choice', opts)

    def add(self):
        a = self.mul()
        while self.is_op('+'):
            self.next()
            a = ('add', a, self.mul())
        return a

    def mul(self):
        a = self.atom()
        while self.is_op('*'):
            self.next()
            a = ('mul', a, self.atom())
        return a

    def atom(self):
        k, v = self.next()
        if k in ('num', 'str', 'bool'):
            return (k, v)
        if k == 'name':
            if v in TYPES and self.is_op('[', '('):
                self.next()
                while self.is_op('{', '('):
                    self.next()
                e = self.choice()
                while self.is_op(')', '}', ']'):
                    self.next()
                return ('cast', v, e)
            if v == 'input':
                if self.p >= len(self.t) or self.is_op(')', '}', ']'):
                    return ('input', ('str', ('', False, False)))
                return ('input', self.atom())
            return ('var', v)
        if k == 'op' and v == '(':
            e = self.choice()
            if not self.is_op(')'):
                raise LarError("missing ')'")
            self.next()
            return e
        raise LarError('bad expression')


# ---------------------------------------------------------------- interpreter

class Interp:
    def __init__(self):
        self.vars = {}
        self.line = 0
        self.oneline = False

    def lookup(self, name):
        if name not in self.vars:
            raise LarError(f"'{name}' is not defined")
        return self.vars[name]

    def eval_text(self, text):
        return self.evaluate(ExprParser(lex(text)).parse())

    def evaluate(self, node):
        k = node[0]
        if k in ('num', 'bool'):
            return node[1]
        if k == 'str':
            s, ins, ol = node[1]
            if ol:
                self.oneline = True
            if ins:
                s = re.sub(r'\[([^\[\]]+)\]',
                           lambda m: show(self.lookup(m.group(1).strip('{}() '))), s)
            return s
        if k == 'var':
            return self.lookup(node[1])
        if k == 'choice':
            return self.evaluate(random.choice(node[1]))
        if k == 'add':
            a, b = self.evaluate(node[1]), self.evaluate(node[2])
            if isinstance(a, str) or isinstance(b, str):
                return show(a) + show(b)
            return a + b
        if k == 'mul':
            a, b = self.evaluate(node[1]), self.evaluate(node[2])
            try:
                return a * b
            except TypeError:
                raise LarError("can't multiply those")
        if k == 'cmp':
            a, b = self.evaluate(node[2]), self.evaluate(node[3])
            return (a != b) if node[1] == '!=' else (a == b)
        if k == 'cast':
            return self.cast(node[1], self.evaluate(node[2]))
        if k == 'input':
            prompt = show(self.evaluate(node[1]))
            sys.stdout.write(prompt + (' ' if prompt else ''))
            sys.stdout.flush()
            try:
                return input()
            except EOFError:
                return ''
        raise LarError('bad expression')

    def cast(self, t, v):
        try:
            if t == 'wrd':
                return show(v)
            if t == 'num':
                return int(float(v)) if isinstance(v, str) else int(v)
            if t == 'flt':
                return float(v)
            if t == 'bl':
                if isinstance(v, str):
                    return v.strip().lower() not in ('', '|', 'false', '0')
                return bool(v)
        except (ValueError, TypeError):
            raise LarError(f"can't turn {show(v)!r} into {t}")

    def run(self, nodes):
        for node in nodes:
            self.exec(node)

    def exec(self, node):
        kind = node[0]
        self.line = node[1]
        if kind == 'block':
            self.run(node[2])
        elif kind == 'assign':
            self.vars[node[2]] = self.eval_text(node[3])
        elif kind == 'print':
            self.oneline = False
            v = self.eval_text(node[2]) if node[2].strip() else ''
            sys.stdout.write(show(v) + ('' if self.oneline else '\n'))
            sys.stdout.flush()
        elif kind == 'expr':
            self.eval_text(node[2])
        elif kind == 'for':
            lo, hi = self.eval_text(node[3]), self.eval_text(node[4])
            if not isinstance(lo, int) or not isinstance(hi, int):
                raise LarError('loop range must be whole numbers')
            for x in range(lo, hi + 1):
                self.vars[node[2]] = x
                self.run(node[5])
        elif kind == 'if':
            for cond, body in node[2]:
                self.line = node[1]
                if self.eval_text(cond):
                    self.run(body)
                    return
            if node[3] is not None:
                self.run(node[3])


def main():
    if len(sys.argv) != 2:
        print('usage: python lar.py file.lr')
        sys.exit(1)
    try:
        with open(sys.argv[1], encoding='utf-8') as f:
            src = f.read()
    except OSError as e:
        print(f"lar: can't open {sys.argv[1]}: {e.strerror}")
        sys.exit(1)
    interp = Interp()
    try:
        interp.run(parse(prepare(src)))
    except LarError as e:
        sys.stdout.flush()
        sys.stderr.write(f'lar error (line {e.line or interp.line}): {e}\n')
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == '__main__':
    main()
