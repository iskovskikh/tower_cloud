# Identity / SSO (`tower_cloud`)

Central single sign-on for cluster services, run entirely in Kubernetes (Flux)
and backed by `tower` storage.

- **Keycloak 26.8.1** — IdP. `https://auth.metoda.cc` (ns `keycloak`) — the
  public issuer host (`KC_HOSTNAME`). **Split-horizon DNS** resolves it to
  ingress-nginx inside and to `adminvps` outside, so the OIDC issuer is identical
  everywhere (required for the public host). `keycloak.k8s.lan` still routes to
  the same Ingress but Keycloak redirects to the canonical name.
- **Postgres 18.6** — Keycloak database, on a dedicated tower iSCSI LUN
  (`postgres`, lun 3, 20 GiB), static PV `tower-iscsi-postgres` (ns `keycloak`).
- **oauth2-proxy 7.15.5** — forward-auth for ingress-nginx,
  `https://auth.k8s.lan` (ns `oauth2-proxy`).
- **Vault** — logs in via Keycloak OIDC (`auth/oidc`, role `tower-user`).
- **OpenCloud** — file sync/share/collab (`https://cloud.metoda.cc`) federated to
  Keycloak as an **external OIDC IdP**: its `idp` service is disabled, the built-in
  IDM/LDAP stays and stores autoprovisioned users. See below.
- **Secrets** — never in git: generated in Vault at `secret/keycloak`, rendered
  by ESO into `keycloak/keycloak-secrets` and
  `oauth2-proxy/oauth2-proxy-secrets`.

Internal names live under the wildcard DNS `*.k8s.lan` (router dnsmasq) and use
TLS from the internal CA (`cert-manager` ClusterIssuer `tower-ca`). The Keycloak
issuer also carries the public name `auth.metoda.cc` in the Ingress; internally
the router dnsmasq answers it with the ingress-nginx node IPs (split-horizon), so
in-cluster clients (oauth2-proxy, Vault) reach the issuer directly.

## Layout (GitOps)

```
k8s/apps/db/postgres/          # Postgres StatefulSet + PV/PVC + Service
k8s/apps/keycloak/             # Namespace, ExternalSecret, realm ConfigMap,
                               # Deployment, Service, Ingress
k8s/apps/oauth2-proxy/         # Namespace, ExternalSecret, Deployment, Service,
                               # Ingress (auth.k8s.lan) + demo whoami app
ansible/roles/vault_config/    # seeds secret/keycloak; configures Vault OIDC
ansible/roles/keycloak_config/ # reconciles realm roles/scopes/clients/users
```

## Admin access

Admin console: `https://auth.metoda.cc/admin` → realm `tower` (or `master`).
The bootstrap admin username/password are in Vault:

```bash
vault kv get -field=admin_username secret/keycloak
vault kv get -field=admin_password secret/keycloak
```

A non-admin test user exists in realm `tower` (`demo_user` /
`demo_user_password` in Vault) — used by the SSO demo.

## Realm-as-code (roles, clients, users)

Realm **roles**, **client scopes**, **OIDC clients** and **users** are reconciled
idempotently by the Ansible role `keycloak_config` (`playbooks/keycloak.yml`)
via the Keycloak Admin REST API. Declare them in
`ansible/roles/keycloak_config/defaults/main.yml` (`keycloak_realm_roles`,
`keycloak_client_scopes`, `keycloak_clients`, `keycloak_users`), store passwords
in Vault (`secret/keycloak`, seeded by `vault_config`), and run:

```bash
cd ansible && ansible-playbook playbooks/keycloak.yml
```

Roles/scopes/clients are upserted (only the managed fields are compared, so
Keycloak-generated ids/secrets are left alone). A user password is only set when
the user is **first created** (with `temporary: true`, so Keycloak forces a
change on first login) — a password the user later chooses is never overwritten.
Currently declared: `aiskovskikh` (roles `user` + `opencloudAdmin`),
`niskovskikh` and `viskovskikh` (role `opencloudUser`, group `/users`, mirroring
the hand-created user `diskovskikh`).

## Protect a service with SSO (forward-auth)

Add three annotations to the service's Ingress:

