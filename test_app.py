import json
import os
import urllib.error

os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ.pop("LLM_PROVIDER", None)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["SERVICE_TOKEN"] = "svc"
os.environ["BUYER_KEY"] = "buyer"

import anthropic
from fastapi.testclient import TestClient

import qualify
from app import app, to_e164

client = TestClient(app)
SVC = {"X-Service-Token": "svc"}
LEAD = {"contact_id": "c1", "first_name": "Maria", "last_name": "Test", "email": " Maria@Example.com ",
        "phone": "(512) 555-0101", "postal_code": "78704", "monthly_bill": "220",
        "homeowner": "Yes", "roof_shade": "None", "timeline": "ASAP", "consent": "yes"}


def fail_claude(lead):
    raise anthropic.APIConnectionError(request=None)


def test_phone_to_e164():
    assert to_e164("(512) 555-0101") == "+15125550101"
    assert to_e164("+63 917 000 0000") == "+639170000000"


def test_tier_thresholds():
    assert [qualify.tier_for(s) for s in (75, 74, 45, 44)] == ["hot", "warm", "warm", "cold"]


def deepseek_reply(content, finish_reason="stop"):
    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]}).encode()

    return lambda request, timeout: Reply()


def test_deepseek_is_the_default_scorer(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(qualify.urllib.request, "urlopen", deepseek_reply('{"score": 88, "reasons": ["Owns the home"], "summary": "Strong fit."}'))
    body = client.post("/qualify", json=LEAD, headers=SVC).json()
    assert body["qualification"] == {"score": 88, "tier": "hot", "reasons": ["Owns the home"], "summary": "Strong fit.", "source": "deepseek"}


def test_deepseek_down_or_out_of_range_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(qualify.urllib.request, "urlopen", deepseek_reply('{"score": 140, "reasons": ["x"], "summary": "y"}'))
    assert client.post("/qualify", json=LEAD, headers=SVC).json()["qualification"]["source"] == "rules"

    def down(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(qualify.urllib.request, "urlopen", down)
    assert client.post("/qualify", json=LEAD, headers=SVC).json()["qualification"]["source"] == "rules"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert client.post("/qualify", json=LEAD, headers=SVC).json()["qualification"]["source"] == "rules"


def test_qualify_falls_back_to_rules_when_claude_is_down(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setattr(qualify, "qualify_with_claude", fail_claude)
    body = client.post("/qualify", json=LEAD, headers=SVC).json()
    assert body["lead"]["email"] == "maria@example.com"
    assert body["lead"]["homeowner"] is True and body["lead"]["consent"] is True
    assert body["qualification"]["source"] == "rules"
    assert body["qualification"]["tier"] == "hot"


def test_qualify_falls_back_to_rules_without_an_anthropic_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    body = client.post("/qualify", json=LEAD, headers=SVC).json()
    assert body["qualification"]["source"] == "rules"
    assert "RuntimeError" in body["qualification"]["reasons"][-1]


def test_claude_score_out_of_range_is_rejected(monkeypatch):
    class Block:
        type = "text"
        text = '{"score": 140, "reasons": ["x"], "summary": "y"}'

    class Response:
        stop_reason = "end_turn"
        content = [Block()]

    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setattr(qualify.client.beta.messages, "create", lambda **kwargs: Response())
    result = qualify.qualify({**LEAD, "homeowner": True, "monthly_bill": 220.0})
    assert result.source == "rules"


def test_qualify_needs_service_token():
    assert client.post("/qualify", json=LEAD).status_code == 401


def test_map_installer_a_and_missing_fields():
    lead = {"first_name": "Maria", "last_name": "Test", "phone_e164": "+15125550101", "email": "m@x.com",
            "postal_code": "78704", "homeowner": True, "monthly_bill": 219.6, "lead_key": "abc"}
    ok = client.post("/map/installer-a", json={"lead": lead, "qualification": {"score": 86, "summary": "s"}}, headers=SVC)
    assert ok.status_code == 200 and ok.json()["avg_bill_usd"] == 220
    bad = client.post("/map/installer-a", json={"lead": {**lead, "phone_e164": ""}, "qualification": {"score": 1, "summary": ""}}, headers=SVC)
    assert bad.status_code == 422


def test_mock_buyer_outage_and_auth():
    headers = {"Authorization": "Bearer buyer", "Idempotency-Key": "lead-t1"}
    assert client.post("/mock-buyer/installer-a/leads", json={}, headers=headers).status_code == 201
    assert client.post("/mock-buyer/installer-a/leads", json={}, headers={**headers, "X-Demo-Outage": "1"}).status_code == 503
    assert client.post("/mock-buyer/installer-a/leads", json={}).status_code == 401
