"""codegen and analyze_code with the model stubbed: what codegen saves for each
shape of model reply. No Ollama, GPU, network or database: the LLM service
module is replaced and output goes to tmp_path."""
import sys
import types
from types import SimpleNamespace

import pytest

import backend.tools.code_tools as ct


class StubLLM:
    def __init__(self, reply="x = 1", model="stub-model", context_window=8192):
        self.reply, self.model, self.context_window = reply, model, context_window
        self.prompts = []

    def chat(self, messages):
        self.prompts.append(messages[0].content)
        return SimpleNamespace(message=SimpleNamespace(content=self.reply))


@pytest.fixture
def llm(tmp_path, monkeypatch):
    """Installs a stub LLM service and returns a function that sets the client
    the next calls receive."""
    monkeypatch.setattr("backend.config.OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr("backend.config.UPLOAD_DIR", str(tmp_path / "uploads"))
    (tmp_path / "uploads").mkdir()
    state = {"llm": StubLLM()}
    fake = types.ModuleType("backend.utils.llm_service")
    fake.ChatMessage = lambda role, content: SimpleNamespace(role=role, content=content)
    fake.MessageRole = SimpleNamespace(USER="user")
    fake.get_default_llm = lambda: state["llm"]
    monkeypatch.setitem(sys.modules, "backend.utils.llm_service", fake)

    def use(stub):
        state["llm"] = stub
        return stub

    return use


def _generate(tmp_path, filename="out.py", **kwargs):
    kwargs.setdefault("instructions", "write a greeting script")
    result = ct.CodeGeneratorTool().execute(output_filename=filename, **kwargs)
    return result, tmp_path / "outputs" / "code" / filename


FILE = "print('hi')"


@pytest.mark.parametrize("reply", [
    FILE,
    f"```python\n{FILE}\n```",
    f"Here is the complete file:\n\n```python\n{FILE}\n```",
    f"```python\n{FILE}\n```\n\nThis version adds a greeting.",
    f"Sure! Here's the file:\n```py\n{FILE}\n```\nIt prints a greeting.",
    f"``` Python3 \n{FILE}\n```",
    f"~~~python\n{FILE}\n~~~",
    f"```python\n{FILE}",
    f"Here is the file:\n```python\n{FILE}\n```\nRun it:\n```bash\npython out.py\n```",
])
def test_only_the_code_is_saved(llm, tmp_path, reply):
    llm(StubLLM(reply))
    result, written = _generate(tmp_path)
    assert result.success, result.error
    assert written.read_text() == FILE
    assert result.output["syntax_ok"] is True


@pytest.mark.parametrize("reply", ["```python\n```", "Here you go:\n```\n```", "No code, sorry\n```\n\n```", "  \n"])
def test_a_reply_with_no_code_is_an_error_and_writes_nothing(llm, tmp_path, reply):
    llm(StubLLM(reply))
    result, written = _generate(tmp_path)
    assert not result.success
    assert "no code" in result.error
    assert not written.exists()


def test_fences_that_belong_to_the_file_are_kept(llm, tmp_path):
    python_with_example = 'def f():\n    return 1\n\n\nDOC = """\n```python\nf()\n```\n"""'
    llm(StubLLM(python_with_example))
    result, written = _generate(tmp_path)
    assert result.success and written.read_text() == python_with_example

    nested = 'DOC = """\n```bash\nrun me\n```\n"""\nprint(DOC)'
    llm(StubLLM(f"```python\n{nested}\n```"))
    result, written = _generate(tmp_path)
    assert result.success and written.read_text() == nested

    script = "#!/bin/bash\ncat > README.md <<'EOF'\n# Title\n```bash\nnpm i\n```\nEOF\necho done"
    llm(StubLLM(script))
    result, written = _generate(tmp_path, filename="mk.sh")
    assert result.success and written.read_text() == script
    assert result.output["syntax_ok"] is None


def test_markdown_output_keeps_its_code_samples(llm, tmp_path):
    readme = "# App\n\nInstall:\n```bash\npip install app\n```"
    llm(StubLLM(readme))
    result, written = _generate(tmp_path, filename="README.md")
    assert result.success and written.read_text() == readme

    llm(StubLLM(f"```markdown\n{readme}\n```"))
    result, written = _generate(tmp_path, filename="README.md")
    assert result.success and written.read_text() == readme


def test_output_that_does_not_parse_is_flagged(llm, tmp_path):
    llm(StubLLM("def broken(:\n    pass"))
    result, written = _generate(tmp_path)
    assert result.success and written.exists()
    assert result.output["syntax_ok"] is False

    llm(StubLLM('Here is the JSON:\n```json\n{"a": 1}\n```'))
    result, written = _generate(tmp_path, filename="cfg.json")
    assert written.read_text() == '{"a": 1}'
    assert result.output["syntax_ok"] is True


# --- input size ---------------------------------------------------------

def test_an_input_too_long_for_the_context_window_is_refused_before_the_model_runs(llm, tmp_path):
    (tmp_path / "uploads" / "big.py").write_text("x = 1\n" * 40_000)
    stub = llm(StubLLM("x = 2", context_window=8192))
    result, written = _generate(tmp_path, filename="big_v2.py", input_file="big.py", instructions="rename x to y")
    assert not result.success
    assert "too long to rewrite" in result.error and "8,192-token" in result.error
    assert stub.prompts == [] and not written.exists()


def test_an_input_that_fits_is_sent_whole(llm, tmp_path):
    source = "x = 1\n" * 100
    (tmp_path / "uploads" / "small.py").write_text(source)
    stub = llm(StubLLM("y = 1", context_window=8192))
    result, written = _generate(tmp_path, filename="small_v2.py", input_file="small.py", instructions="rename x to y")
    assert result.success, result.error
    assert source in stub.prompts[0]
    assert written.read_text() == "y = 1"


def test_a_client_that_names_no_context_window_is_not_second_guessed(llm, tmp_path):
    (tmp_path / "uploads" / "big.py").write_text("x = 1\n" * 40_000)
    stub = llm(StubLLM("y = 1", context_window=0))
    result, _written = _generate(tmp_path, filename="big_v2.py", input_file="big.py", instructions="rename x to y")
    assert result.success and len(stub.prompts) == 1


def test_an_upload_over_the_byte_cap_is_not_read(llm, tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "MAX_INPUT_BYTES", 1000)
    (tmp_path / "uploads" / "huge.py").write_text("x = 1\n" * 500)
    content, error, found = ct._read_code_input(ct.CodeGeneratorTool(), "huge.py")
    assert content is None and found is None
    assert "larger than" in error


# --- requests aimed at an existing file ---------------------------------

EXISTING = {"README.md", "start.sh", "docker-compose.yml", "backend/app.py", "quality_gate.py"}


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setattr(ct.CodeGeneratorTool, "_resolves", lambda self, name: name in EXISTING)
    return ct.CodeGeneratorTool()


@pytest.mark.parametrize("output,instructions", [
    ("README.md", "Generate a README.md for a to-do list web app"),
    ("start.sh", "a bash script that starts my node server"),
    ("app/config.yml", "config similar in spirit to docker-compose.yml but for my app"),
    ("helper.py", "Write a brand new helper.py with a greet function"),
    ("backend/app.py", "a small Flask app with one health route"),
])
def test_a_new_file_that_shares_an_existing_name_is_allowed(guard, output, instructions):
    assert guard._referenced_existing_file(instructions, output) is None


@pytest.mark.parametrize("output,instructions,expected", [
    ("README.md", "Improve the README", "README.md"),
    ("app_v2.py", "Refactor backend/app.py for readability", "backend/app.py"),
    ("backend/app.py", "refactor it", "backend/app.py"),
    ("gate_v2.py", "Improve the uploaded quality_gate.py with better structure", "quality_gate.py"),
    ("out.py", "Improve this file so it runs faster", "out.py"),
])
def test_a_request_to_change_an_existing_file_needs_input_file(guard, output, instructions, expected):
    assert guard._referenced_existing_file(instructions, output) == expected


def test_the_refusal_offers_both_ways_out_and_runs_no_model(llm, guard, tmp_path):
    stub = llm(StubLLM("x = 1"))
    result = guard.execute(output_filename="app_v2.py", instructions="Refactor backend/app.py for readability")
    assert not result.success
    assert "input_file='backend/app.py'" in result.error
    assert "new file" in result.error
    assert stub.prompts == []


def test_a_readme_for_a_new_project_is_written(llm, guard, tmp_path):
    llm(StubLLM("# Todo\n"))
    result = guard.execute(output_filename="README.md", instructions="Generate a README.md for a to-do list web app")
    assert result.success, result.error
    assert (tmp_path / "outputs" / "code" / "README.md").read_text() == "# Todo"
