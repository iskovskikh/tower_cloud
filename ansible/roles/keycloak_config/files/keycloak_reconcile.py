#!/usr/bin/env python3
"""Reconcile a Keycloak realm via the Admin REST API (idempotent).

Configured entirely through environment variables (set by the Ansible role):
  KC_URL, KC_ADMIN_REALM, KC_REALM, KC_ADMIN_USER, KC_ADMIN_PASS,
  KC_INSECURE ("true"/"false"),
  KC_REALM_ROLES   (JSON list of {name, description}),
  KC_CLIENT_SCOPES (JSON list of client-scope representations incl. protocolMappers),
  KC_CLIENTS       (JSON list of client representations, incl. defaultClientScopes),
  KC_USERS         (JSON list of user dicts).

Applied in order: realm roles -> client scopes -> clients -> users, because
clients/users reference roles and scopes by name.

Existing users are never modified (passwords are not reset); missing users are
created with a temporary password, and realm-role / group membership is ensured.
Realm roles are created if missing. Client scopes and clients are upserted
(only the keys listed in MANAGED_CLIENT_KEYS are compared and updated).
Prints CHANGED or OK for Ansible's changed_when.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, NoReturn

KC_URL = os.environ["KC_URL"].rstrip("/")
ADMIN_REALM = os.environ.get("KC_ADMIN_REALM", "master")
REALM = os.environ["KC_REALM"]
ADMIN_USER = os.environ["KC_ADMIN_USER"]
ADMIN_PASS = os.environ["KC_ADMIN_PASS"]
INSECURE = os.environ.get("KC_INSECURE", "true").lower() == "true"
REALM_ROLES = json.loads(os.environ.get("KC_REALM_ROLES", "[]"))
CLIENT_SCOPES = json.loads(os.environ.get("KC_CLIENT_SCOPES", "[]"))
CLIENTS = json.loads(os.environ.get("KC_CLIENTS", "[]"))
USERS = json.loads(os.environ.get("KC_USERS", "[]"))

# Only these client fields are compared/updated, so unrelated Keycloak-managed
# fields (secrets, internal ids, ...) are left untouched.
MANAGED_CLIENT_KEYS = (
    "name",
    "enabled",
    "protocol",
    "publicClient",
    "standardFlowEnabled",
    "directAccessGrantsEnabled",
    "rootUrl",
    "adminUrl",
    "redirectUris",
    "webOrigins",
    "attributes",
    "defaultClientScopes",
    "optionalClientScopes",
)

CTX = ssl.create_default_context()
if INSECURE:
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE


def call(method, path, token=None, body=None, form=None) -> "tuple[int, Any]":
    headers = {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(KC_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = raw
        return err.code, parsed


def fatal(msg) -> "NoReturn":
    print(f"ERROR: {msg}")
    sys.exit(1)


def canon(value):
    if isinstance(value, list):
        return sorted((json.dumps(canon(v), sort_keys=True) for v in value))
    if isinstance(value, dict):
        return {k: canon(v) for k, v in value.items()}
    return value


def matches(desired, current):
    """True if `current` already satisfies `desired`.

    Dicts are compared as a subset: extra keys Keycloak adds on its own (secrets,
    timestamps, default attributes) are ignored, so reconciliation stays
    idempotent. Lists and scalars must match exactly.
    """
    if isinstance(desired, dict):
        if not isinstance(current, dict):
            return False
        return all(k in current and matches(v, current[k]) for k, v in desired.items())
    if isinstance(desired, list):
        if not isinstance(current, list):
            return False
        return canon(desired) == canon(current)
    return desired == current


status, tok = call(
    "POST",
    f"/realms/{ADMIN_REALM}/protocol/openid-connect/token",
    form={
        "grant_type": "password",
        "client_id": "admin-cli",
        "username": ADMIN_USER,
        "password": ADMIN_PASS,
    },
)
if status != 200:
    fatal(f"admin token request failed ({status}): {tok}")
token = tok["access_token"]

changed = False


def find_group(path):
    status, groups = call("GET", f"/admin/realms/{REALM}/groups", token)
    if status != 200:
        fatal(f"list groups failed ({status}): {groups}")
    stack = list(groups or [])
    while stack:
        group = stack.pop()
        if group.get("path") == path:
            return group["id"]
        stack.extend(group.get("subGroups") or [])
    return None


def find_realm_role(name):
    status, role = call("GET", f"/admin/realms/{REALM}/roles/{urllib.parse.quote(name)}", token)
    return role if status == 200 else None


def list_client_scopes():
    status, scopes = call("GET", f"/admin/realms/{REALM}/client-scopes", token)
    if status != 200:
        fatal(f"list client scopes failed ({status}): {scopes}")
    return scopes or []


# --- realm roles -----------------------------------------------------------
for role in REALM_ROLES:
    name = role["name"]
    status, existing = call("GET", f"/admin/realms/{REALM}/roles/{urllib.parse.quote(name)}", token)
    if status == 200:
        continue
    if status != 404:
        fatal(f"lookup realm role {name} failed ({status}): {existing}")
    status, resp = call(
        "POST",
        f"/admin/realms/{REALM}/roles",
        token,
        body={"name": name, "description": role.get("description", "")},
    )
    if status not in (201, 204):
        fatal(f"create realm role {name} failed ({status}): {resp}")
    changed = True

# --- client scopes (+ their protocol mappers) ------------------------------
for spec in CLIENT_SCOPES:
    name = spec["name"]
    existing = next((s for s in list_client_scopes() if s["name"] == name), None)
    body = {
        "name": name,
        "description": spec.get("description", ""),
        "protocol": spec.get("protocol", "openid-connect"),
        "attributes": spec.get("attributes", {}),
    }
    if existing is None:
        status, resp = call("POST", f"/admin/realms/{REALM}/client-scopes", token, body=body)
        if status not in (201, 204):
            fatal(f"create client scope {name} failed ({status}): {resp}")
        changed = True
        existing = next((s for s in list_client_scopes() if s["name"] == name), None)
        if existing is None:
            fatal(f"client scope {name} not found after create")
    elif not (
        matches(body["description"], existing.get("description", ""))
        and matches(body["attributes"], existing.get("attributes", {}))
        and existing.get("protocol") == body["protocol"]
    ):
        status, resp = call(
            "PUT",
            f"/admin/realms/{REALM}/client-scopes/{existing['id']}",
            token,
            body={**existing, **body},
        )
        if status not in (204,):
            fatal(f"update client scope {name} failed ({status}): {resp}")
        changed = True

    sid = existing["id"]
    status, mappers = call(
        "GET", f"/admin/realms/{REALM}/client-scopes/{sid}/protocol-mappers/models", token
    )
    if status != 200:
        fatal(f"list protocol mappers of {name} failed ({status}): {mappers}")
    by_name = {m["name"]: m for m in (mappers or [])}
    for mapper in spec.get("protocolMappers", []):
        mname = mapper["name"]
        desired = {
            "name": mname,
            "protocol": mapper.get("protocol", "openid-connect"),
            "protocolMapper": mapper["protocolMapper"],
            "consentRequired": mapper.get("consentRequired", False),
            "config": mapper.get("config", {}),
        }
        current = by_name.get(mname)
        if current is None:
            status, resp = call(
                "POST",
                f"/admin/realms/{REALM}/client-scopes/{sid}/protocol-mappers/models",
                token,
                body=desired,
            )
            if status not in (201, 204):
                fatal(f"create mapper {mname} on {name} failed ({status}): {resp}")
            changed = True
        elif not (
            matches(desired["config"], current.get("config", {}))
            and current.get("protocolMapper") == desired["protocolMapper"]
        ):
            status, resp = call(
                "PUT",
                f"/admin/realms/{REALM}/client-scopes/{sid}/protocol-mappers/models/{current['id']}",
                token,
                body={**current, **desired},
            )
            if status not in (204,):
                fatal(f"update mapper {mname} on {name} failed ({status}): {resp}")
            changed = True

# --- clients ---------------------------------------------------------------
for spec in CLIENTS:
    client_id = spec["clientId"]
    status, found = call(
        "GET", f"/admin/realms/{REALM}/clients?clientId={urllib.parse.quote(client_id)}", token
    )
    if status != 200:
        fatal(f"lookup client {client_id} failed ({status}): {found}")
    if not found:
        status, resp = call("POST", f"/admin/realms/{REALM}/clients", token, body=spec)
        if status not in (201, 204):
            fatal(f"create client {client_id} failed ({status}): {resp}")
        changed = True
    else:
        current = found[0]
        drift = any(
            not matches(spec[key], current.get(key))
            for key in MANAGED_CLIENT_KEYS
            if key in spec
        )
        if drift:
            status, resp = call(
                "PUT",
                f"/admin/realms/{REALM}/clients/{current['id']}",
                token,
                body={**current, **spec},
            )
            if status not in (204,):
                fatal(f"update client {client_id} failed ({status}): {resp}")
            changed = True

# --- users -----------------------------------------------------------------
for user in USERS:
    username = user["username"]
    status, found = call(
        "GET",
        f"/admin/realms/{REALM}/users?username={urllib.parse.quote(username)}&exact=true",
        token,
    )
    if status != 200:
        fatal(f"lookup {username} failed ({status}): {found}")

    created = False
    if not found:
        payload = {"username": username, "enabled": user.get("enabled", True)}
        if user.get("email"):
            payload["email"] = user["email"]
        first_name = user.get("first_name") or user.get("firstName")
        last_name = user.get("last_name") or user.get("lastName")
        if first_name:
            payload["firstName"] = first_name
        if last_name:
            payload["lastName"] = last_name
        if user.get("email_verified", user.get("emailVerified", True)):
            payload["emailVerified"] = True
        status, resp = call("POST", f"/admin/realms/{REALM}/users", token, body=payload)
        if status not in (201, 204):
            fatal(f"create {username} failed ({status}): {resp}")
        created = True
        changed = True
        status, found = call(
            "GET",
            f"/admin/realms/{REALM}/users?username={urllib.parse.quote(username)}&exact=true",
            token,
        )
    if not found:
        fatal(f"{username} not found after create")
    uid = found[0]["id"]

    if created and user.get("password"):
        temporary = user.get("temporary", user.get("temporary_password", True))
        status, resp = call(
            "PUT",
            f"/admin/realms/{REALM}/users/{uid}/reset-password",
            token,
            body={"type": "password", "value": user["password"], "temporary": temporary},
        )
        if status != 204:
            fatal(f"set password for {username} failed ({status}): {resp}")

    status, current = call("GET", f"/admin/realms/{REALM}/users/{uid}/role-mappings/realm", token)
    have_roles = {r["name"] for r in (current or [])}
    for role_name in user.get("realm_roles", []):
        if role_name in have_roles:
            continue
        role = find_realm_role(role_name)
        if not role:
            fatal(f"realm role '{role_name}' not found")
        status, resp = call(
            "POST",
            f"/admin/realms/{REALM}/users/{uid}/role-mappings/realm",
            token,
            body=[{"id": role["id"], "name": role["name"]}],
        )
        if status != 204:
            fatal(f"add role {role_name} to {username} failed ({status}): {resp}")
        changed = True

    status, current = call("GET", f"/admin/realms/{REALM}/users/{uid}/groups", token)
    have_groups = {g["path"] for g in (current or [])}
    for group_path in user.get("groups", []):
        if group_path in have_groups:
            continue
        gid = find_group(group_path)
        if not gid:
            fatal(f"group '{group_path}' not found")
        status, resp = call(
            "PUT", f"/admin/realms/{REALM}/users/{uid}/groups/{gid}", token
        )
        if status != 204:
            fatal(f"add {username} to {group_path} failed ({status}): {resp}")
        changed = True

print("CHANGED" if changed else "OK")