```yaml
nginx.ingress.kubernetes.io/auth-url: "http://oauth2-proxy.oauth2-proxy.svc.cluster.local:4180/oauth2/auth"
nginx.ingress.kubernetes.io/auth-signin: "https://auth.k8s.lan/oauth2/start?rd=$scheme://$host$request_uri"
nginx.ingress.kubernetes.io/auth-response-headers: "x-auth-request-user, x-auth-request-email"
```

Unauthenticated users are redirected to Keycloak; after login the proxy adds
`X-Auth-Request-User` / `X-Auth-Request-Email` headers to the upstream request.
Example: `k8s/apps/oauth2-proxy/demo-ingress.yaml` (`whoami.k8s.lan`).

## Add an OIDC client (for an app that speaks OIDC)

Add it to `keycloak_clients` in
`ansible/roles/keycloak_config/defaults/main.yml` and run the role (see
[Realm-as-code](#realm-as-code-roles-clients-users)). For a confidential client
that needs a secret, add the secret to Vault (`secret/keycloak`) + the
`keycloak/externalsecret.yaml` mapping and keep it out of the defaults (see the
`vault` / `oauth2-proxy` clients for the pattern).

The realm is also bootstrapped from `k8s/apps/keycloak/configmap-realm.yaml`, but
`--import-realm` **only imports realms that do not exist yet** — keep the two in
sync for fresh installs. In that JSON, client secrets are injected from Vault via
`${VAR}` placeholders (Keycloak substitutes them from the container env at import
time — note: `${VAR}`, **not** `${env.VAR}`).

## OpenCloud SSO (external OIDC IdP)

OpenCloud (`https://cloud.metoda.cc`, ns `opencloud`) uses Keycloak as an
**external OIDC identity provider** instead of its own built-in IDP. In
`k8s/apps/opencloud/deployment.yaml`:

- `OC_EXCLUDE_RUN_SERVICES=idp` — the built-in OIDC provider is not started; the
  built-in **IDM/LDAP is kept** and stores the autoprovisioned users (the
  "smaller setup" option from the OpenCloud docs — no separate LDAP).
- `OC_OIDC_ISSUER=https://auth.metoda.cc/realms/tower`,
  `PROXY_OIDC_REWRITE_WELLKNOWN=true`,
  `PROXY_OIDC_ACCESS_TOKEN_VERIFY_METHOD=jwt`.
- `PROXY_AUTOPROVISION_ACCOUNTS=true` with `PROXY_USER_OIDC_CLAIM` /
  `PROXY_AUTOPROVISION_CLAIM_USERNAME=preferred_username` — a Keycloak user is
  created in OpenCloud on first login, using the Keycloak username.
- `PROXY_ROLE_ASSIGNMENT_DRIVER=oidc` + `PROXY_ROLE_ASSIGNMENT_OIDC_CLAIM=roles`
  — the OpenCloud role comes from the flat `roles` claim, with
  `OC_ADMIN_USER_ID=""` and `GRAPH_ASSIGN_DEFAULT_USER_ROLE=false`
  (role-based admin).

Keycloak side (realm `tower`): realm roles `opencloudAdmin` /
`opencloudSpaceAdmin` / `opencloudUser` / `opencloudGuest`; four **public PKCE**
clients `web` / `OpenCloudDesktop` / `OpenCloudAndroid` / `OpenCloudIOS` (client
IDs/scopes must match OpenCloud's WebFinger defaults — see the deployment); and
the `opencloud-roles` client scope — an `oidc-usermodel-realm-role-mapper` that
emits a **flat, multi-valued `roles`** claim, because the built-in Keycloak
`roles` scope emits them under the nested `realm_access.roles` claim that
OpenCloud does not read.

Note: with the `oidc` role driver, a user whose `roles` claim matches none of the
mappings cannot log in — make sure each OpenCloud user holds one of the
`opencloud*` realm roles.

## Caveats

- Only **one** Keycloak/Postgres replica (no HA); the tower LUN is RWO.
- `oauth2-proxy` skips TLS verification to Keycloak
  (`--ssl-insecure-skip-verify`) because the internal CA isn't mounted there.
- Realm changes are not yet fully declarative (see above).
