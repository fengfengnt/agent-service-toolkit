import re

from xiaohe.contracts import ServiceError

ROUNDS = {
    "buyer": (
        ("companyName", "industry", "companyScale", "city"),
        ("category", "requirement", "annualPurchase", "painPoints", "contactName", "contactRole"),
        ("existingSystems", "projectStatus", "budget", "desiredTime", "decisionProcess"),
    ),
    "organization": (
        (
            "organizationName",
            "organizationType",
            "memberSize",
            "coverage",
            "city",
            "onlineChannels",
        ),
        ("industries", "services", "aiNeeds", "contactName", "contactRole", "email"),
        ("activeRatio", "revenueStructure", "desiredLaunchTime", "decisionProcess", "resources"),
    ),
    "supplier": (
        ("companyName", "companyType", "category", "city"),
        (
            "productDescription",
            "capabilityTags",
            "qualifications",
            "serviceAreas",
            "orderTypes",
            "contactName",
        ),
        ("capacity", "deliveryCycle", "revenue", "teamSize", "targetCustomers", "targetAreas"),
    ),
}
REQUIRED = {
    "buyer": ("companyName",),
    "supplier": ("companyName",),
    "organization": ("organizationName", "memberSize", "coverage", "onlineChannels", "contactName"),
}
ORG_ENUMS = {
    "memberSize": ("100 家以下", "100—500", "500—2000", "2000 以上"),
    "coverage": ("本市", "本省", "全国", "含海外"),
    "contactRole": ("会长", "副会长", "秘书长", "运营负责人", "专职工作人员", "其他"),
}
CHANNELS = ("公众号", "小程序", "APP", "官网", "微信群", "暂无")


def validate_fields(direction: str, fields: dict) -> dict:
    allowed = {key for group in ROUNDS.get(direction, ()) for key in group}
    if not allowed or not isinstance(fields, dict) or fields.keys() - allowed:
        raise ServiceError("INPUT_INVALID", 422)
    result = {}
    for key, value in fields.items():
        if value is None:
            result[key] = None
            continue
        valid = isinstance(value, str) and len(value) <= 4000
        if isinstance(value, list):
            valid = len(value) <= 32 and all(
                isinstance(item, str) and len(item) <= 256 for item in value
            )
        if key == "companyName":
            valid = isinstance(value, str) and bool(value.strip()) and len(value) <= 200
        if direction == "organization":
            if key in ORG_ENUMS:
                valid = isinstance(value, str) and value in ORG_ENUMS[key]
            elif key == "organizationName":
                valid = isinstance(value, str) and bool(value.strip()) and 2 <= len(value) <= 40
            elif key == "contactName":
                valid = isinstance(value, str) and bool(value.strip()) and len(value) <= 100
            elif key == "email":
                valid = (
                    isinstance(value, str)
                    and len(value) <= 254
                    and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))
                )
            elif key == "onlineChannels":
                valid = (
                    isinstance(value, list)
                    and bool(value)
                    and all(isinstance(item, str) and item in CHANNELS for item in value)
                )
                valid = (
                    valid
                    and len(value) == len(set(value))
                    and ("暂无" not in value or value == ["暂无"])
                )
        if not valid:
            raise ServiceError("INPUT_INVALID", 422)
        result[key] = (
            value.strip()
            if key in ("companyName", "organizationName", "contactName") and isinstance(value, str)
            else value
        )
    return result


def missing_required(direction: str, fields: dict) -> list[str]:
    return [key for key in REQUIRED[direction] if not fields.get(key)]
