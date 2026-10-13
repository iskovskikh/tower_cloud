#!/usr/bin/env python3
"""Reconcile Keycloak realm users via the Admin REST API (idempotent).

Configured entirely through environment variables (set by the Ansible role):
  KC_URL, KC_ADMIN_REALM, KC_REALM, KC_ADMIN_USER, KC_ADMIN_PASS,
  KC_INSECURE ("true"/"false"), KC_USERS (JSON list of user dicts).

Existing users are never modified (passwords are not reset); missing users are
created with a temporary password, and realm-role / group membership is ensured.
Prints CHANGED or OK for Ansible's changed_when.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

KC_URL = os.environ["KC_URL"].rstrip("/")
ADMIN_REALM = os.environ.get("KC_ADMIN_REALM", "master")
REALM = os.environ["KC_REALM"]
ADMIN_USER = os.environ["KC_ADMIN_USER"]
ADMIN_PASS = os.environ["KC_ADMIN_PASS"]
INSECURE = os.environ.get("KC_INSECURE", "true").lower() == "true"
USERS = json.loads(os.environ["KC_USERS"])

CTX = ssl.create_default_context()
if INSECURE:
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE


def call(method, path, token=None, body=None, form=None):
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


def fatal(msg):
    print(f"ERROR: {msg}")
    sys.exit(1)


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
    status, role = call("GET", f"/admin/realms/{REALM}/roles/{name}", token)
    return role if status == 200 else None


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
        for src, dst in (("email", "email"), ("firstName", "firstName"), ("lastName", "lastName")):
            if user.get(src):
                payload[dst] = user[src]
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
