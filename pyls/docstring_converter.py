# Copyright(c) Microsoft Corporation
# All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the License); you may not use
# this file except in compliance with the License. You may obtain a copy of the
# License at http://www.apache.org/licenses/LICENSE-2.0
#
# THIS CODE IS PROVIDED ON AN  *AS IS* BASIS, WITHOUT WARRANTIES OR CONDITIONS
# OF ANY KIND, EITHER EXPRESS OR IMPLIED, INCLUDING WITHOUT LIMITATION ANY
# IMPLIED WARRANTIES OR CONDITIONS OF TITLE, FITNESS FOR A PARTICULAR PURPOSE,
# MERCHANTABILITY OR NON-INFRINGEMENT.
#
# See the Apache Version 2.0 License for specific language governing
# permissions and limitations under the License.

import re
import sys

DoctestRegex = re.compile(r" *>>> ")

DirectivesExtraNewlineRegex = re.compile(r"^\s*:(param|arg|type|return|rtype|raise|except|var|ivar|cvar|copyright|license)")

PotentialHeaders = [
    (re.compile(r"^\s*=+(\s+=+)+$"), "="),
    (re.compile(r"^\s*-+(\s+-+)+$"), "-"),
    (re.compile(r"^\s*~+(\s+~+)+$"), "~"),
    (re.compile(r"^\s*\++(\s+\++)+$"), "+"),
]

WhitespaceRegex = re.compile(r"\s")

TildaHeaderRegex = re.compile(r"^\s*~~~+$")
PlusHeaderRegex = re.compile(r"^\s*\+\+\++$")
LeadingAsteriskRegex = re.compile(r"^(\s+\* )(.*)$")
UnescapedMarkdownCharsRegex = re.compile(r"(?<!\\)([_*~\[\]])")

# http://docutils.sourceforge.net/docs/ref/rst/restructuredtext.html#literal-blocks
LiteralBlockEmptyRegex = re.compile(r"^\s*::$")
LiteralBlockReplacements = [
    (re.compile(r"\s+::$"), ""),
    (re.compile(r"(\S)\s*::$"), "$1:"),
    # http://docutils.sourceforge.net/docs/ref/rst/restructuredtext.html#interpreted-text
    (re.compile(r":[\w_\-+:.]+:`"), "`"),
    (re.compile(r"`:[\w_\-+:.]+:"), "`"),
]

DirectiveLikeRegex = re.compile(r"^\s*\.\.\s+(\w+)::\s*(.*)$")

SpaceDotDotRegex = re.compile(r"^\s*\.\. ")


def is_null_or_whitespace(s):
    if s is None:
        return True
    return s.isspace()

def count_leading_spaces(s):
    return len(s) - len(s.lstrip())

def last_or_none(l):
    try:
        return l[-1]
    except IndexError:
        return None

