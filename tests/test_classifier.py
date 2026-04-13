# tests/test_classifier.py
import pytest
from granite.enrichers.classifier import Classifier


@pytest.fixture
def classifier():
    config = {
        "scoring": {
            "weights": {
                "has_website": 10,
                "has_telegram": 15,
                "has_whatsapp": 10,
                "multiple_phones": 5,
                "has_email": 5,
                "cms_bitrix": 15,
                "cms_modern": 10,
                "has_marquiz": 5,
                "tg_trust_multiplier": 5,
                "is_network": 15
            },
            "levels": {
                "segment_A": 60,
                "segment_B": 40,
                "segment_C": 20
            }
        }
    }
    return Classifier(config)


def test_classifier_empty_company(classifier):
    company = {}
    score = classifier.calculate_score(company)
    segment = classifier.determine_segment(score)
    
    assert score == 0
    assert segment == "D"


def test_classifier_max_score(classifier):
    company = {
        "website": "http://granit-master.ru",
        "cms": "bitrix",
        "has_marquiz": True,
        "messengers": {"telegram": "t.me/granit", "whatsapp": "wa.me/79031234567"},
        "tg_trust": {"trust_score": 3},  # 3 * 5 = +15
        "phones": ["79031234567", "79032222222"],
        "emails": ["info@granit.ru"],
        "is_network": True
    }
    
    score = classifier.calculate_score(company)
    # 10(web) + 15(bitrix) + 5(marquiz) + 15(tg) + 15(tg_trust) + 10(wa) + 5(2_phones) + 5(email) + 15(network) = 95
    assert score == 95
    segment = classifier.determine_segment(score)
    assert segment == "A"


def test_classifier_segment_B(classifier):
    company = {
        "website": "http://granit-master.ru",
        "messengers": {"whatsapp": "wa.me/79031234567"},
        "phones": ["79031234567", "79032222222"]
    }
    
    score = classifier.calculate_score(company)
    # 10(web) + 10(wa) + 5(2_phones) = 25
    assert score == 25
    segment = classifier.determine_segment(score)
    assert segment == "C" # 25 is >= 20 -> C


def test_classifier_flexbe_gets_modern_cms_bonus(classifier):
    """Flexbe is classified as a modern CMS (same as WordPress/Tilda)."""
    company = {"website": "http://site.ru", "cms": "flexbe"}
    score = classifier.calculate_score(company)
    # 10 (has_website) + 10 (cms_modern) = 20
    assert score == 20
