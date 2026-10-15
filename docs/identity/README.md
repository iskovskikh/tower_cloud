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
ansible/roles/keycloak_config/ # reconciles realm users (Admin REST API)
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

## Manage users (realm-as-code)

Users are reconciled idempotently by the Ansible role `keycloak_config`
(`playbooks/keycloak.yml`) via the Keycloak Admin REST API. Declare them in
`ansible/roles/keycloak_config/defaults/main.yml` (`keycloak_users`), store the
password in Vault (`secret/keycloak`, seeded by `vault_config`), and run:

```bash
cd ansible && ansible-playbook playbooks/keycloak.yml
```

The password is only set when the user is **first created** (with
`temporary: true`, so Keycloak forces a change on first login) — a password the
user later chooses is never overwritten. Currently declared: `aiskovskikh`
(role `user`, group `/users`).

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

The realm is bootstrapped from `k8s/apps/keycloak/configmap-realm.yaml`.
`--import-realm` **only imports realms that do not exist yet**, so changes to the
JSON do **not** re-apply on an existing realm. Today: create/adjust clients in
the admin console (or re-import by deleting the realm — dev only). Realm-as-code
(operator / Terraform provider) is a follow-up.

Client secrets are injected from Vault with `${VAR}` placeholders in the realm
JSON (Keycloak substitutes them from the container env at import time — note:
`${VAR}`, **not** `${env.VAR}`).

## Caveats

- Only **one** Keycloak/Postgres replica (no HA); the tower LUN is RWO.
- `oauth2-proxy` skips TLS verification to Keycloak
  (`--ssl-insecure-skip-verify`) because the internal CA isn't mounted there.
- Realm changes are not yet fully declarative (see above).
