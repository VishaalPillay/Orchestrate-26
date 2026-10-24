"""RuleIntakeAgent: request texts copied verbatim from deletion_requests.csv / sample_deletion_requests.csv."""

from __future__ import annotations

import pytest

from pcda.agents.intake import KIND_AMBIGUOUS, KIND_DELETION, RuleIntakeAgent

NON_DELETION = [
    ("The mobile number on my profile is out of date. Please replace it with the new one and leave the account active.", "PHONE_CHANGE"),
    ("I'd like to download my account information in a portable format. Please keep the account as it is.", "EXPORT"),
    ("I can't access my profile and need help resetting the login. Do not close the account.", "LOGIN_HELP"),
    ("My surname is misspelled on the account. Can you correct it without removing any of my records?", "CORRECTION"),
    ("Please remove me from marketing lists only. I still want receipts and service messages for this account.", "MARKETING_OPT_OUT"),
    ("I need a copy of last year's invoices for my records. Please leave my profile and purchase history in place.", "ACCESS"),
    ("Please change the email address used for this account. I want to continue using the service.", "EMAIL_CHANGE"),
    ("Could you send me a copy of the information held on my account? I still use it, so please don't delete or change anything.", "ACCESS"),
    ("Please stop the promotional emails. I still need the account and my order history, so I am not asking for deletion.", "MARKETING_OPT_OUT"),
    ("Can you tell me what personal information you hold about me? This is an information request, not a request to erase it.", "ACCESS"),
]

DELETION = [
    "Je demande la suppression définitive du compte associé à cette adresse e-mail. Veuillez examiner les identifiants actuels et antérieurs conformément à votre procédure de confidentialité. Merci de confirmer le résultat une fois la vérification terminée.",
    "Solicito a exclusão permanente da conta associada a este e-mail. Verifiquem os identificadores atuais e anteriores conforme o processo de privacidade. Por favor, confirmem o resultado quando a análise terminar.",
    "このメールアドレスに関連するアカウントの完全な削除を依頼します。 プライバシー手続きに従い、現在および過去の識別情報を確認してください。 確認が完了したら結果をお知らせください。",
    "이 이메일 주소에 연결된 계정의 영구 삭제를 요청합니다. 개인정보 처리 절차에 따라 현재 및 이전 식별자를 검토해 주세요. 검토가 완료되면 결과를 확인해 주세요.",
    "I closed my account, but an old “download your data” link still works. Please delete that export file as well.",
    "I’m deleting my account. You can keep anonymous statistics, but nothing in analytics should still point back to me.",
    "Can you verify that my account was deleted? A search result with a similar email belongs to someone else, so please do not treat that record as mine.",
]


@pytest.mark.parametrize(("text", "kind"), NON_DELETION)
def test_non_deletion(text: str, kind: str) -> None:
    result = RuleIntakeAgent().classify("c", text)
    assert result.authorizes_deletion is False
    assert result.request_kind == kind


@pytest.mark.parametrize("text", DELETION)
def test_deletion(text: str) -> None:
    result = RuleIntakeAgent().classify("c", text)
    assert result.authorizes_deletion is True
    assert result.request_kind == KIND_DELETION


def test_ambiguous_defaults_to_deletion_and_is_flagged() -> None:
    text = (
        "I found an old report saying my account still existed. Please check the latest state and confirm "
        "that the profile is gone and that remaining records cannot identify me."
    )
    result = RuleIntakeAgent().classify("case_26", text)
    assert result.authorizes_deletion is True
    assert result.request_kind == KIND_AMBIGUOUS
    assert result.is_ambiguous


def test_language() -> None:
    agent = RuleIntakeAgent()
    assert agent.classify("c", DELETION[0]).language == "fr"
    assert agent.classify("c", DELETION[1]).language == "pt"
    assert agent.classify("c", DELETION[2]).language == "ja"
    assert agent.classify("c", DELETION[3]).language == "ko"