class DocstringConverter:

    @staticmethod
    def to_plaintext(docstring):
        """
        Converts a docstring to a plaintext, human readable form. This will
        first strip any common leading indention (like inspect.cleandoc),
        then remove duplicate empty/whitespace lines.

        <param name="docstring">The docstring to convert, likely from the AST.</param>
        <returns>The converted docstring, with Environment.NewLine line endings.</returns>
        """
        if is_null_or_whitespace(docstring):
            return ""

        lines = DocstringConverter.split_docstring(docstring)
        output = []

        for line in lines:
            if is_null_or_whitespace(line) and is_null_or_whitespace(last_or_none(output)):
                continue
            output += line

        return "\n".join(output).rstrip()

    @staticmethod
    def to_markdown(docstring):
        """
        Converts a docstring to a markdown format. This does various things,
        including removing common indention, escaping characters, handling
        code blocks, and more.

        <param name="docstring">The docstring to convert, likely from the AST.</param>
        <returns>The converted docstring, with Environment.NewLine line endings.</returns>
        """
        if is_null_or_whitespace(docstring):
            return ""

        return DocstringConverter(docstring).convert()

    def next_block_indent(self):
        lines = self._lines[(self._lineNum + 1):]

        i = 0
        while i < len(lines) and is_null_or_whitespace(lines[i]):
            i += 1

        lines = lines[i:]

        if len(lines) == 0:
            result = ""
        else:
            result = lines[0]

        i = 0
        while i < len(result) and result[i].isspace():
            i += 1

        return i

    def __init__(self, inp):
        self._skipAppendEmptyLine = True
        self._insideInlineCode = False
        self._appendDirectiveBlock = False

        self._stateStack = [] # stack of Action

        self._lineNum = 0
        self._blockIndent = 0

        self._builder = ""
        self._state = self.parse_text
        self._lines = self.split_docstring(inp)

    def current_indent(self):
        return count_leading_spaces(self.current_line())

    def line_at(self, i):
        try:
            return self._lines[i]
        except IndexError:
            return None # TODO: return empty string instead?

    def current_line_is_outside_block(self):
        return self.current_indent() < self._blockIndent

    def current_line_within_block(self):
        return self.current_line()[self._blockIndent:]

    def current_line(self):
        try:
            return self._lines[self._lineNum]
        except IndexError:
            return None # TODO: return empty string instead?

    def eat_line(self):
        self._lineNum += 1

    def convert(self):
        while self.current_line() is not None:
            before = self._state
            before_line = self._lineNum

            self._state()

            # Parser must make progress; either the state or line number must change.
            if self._state == before and self._lineNum == before_line:
                print("Infinite loop during docstring conversion")
                sys.exit(1)

        # Close out any outstanding code blocks.
        if self._state == self.parse_backtick_block or \
                self._state == self.parse_doctest or \
                self._state == self.parse_literal_block:
            self.trim_output_and_append_line("```")
        elif self._insideInlineCode:
            self.trim_output_and_append_line("`", True)

        return self._builder.strip()

    def push_and_set_state(self, next_state):
        if self._state == self.parse_text:
            _insideInlineCode = False

        self._stateStack.append(self._state)
        self._state = next_state

    def pop_state(self):
        self._state = self._stateStack.pop()

        if self._state == self.parse_text:
            # Terminate inline code when leaving a block.
            self._insideInlineCode = False

    def parse_text(self):
        if is_null_or_whitespace(self.current_line()):
            self._state = self.parse_empty
            return

        if self.begin_backtick_block():
            return

        if self.begin_literal_block():
            return

        if self.begin_doc_test():
            return

        if self.begin_directive():
            return

        # TODO: Push into Google/Numpy style list parser.

        self.append_text_line(self.current_line())
        self.eat_line()

    def append_text_line(self, line):
        line = self.preprocess_text_line(line)

        # Hack: attempt to put directives lines into their own paragraphs.
        # This should be removed once proper list-like parsing is written.
        if (not self._insideInlineCode) and DirectivesExtraNewlineRegex.match(line):
            self.append_line()

        parts = line.split('`')

        for i in range(len(parts)):
            part = parts[i]

            if i > 0:
                self._insideInlineCode = not self._insideInlineCode
                self.append('`')

            if self._insideInlineCode:
                self.append(part)
                continue

            if i == 0:
                # Only one part, and not inside code, so check header cases.
                if len(parts) == 1:
                    # Handle weird separator lines which contain random spaces.
                    for (regex, replacement) in PotentialHeaders:
                        if regex.match(part):
                            part = WhitespaceRegex.Replace(part, replacement)
                            break

                    # Replace ReST style ~~~ header to prevent it being interpreted as a code block
                    # (an alternative in Markdown to triple backtick blocks).
                    if TildaHeaderRegex.match(part):
                        self.append(part.replace('~', '-'))
                        continue

                    # Replace +++ heading too.
                    # TODO: Handle the rest of these, and the precedence order (which depends on the
                    # order heading lines are seen, not what the line contains).
                    # http://docutils.sourceforge.net/docs/ref/rst/restructuredtext.html#sections
                    if PlusHeaderRegex.match(part):
                        self.append(part.Replace('+', '-'))
                        continue

                # Don't strip away asterisk-based bullet point lists.
                # TODO: Replace this with real list parsing. This may have
                # false positives and cause random italics when the ReST list
                # doesn't match Markdown's specification.
                match = LeadingAsteriskRegex.match(part)
                if match:
                    self.append(match.Groups[1].Value)
                    part = match.Groups[2].Value

            # TODO: Find a better way to handle this; the below breaks escaped
            # characters which appear at the beginning or end of a line.
            # Applying this only when i == 0 or i == parts.Length-1 may work.

            # http://docutils.sourceforge.net/docs/ref/rst/restructuredtext.html#hyperlink-references
            # part = Regex.Replace(part, @"^_+", "")
            # http://docutils.sourceforge.net/docs/ref/rst/restructuredtext.html#inline-internal-targets
            # part = Regex.Replace(part, @"_+$", "")

            # TODO: Strip footnote/citation references.

            # Escape _, *, and ~, but ignore things like ":param \*\*kwargs:".
            part = UnescapedMarkdownCharsRegex.Replace(part, r"\$1")

            self.append(part)

        # Go straight to the builder so that append_line doesn't think
        # we're actually trying to insert an extra blank line and skip
        # future whitespace. Empty line deduplication is already handled
        # because Append is used above.
        self._builder += "\n"

    @staticmethod
    def preprocess_text_line(line):
        # http://docutils.sourceforge.net/docs/ref/rst/restructuredtext.html#literal-blocks
        if LiteralBlockEmptyRegex.match(line):
            return ""

        for (regex, replacement) in LiteralBlockReplacements:
            line = regex.Replace(line, replacement)

        line = line.replace("``", "`")
        return line

    def parse_empty(self):
        if is_null_or_whitespace(self.current_line()):
            self.append_line()
            self.eat_line()
            return

        self._state = self.parse_text

    def begin_min_indent_code_block(self, state):
        self.append_line("```")
        self.push_and_set_state(state)
        self._blockIndent = self.current_indent()

    def begin_backtick_block(self):
        if self.current_line().StartsWith("```"):
            self.append_line(self.current_line())
            self.push_and_set_state(self.parse_backtick_block)
            self.eat_line()
            return True

        return False

    def parse_backtick_block(self):
        if self.current_line().StartsWith("```"):
            self.append_line("```")
            self.append_line()
            self.pop_state()
        else:
            self.append_line(self.current_line())

        self.eat_line()

    def begin_doc_test(self):
        if not DoctestRegex.match(self.current_line()):
            return False

        self.begin_min_indent_code_block(self.parse_doctest)
        self.append_line(self.current_line_within_block())
        self.eat_line()

        return True

    def parse_doctest(self):
        if self.current_line_is_outside_block() or is_null_or_whitespace(self.current_line()):
            self.trim_output_and_append_line("```")
            self.append_line()
            self.pop_state()
            return

        self.append_line(self.current_line_within_block())
        self.eat_line()

    def begin_literal_block(self):
        # The previous line must be empty.
        prev = self.line_at(self._lineNum - 1)
        if prev is None:
            return False
        elif not is_null_or_whitespace(prev):
            return False

        # Find the previous paragraph and check that it ends with ::
        i = self._lineNum - 2
        while i >= 0:
            line = self.line_at(i)

            if is_null_or_whitespace(line):
                i -= 1
                continue

            # Safe to ignore whitespace after the :: because all lines have been TrimEnd'd.
            if line.endswith("::"):
                break

            return False

        if i < 0:
            return False

        # Special case: allow one-liners at the same indent level.
        if self.current_indent() == 0:
            self.append_line("```")
            self.push_and_set_state(self.parse_literal_block_single_line)
            return True

        self.begin_min_indent_code_block(self.parse_literal_block)

        return True

    def parse_literal_block(self):
        # Slightly different than doctest, wait until the first non-empty unindented line to exit.
        if is_null_or_whitespace(self.current_line()):
            self.append_line()
            self.eat_line()
            return

        if self.current_line_is_outside_block():
            self.trim_output_and_append_line("```")
            self.append_line()
            self.pop_state()
            return

        self.append_line(self.current_line_within_block())
        self.eat_line()

    def parse_literal_block_single_line(self):
        self.append_line(self.current_line())
        self.append_line("```")
        self.append_line()
        self.pop_state()
        self.eat_line()

    def begin_directive(self):
        if not SpaceDotDotRegex.match(self.current_line()):
            return False

        self.push_and_set_state(self.parse_directive)
        self._blockIndent = self.next_block_indent()
        self._appendDirectiveBlock = False
        return True

    def parse_directive(self):
        # http://docutils.sourceforge.net/docs/ref/rst/restructuredtext.html#directives

        match = DirectiveLikeRegex.Match(self.current_line())
        if match.Success:
            directive_type = match.Groups[1].Value
            directive = match.Groups[2].Value

            if directive_type == "class":
                self._appendDirectiveBlock = True
                self.append_line()
                self.append_line("```")
                self.append_line(directive)
                self.append_line("```")
                self.append_line()

        if self._blockIndent == 0:
            # This is a one-liner directive, so pop back.
            self.pop_state()
        else:
            self._state = self.parse_directive_block

        self.eat_line()

    def parse_directive_block(self):
        if (not is_null_or_whitespace(self.current_line())) and self.current_line_is_outside_block():
            self.pop_state()
            return

        if self._appendDirectiveBlock:
            # This is a bit of a hack. This just trims the text and appends it
            # like top-level text, rather than doing actual indent-based recursion.
            self.append_text_line(self.current_line().lstrip())

        self.eat_line()

    def append_line(self, line=None):
        if not is_null_or_whitespace(line):
            self._builder += "\n" + line
            self._skipAppendEmptyLine = False
        elif not self._skipAppendEmptyLine:
            self._builder += "\n"
            self._skipAppendEmptyLine = True

    def append(self, text):
        self._builder += text
        self._skipAppendEmptyLine = False

    def trim_output_and_append_line(self, line=None, no_newline=False):
        self._builder.rstrip()
        self._skipAppendEmptyLine = False

        if not no_newline:
            self.append_line()

        self.append_line(line)

    @staticmethod
    def split_docstring(docstring):
        # As done by inspect.cleandoc.
        docstring = docstring.replace("\t", "        ")

        lines = [x.rstrip() for x in docstring.split()]

        if len(lines) > 0:
            first = lines[0].lstrip()

            if first == "":
                first = None
            else:
                lines = lines[1:]

            lines = DocstringConverter.strip_leading_whitespace(lines)

            if first is not None:
                lines.insert(0, first)

        return lines

    @staticmethod
    def strip_leading_whitespace(lines, trim=None): # List<string>
        if trim is None:
            amount = DocstringConverter.largest_trim(lines)
        else:
            amount = trim

        return ["" if amount > len(x) else x[amount:] for x in lines]

    @staticmethod
    def largest_trim(lines): # int
        counts = [count_leading_spaces(x) for x in lines if not is_null_or_whitespace(x)]
        if len(counts) == 0:
            return 0
        else:
            return min(counts)
