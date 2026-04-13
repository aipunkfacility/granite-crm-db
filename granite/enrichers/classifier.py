# enrichers/classifier.py

class Classifier:
    """Оценка (скоринг) компаний по конфигурации config.yaml.
    Распределяет по сегментам (A, B, C, D).
    """

    def __init__(self, config: dict):
        self.config = config
        self.rules = config.get("scoring", {})
        self.weights = self.rules.get("weights", {})
        self.thresholds = self.rules.get("levels", {})

    def calculate_score(self, company: dict) -> int:
        """Подсчет CRM Score на основе обогащенных данных."""
        score = 0
        
        # Сайт
        if company.get("website"):
            score += self.weights.get("has_website", 0)
            
            cms = company.get("cms", "unknown")
            if cms == "bitrix":
                score += self.weights.get("cms_bitrix", 0)
            elif cms in ["wordpress", "tilda", "flexbe"]:
                score += self.weights.get("cms_modern", 0)
                
            if company.get("has_marquiz"):
                score += self.weights.get("has_marquiz", 0)
                
        # Мессенджеры
        messengers = company.get("messengers", {})
        if messengers.get("telegram"):
            score += self.weights.get("has_telegram", 0)
            
            tg_trust = company.get("tg_trust") or {}
            try:
                score += int(tg_trust.get("trust_score", 0) * self.weights.get("tg_trust_multiplier", 0))
            except (TypeError, ValueError):
                pass
            
        if messengers.get("whatsapp"):
            score += self.weights.get("has_whatsapp", 0)
            
        # Несколько телефонов
        phones = company.get("phones", [])
        if len(phones) > 1:
            score += self.weights.get("multiple_phones", 0)
            
        # Наличие Email
        emails = company.get("emails", [])
        if len(emails) > 0:
            score += self.weights.get("has_email", 0)
            
        # Сеть филиалов
        if company.get("is_network"):
            score += self.weights.get("is_network", 0)

        return score

    def determine_segment(self, score: int) -> str:
        """Определение сегмента на основе Score."""
        if score >= self.thresholds.get("segment_A", 50):
            return "A"
        elif score >= self.thresholds.get("segment_B", 30):
            return "B"
        elif score >= self.thresholds.get("segment_C", 15):
            return "C"
        else:
            return "D"
