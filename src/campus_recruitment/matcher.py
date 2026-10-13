"""Configurable deterministic role matching with explicit evidence and exclusions."""
import json
import re
from pathlib import Path


class JobMatcher:
    def __init__(self, config):
        self.config = config
        self.rules = [(rule, re.compile(rule["title_pattern"], re.I), re.compile(rule["requirements_pattern"], re.I))
                      for rule in config["rules"]]
        self.exclusions = [re.compile(pattern, re.I) for pattern in config["exclude_titles"]]
        self.software_test = re.compile(config["software_test_pattern"], re.I)
        self.technical_role = re.compile(config["technical_role_pattern"], re.I)

    @classmethod
    def from_file(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def match(self, position):
        title = position.position_name or ""
        requirements = position.requirements or ""
        reasons = []
        if any(pattern.search(title) for pattern in self.exclusions):
            score, priority, reasons = 0, "excluded", ["岗位名称命中销售/非研发排除规则"]
        elif self.software_test.search(title):
            score, priority, reasons = 0, "low", ["软件测试不能自动等同硬件测试"]
        else:
            candidates = []
            high_requirement_signals = []
            for rule, title_pattern, req_pattern in self.rules:
                name_hit = bool(title_pattern.search(title))
                req_hit = bool(req_pattern.search(requirements)) and bool(self.technical_role.search(title))
                if name_hit:
                    points = self.config["weights"][rule["priority"] + "_title"]
                    if req_hit:
                        points += self.config["weights"]["requirements"]
                    candidates.append(points)
                    reasons.append(f"{rule['label']}：岗位名称匹配" + ("，要求支持" if req_hit else ""))
                elif req_hit and rule["priority"] == "high":
                    high_requirement_signals.append(rule["label"])
            if high_requirement_signals:
                evidence_points = min(100, len(high_requirement_signals) * self.config["weights"]["requirements_signal"])
                candidates.append(evidence_points)
                reasons.append("技术岗位要求匹配：" + "、".join(high_requirement_signals))
            score = min(100, max(candidates, default=0))
            priority = "high" if score >= self.config["thresholds"]["high"] else ("medium" if score >= self.config["thresholds"]["medium"] else "low")
            if not reasons:
                reasons = ["未找到足够相关的岗位名称/要求证据"]
        position.matching_score = score
        position.matching_priority = priority
        position.matching_reasons = reasons
        return position
