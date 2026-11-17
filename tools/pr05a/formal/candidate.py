"""Reviewable overlays. Frozen migration and public OpenAPI files are read-only inputs."""

import copy
from pathlib import Path


def candidate_sql(root):
    source = (Path(root) / "web/backend/migrations/versions/0007_authentication.sql").read_text(encoding="utf-8")
    start = source.index("CREATE FUNCTION identity_api.issue_reauth(p jsonb)")
    stop = source.index("END $f$;", start) + len("END $f$;")
    function = source[start:stop].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    marker = "  ELSE RETURN jsonb_build_object('error','SERVICE_DISABLED');END IF;"
    branch = """  ELSIF purpose IN('unlockCache','recoverCache') THEN
    SELECT make_interval(secs=>least(reauth_window_seconds,CASE WHEN s->>'role'='admin' THEN 300 ELSE 600 END))
      INTO risk_window FROM identity_control.cache_policy WHERE singleton AND enabled
      AND (purpose<>'recoverCache' OR history_enabled) FOR SHARE;
    IF risk_window IS NULL OR p->>'target_kind'<>'device' OR (p->>'target_id')::uuid<>(s->>'device_id')::uuid
      THEN RETURN jsonb_build_object('error','SERVICE_DISABLED');END IF;
"""
    if function.count(marker) != 1:
        raise ValueError("reviewed issue_reauth insertion point changed")
    function = function.replace(marker, branch + marker)
    return Path(__file__).with_name("cache_candidate.sql").read_text(encoding="utf-8") + "\n" + function


def candidate_contract(contract):
    result = copy.deepcopy(contract)
    result["x-pr05a-candidate"] = True
    schemas = result["components"]["schemas"]
    recovery = copy.deepcopy(schemas["CacheUnlockRequest"])
    recovery["properties"]["key_id"] = {"type": "string", "format": "uuid"}
    recovery["required"].append("key_id")
    schemas["CacheRecoverRequest"] = recovery
    recovered = copy.deepcopy(schemas["CacheUnlockResult"])
    recovered["properties"]["key_version"] = {"type": "integer", "minimum": 1}
    recovered["required"].append("key_version")
    schemas["CacheRecoverResult"] = recovered
    unlock = result["paths"]["/me/cache-unlock"]["post"]
    unlock["x-requires-reauth"] = True
    unlock.setdefault("x-reauth-policy", {"purpose": "unlockCache", "state": "candidate only", "windows": "explicit gated DB policy"})
    recover = copy.deepcopy(unlock)
    recover["operationId"] = "recoverCache"
    recover["summary"] = "Candidate same-active-device historical cache key recovery"
    recover["requestBody"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/CacheRecoverRequest"}
    recover["responses"]["200"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/CacheRecoverResult"}
    recover["x-reauth-policy"]["purpose"] = "recoverCache"
    result["paths"]["/me/cache-recover"] = {"post": recover}
    for purpose, body in (("unlockCache", "CacheUnlockRequest"), ("recoverCache", "CacheRecoverRequest")):
        operation = copy.deepcopy(schemas["ReauthOperation"]["oneOf"][0])
        operation["properties"]["purpose"] = {"const": purpose}
        operation["properties"]["target"]["properties"]["kind"] = {"const": "device"}
        operation["properties"]["candidate_parameters"]["properties"]["body"] = {"$ref": f"#/components/schemas/{body}"}
        existing = [item for item in schemas["ReauthOperation"]["oneOf"]
                    if item.get("properties", {}).get("purpose", {}).get("const") == purpose]
        if existing:
            if len(existing) != 1 or existing[0] != operation:
                raise ValueError(f"incompatible existing {purpose} contract")
        else:
            schemas["ReauthOperation"]["oneOf"].append(operation)
    return result
