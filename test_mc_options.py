"""Gate for the multiple-choice option canonicalizer in main.py.

Reproduces the 2026-08-25 production failure (Metaculus HTTP 400
"Forecast must reflect current options") without importing the heavy
forecasting_tools dependency: the two methods under test are extracted from
main.py by AST and executed against stubs.
"""

import ast
import io
import sys
import types
from dataclasses import dataclass

SRC = io.open("main.py", encoding="utf-8").read()


@dataclass
class PredictedOption:
    option_name: str
    probability: float


class PredictedOptionList:
    def __init__(self, predicted_options):
        total = sum(o.probability for o in predicted_options)
        if total <= 0:
            raise ValueError("distribution sums to zero")  # mirrors upstream validator
        self.predicted_options = [
            PredictedOption(o.option_name, o.probability / total)
            for o in predicted_options
        ]


class Question:
    def __init__(self, options):
        self.options = options
        self.page_url = "https://www.metaculus.com/questions/45325"


def _load_canonicalizer():
    tree = ast.parse(SRC)
    wanted = {"_normalize_option_name", "_canonicalize_option_list"}
    funcs = [
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name in wanted
    ]
    assert {f.name for f in funcs} == wanted, f"methods missing from main.py: {wanted}"
    stub_ft = types.ModuleType("forecasting_tools")
    stub_ft.PredictedOption = PredictedOption
    sys.modules["forecasting_tools"] = stub_ft

    class Logger:
        def warning(self, *a, **k):
            pass

    ns = {
        "re": __import__("re"),
        "PredictedOptionList": PredictedOptionList,
        "MultipleChoiceQuestion": Question,
        "logger": Logger(),
    }
    for f in funcs:
        f.decorator_list = []  # drop @staticmethod; call unbound below
        exec(compile(ast.Module(body=[f], type_ignores=[]), "main.py", "exec"), ns)

    class Holder:
        _normalize_option_name = staticmethod(ns["_normalize_option_name"])
        _canonicalize_option_list = ns["_canonicalize_option_list"]

    return Holder()


HOLDER = _load_canonicalizer()
Q45325 = [
    "IonQ (IONQ)",
    "Rigetti (RGTI)",
    "D-Wave Quantum (QBTS)",
    "Quantum Computing Inc. (QUBT)",
]


def canon(options, parsed):
    return HOLDER._canonicalize_option_list(
        Question(options),
        PredictedOptionList([PredictedOption(n, p) for n, p in parsed]),
    )


def names(result):
    return [o.option_name for o in result.predicted_options]


def probs(result):
    return [round(o.probability, 6) for o in result.predicted_options]


passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS {label}")
    else:
        failed += 1
        print(f"FAIL {label}")


# A1: the exact production failure -- parser dropped the ticker suffixes.
r = canon(
    Q45325,
    [("IonQ", 0.4), ("Rigetti", 0.3), ("D-Wave Quantum", 0.2), ("Quantum Computing Inc.", 0.1)],
)
check("A1 truncated names remapped to exact option names", names(r) == Q45325)
check("A1 probabilities preserved in order", probs(r) == [0.4, 0.3, 0.2, 0.1])

# A2: exact names pass through untouched.
r = canon(Q45325, [(o, 0.25) for o in Q45325])
check("A2 exact names unchanged", names(r) == Q45325 and probs(r) == [0.25] * 4)

# A3: a dropped option is filled with 0.0 and the rest renormalize.
r = canon(Q45325, [("IonQ", 0.5), ("Rigetti", 0.3), ("D-Wave Quantum", 0.2)])
check("A3 missing option present with 0.0", names(r) == Q45325 and probs(r)[3] == 0.0)

# A4: extra/hallucinated options are discarded, never submitted.
r = canon(Q45325, [("IonQ", 0.5), ("Some Other Co", 0.5)])
check("A4 unknown parsed names dropped", names(r) == Q45325)

# A5: "Option X:" prefixes are still recoverable.
r = canon(["Yes", "No"], [("Option Yes", 0.7), ("Option No", 0.3)])
check("A5 prefixed names remapped", names(r) == ["Yes", "No"])

# A6: exact matches win over fuzzy ones (no stealing across options).
r = canon(["Yes", "Yes and more"], [("Yes and more", 0.8), ("Yes", 0.2)])
check("A6 exact match not stolen by earlier option", probs(r) == [0.2, 0.8])

# A7: nothing recoverable -> raise, so the caller falls back to persona average.
try:
    canon(Q45325, [("Totally Unrelated", 1.0)])
    check("A7 unmappable distribution raises", False)
except ValueError:
    check("A7 unmappable distribution raises", True)

# A8: every returned name is one of the question's options (the 400 invariant).
r = canon(Q45325, [("ionq (IONQ)", 0.6), ("RIGETTI (RGTI)", 0.4)])
check("A8 all returned names are valid options", set(names(r)) <= set(Q45325))

print(f"\n{'ALL PASS' if not failed else 'FAILURES'}: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
