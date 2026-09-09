import pytest

from headofcontext.integrations import AgentSession

crewai = pytest.importorskip("crewai")
from crewai.tools import BaseTool  # noqa: E402

from headofcontext.integrations.crewai import guard_tools  # noqa: E402


class MailTool(BaseTool):
    name: str = "mail.send"
    description: str = "Send an email."

    def _run(self, to: str, body: str) -> str:
        return f"sent to {to}"


class FinanceTool(BaseTool):
    name: str = "finance.report"
    description: str = "Produce the finance report."

    def _run(self, quarter: str) -> str:
        return f"report {quarter}"


def test_guarded_crewai_tools(session: AgentSession) -> None:
    mail, finance = guard_tools([MailTool(), FinanceTool()], session)
    assert mail.name == "mail.send" and mail.args_schema is not None
    assert mail.run(to="bob@acme.example", body="hi") == "sent to bob@acme.example"
    out = finance.run(quarter="Q3")
    assert out.startswith("HeadOfContext denied") and "report Q3" not in out


def test_crewai_raise_mode(session: AgentSession) -> None:
    from headofcontext.core.errors import ActionDenied

    [finance] = guard_tools([FinanceTool()], session, on_deny="raise")
    with pytest.raises(ActionDenied):
        finance.run(quarter="Q3")


def test_rejects_non_tools(session: AgentSession) -> None:
    with pytest.raises(TypeError):
        guard_tools([object()], session)
