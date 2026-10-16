"""Programme principal qui appelle une fonction du Cerveau sur des entrées et écrit une valeur JSON par
ligne, dans chacun des dix langages (`apex.brain.crosscheck` à l'exécution, `apex.neural.polyglot` pour
l'évaluation et les données). Ici, côté inférence : le code d'inférence n'importe jamais le code
d'entraînement."""

from __future__ import annotations

import json


def harness(language: str, name: str, inputs: list[list[int]]) -> tuple[str, str]:
    """(nom de fichier, programme principal) qui appelle `name` sur chaque entrée et écrit une valeur JSON
    par ligne."""
    if language == "python":
        calls = "".join(f"print(json.dumps({name}({json.dumps(i)})))\n" for i in inputs)
        return "main.py", "import json\n" + calls
    if language in ("javascript", "typescript"):
        lines = "".join(f"console.log(JSON.stringify({name}({json.dumps(i)})));\n" for i in inputs)
        return ("main.ts" if language == "typescript" else "main.js"), lines
    if language == "csharp":
        calls = "".join(
            f"        Show(Solution.{name}(new long[]{{{', '.join(map(str, i))}}}));\n" for i in inputs
        )
        return "main.cs", (
            "public static class Program\n{\n"
            "    static void Show(long v) { System.Console.WriteLine(v); }\n"
            "    static void Show(int v) { System.Console.WriteLine(v); }\n"
            "    static void Show(System.Collections.Generic.IEnumerable<long> v) { "
            'System.Console.WriteLine("[" + string.Join(",", v) + "]"); }\n'
            f"    public static void Main()\n    {{\n{calls}    }}\n}}\n"
        )
    if language == "cpp":
        calls = "".join(
            f"    show({name}(std::vector<long long>{{{', '.join(map(str, i))}}}));\n" for i in inputs
        )
        return "main.cpp", (
            "#include <iostream>\n#include <vector>\n"
            "static void show(long long v) { std::cout << v << '\\n'; }\n"
            "static void show(const std::vector<long long>& v) { std::cout << '['; "
            "for (size_t i = 0; i < v.size(); ++i) "
            "{ if (i) std::cout << ','; std::cout << v[i]; } std::cout << \"]\\n\"; }\n"
            f"int main() {{\n{calls}    return 0;\n}}\n"
        )
    if language == "java":
        calls = "".join(
            f"        show(Solution.{name}(new long[]{{{', '.join(map(str, i))}}}));\n" for i in inputs
        )
        return "Main.java", (
            "public class Main {\n"
            "    static void show(long v) { System.out.println(v); }\n"
            '    static void show(java.util.List<Long> v) { StringBuilder s = new StringBuilder("["); '
            "for (int i = 0; i < v.size(); i++) { if (i > 0) s.append(','); s.append(v.get(i)); } "
            "System.out.println(s.append(']')); }\n"
            f"    public static void main(String[] args) {{\n{calls}    }}\n}}\n"
        )
    if language == "rust":
        calls = "".join(
            f'    println!("{{:?}}", {name}(&[{", ".join(f"{v}i64" for v in i)}]));\n'
            if i
            else f'    println!("{{:?}}", {name}(&[]));\n'
            for i in inputs
        )
        return "main.rs", f"fn main() {{\n{calls}}}\n"
    if language == "go":
        calls = "".join(f"\tshow({name}([]int64{{{', '.join(map(str, i))}}}))\n" for i in inputs)
        return "main.go", (
            'func show(v any) {\n\tb, _ := json.Marshal(v)\n\tif string(b) == "null" {\n'
            '\t\tb = []byte("[]")\n\t}\n'
            f"\tfmt.Println(string(b))\n}}\n\nfunc main() {{\n{calls}}}\n"
        )
    if language == "php":
        calls = "".join(f'echo json_encode({name}({json.dumps(i)})), "\\n";\n' for i in inputs)
        return "main.php", calls
    if language == "ruby":
        calls = "".join(f"puts JSON.generate({name}({json.dumps(i)}))\n" for i in inputs)
        return "main.rb", "require 'json'\n" + calls
    raise ValueError(language)


def program(language: str, code: str, name: str, inputs: list[list[int]]) -> tuple[str, str]:
    """(nom de fichier, source complet) : `code` suivi d'un programme principal qui appelle `name` sur
    chaque entrée et écrit une valeur JSON par ligne (évaluation, et vérification croisée
    `apex.brain.crosscheck`)."""
    file_name, main = harness(language, name, inputs)
    if language == "go":
        return file_name, 'package main\n\nimport (\n\t"encoding/json"\n\t"fmt"\n)\n\n' + code + "\n" + main
    if language == "php":
        # Le code commence par « <?php » ; une balise fermante éventuelle est rouverte avant le programme.
        return file_name, code.rstrip() + ("\n<?php\n" if code.rstrip().endswith("?>") else "\n") + main
    return file_name, code + "\n" + main
