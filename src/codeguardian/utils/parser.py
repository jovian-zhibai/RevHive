"""Code parsing utilities using tree-sitter."""

from dataclasses import dataclass


@dataclass
class CodeStructure:
    """Parsed structure of a code file."""
    functions: list[str]
    classes: list[str]
    imports: list[str]
    language: str


def parse_code(code: str, language: str = "python") -> CodeStructure:
    """Parse code to extract its structure.

    Uses tree-sitter for multi-language AST parsing.
    Falls back to regex-based parsing if tree-sitter is not available.
    """
    try:
        return _parse_with_tree_sitter(code, language)
    except Exception:
        return _parse_with_regex(code, language)


def _parse_with_tree_sitter(code: str, language: str) -> CodeStructure:
    """Parse code using tree-sitter for accurate AST analysis."""
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser

    lang_map = {
        "python": tspython.language(),
    }

    parser = Parser(Language(lang_map.get(language, tspython.language())))
    tree = parser.parse(code.encode())

    functions = []
    classes = []
    imports = []

    def walk(node):
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                functions.append(name_node.text.decode())
        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                classes.append(name_node.text.decode())
        elif node.type in ("import_statement", "import_from_statement"):
            imports.append(node.text.decode())

        for child in node.children:
            walk(child)

    walk(tree.root_node)

    return CodeStructure(
        functions=functions,
        classes=classes,
        imports=imports,
        language=language,
    )


def _parse_with_regex(code: str, language: str) -> CodeStructure:
    """Fallback regex-based parsing."""
    import re

    functions = re.findall(r"def\s+(\w+)\s*\(", code)
    classes = re.findall(r"class\s+(\w+)", code)
    imports = re.findall(r"^(?:import|from)\s+.+$", code, re.MULTILINE)

    return CodeStructure(
        functions=functions,
        classes=classes,
        imports=imports,
        language=language,
    )
