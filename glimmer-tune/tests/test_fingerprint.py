from a2ui_eval.fingerprint import fingerprint

BASE = """surface("s")
root = Card(col)
col = Column([title, btn])
title = Text("Hello")
btn_text = Text("Go")
btn = Button(btn_text, _, Event("go", {}))
"""


def fp(sdk, index, express):
    return fingerprint(sdk.compile_express(express), index)


def test_text_invariant(sdk, index):
    other = BASE.replace('"Hello"', '"Goodbye"').replace('"go"', '"leave"').replace('surface("s")', 'surface("t")')
    assert fp(sdk, index, BASE) == fp(sdk, index, other)


def test_id_invariant(sdk, index):
    other = BASE.replace("col", "main").replace("title", "heading")
    assert fp(sdk, index, BASE) == fp(sdk, index, other)


def test_added_child_differs(sdk, index):
    other = BASE.replace("[title, btn]", "[title, sub, btn]") + 'sub = Text("More")\n'
    assert fp(sdk, index, BASE) != fp(sdk, index, other)


def test_type_change_differs(sdk, index):
    other = BASE.replace("root = Card(col)", "root = Column([col])")
    assert fp(sdk, index, BASE) != fp(sdk, index, other)


def test_child_order_matters(sdk, index):
    other = BASE.replace("[title, btn]", "[btn, title]")
    assert fp(sdk, index, BASE) != fp(sdk, index, other)
